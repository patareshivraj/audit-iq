"""
server.py — Flask application serving the Audit Intelligence Platform.

Security improvements:
  - Input validation on all endpoints
  - Rate-limiting via simple in-process counter (no external dependency)
  - Secure headers on every response
  - XSS-safe JSON API (data never rendered as raw HTML by server)
  - Environment check at startup

Routes:
    GET  /                    → Web UI
    POST /api/analyze         → Full pipeline (SSE streaming)
    POST /api/ask             → Follow-up Q&A
    GET  /api/history         → Past analyses list
    GET  /api/analysis/<id>   → Specific past result (JSON)
    GET  /api/stats           → Dashboard statistics
    GET  /api/health          → Health-check
"""

import os
import sys
import json
import time
import logging
import threading
from collections import defaultdict, deque

from flask import Flask, render_template, request, Response, jsonify, stream_with_context

import config

# ── Ensure data directory exists before logging ───────────────────────────────
config.DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(config.LOG_PATH), mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["JSON_SORT_KEYS"] = False


# ── Secure response headers middleware ───────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]            = "DENY"
    response.headers["X-XSS-Protection"]           = "1; mode=block"
    response.headers["Referrer-Policy"]            = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none';"
    )
    return response


# ── Simple in-process rate limiter ───────────────────────────────────────────
_rate_store: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _is_rate_limited(ip: str, max_calls: int, window_seconds: int) -> bool:
    """Sliding-window rate limiter. Returns True if the IP should be blocked."""
    now = time.monotonic()
    with _rate_lock:
        q = _rate_store[ip]
        # Remove timestamps outside the window
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= max_calls:
            return True
        q.append(now)
        return False


# ── Pipeline singleton ────────────────────────────────────────────────────────
_pipeline = None
_pipeline_lock = threading.Lock()


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:   # double-checked
                if not config.GROQ_API_KEY:
                    raise EnvironmentError(
                        "GROQ_API_KEY environment variable is not set. "
                        "Set it with: $env:GROQ_API_KEY='gsk_your_key'"
                    )
                from pipeline import AnalysisPipeline
                _pipeline = AnalysisPipeline(groq_api_key=config.GROQ_API_KEY)
    return _pipeline


# ── Input helpers ─────────────────────────────────────────────────────────────
def _get_client_ip() -> str:
    # Accept X-Forwarded-For for proxied deployments
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")


def _json_error(msg: str, code: int) -> tuple:
    return jsonify({"status": "error", "error": msg}), code


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the web UI."""
    return render_template("index.html")


@app.route("/api/health")
def health():
    """Health-check endpoint."""
    api_key_set = bool(config.GROQ_API_KEY)
    return jsonify({
        "status":     "ok" if api_key_set else "degraded",
        "api_key_set": api_key_set,
        "version":    "2.0.0",
    })


@app.route("/api/stats")
def get_stats():
    """Return aggregate statistics for the dashboard."""
    try:
        pipeline = get_pipeline()
        return jsonify({"status": "success", "data": pipeline.get_stats()})
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        logger.exception("Stats fetch failed")
        return _json_error(str(exc), 500)


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """
    Run full pipeline with SSE streaming.

    Request:  {"company": "TCS"}
    Response: text/event-stream of progress + result events
    """
    ip = _get_client_ip()
    if _is_rate_limited(ip, max_calls=5, window_seconds=60):
        return _json_error("Rate limit exceeded. Please wait a minute before trying again.", 429)

    data    = request.get_json(silent=True) or {}
    company = str(data.get("company", "")).strip()

    if not company:
        return _json_error("Company name is required", 400)
    if len(company) > config.MAX_COMPANY_NAME_LEN:
        return _json_error(
            f"Company name too long (max {config.MAX_COMPANY_NAME_LEN} chars)", 400
        )

    try:
        pipeline = get_pipeline()
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)

    def generate():
        try:
            for event in pipeline.analyze_stream(company):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Pipeline error for '%s'", company)
            error_event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error_event)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/ask", methods=["POST"])
def ask_question():
    """
    Ask a follow-up question about an analysed document.

    Request:  {"session_id": "abc123...", "question": "What was the revenue?"}
    """
    ip = _get_client_ip()
    if _is_rate_limited(ip, max_calls=20, window_seconds=60):
        return _json_error("Rate limit exceeded.", 429)

    data       = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "")).strip()
    question   = str(data.get("question", "")).strip()

    if not session_id:
        return _json_error("session_id is required", 400)
    if not question:
        return _json_error("question is required", 400)
    if len(question) > config.MAX_QUESTION_LEN:
        return _json_error(
            f"Question too long (max {config.MAX_QUESTION_LEN} characters)", 400
        )

    # Validate session_id is a hex string (our SHA-256 truncated IDs)
    if not all(c in "0123456789abcdef" for c in session_id) or len(session_id) > 64:
        return _json_error("Invalid session_id", 400)

    try:
        pipeline = get_pipeline()
        answer   = pipeline.ask_question(session_id, question)
        return jsonify({"status": "success", "question": question, "answer": answer})
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        logger.exception("Q&A failed")
        return _json_error(f"Failed to generate answer: {exc}", 500)


@app.route("/api/history", methods=["GET"])
def get_history():
    """Get recent analysis history."""
    try:
        limit    = min(int(request.args.get("limit", 20)), 100)
        pipeline = get_pipeline()
        history  = pipeline.get_history(limit=limit)
        return jsonify({"status": "success", "history": history})
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        logger.exception("History fetch failed")
        return _json_error(str(exc), 500)


@app.route("/api/analysis/<int:analysis_id>", methods=["GET"])
def get_analysis(analysis_id: int):
    """Get a specific past analysis result."""
    try:
        pipeline = get_pipeline()
        result   = pipeline.get_analysis(analysis_id)
        if not result:
            return _json_error("Analysis not found", 404)
        return jsonify({"status": "success", "data": result})
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        logger.exception("Analysis fetch failed")
        return _json_error(str(exc), 500)


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    """Delete all analysis history records."""
    try:
        pipeline = get_pipeline()
        deleted  = pipeline.db.clear_all()
        return jsonify({"status": "success", "deleted": deleted})
    except EnvironmentError as exc:
        return _json_error(str(exc), 503)
    except Exception as exc:
        logger.exception("Clear history failed")
        return _json_error(str(exc), 500)


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return _json_error("Endpoint not found", 404)


@app.errorhandler(405)
def method_not_allowed(e):
    return _json_error("Method not allowed", 405)


@app.errorhandler(500)
def internal_error(e):
    return _json_error("Internal server error", 500)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import socket

    port = config.PORT

    def _port_in_use(p: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("localhost", p)) == 0

    if _port_in_use(port):
        print(f"\n❌ Port {port} is already in use.")
        print(f"   Kill the existing process or set: $env:PORT=8001")
        sys.exit(1)

    if not config.GROQ_API_KEY:
        print("\n⚠️  WARNING: GROQ_API_KEY is not set.")
        print("   Set it with: $env:GROQ_API_KEY='gsk_your_key_here'")
        print("   The server will start, but analyses will fail.\n")

    print(f"""
╔══════════════════════════════════════════════════════════╗
║          🔍 Audit Intelligence Platform v2.0             ║
║          ────────────────────────────────                ║
║          Server : http://127.0.0.1:{port}                 ║
║          API key: {"SET ✅" if config.GROQ_API_KEY else "NOT SET ❌"}                              ║
║          Press Ctrl+C to stop                            ║
╚══════════════════════════════════════════════════════════╝
    """)

    # Flask threaded dev server (SSE requires unbuffered responses)
    # For production: gunicorn --worker-class=gevent --workers=4 server:app
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
