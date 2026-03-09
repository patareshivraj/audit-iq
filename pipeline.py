"""
pipeline.py — Orchestration pipeline: Scraper → RAG Engine → Extraction → DB.

Improvements over v1:
- LRU cache for RAGEngine instances (caps memory growth)
- Input sanitisation before processing
- Cleaner error messages
- Uses central config for all limits
"""

import os
import logging
import hashlib
from collections import OrderedDict

import config
from scraper    import ScreenerScraper, CompanyNotFoundError, ScraperError
from rag_engine import RAGEngine
from database   import Database

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when the analysis pipeline fails."""


class AnalysisPipeline:
    """
    Full automated pipeline:
    Company Name → Screener Search → PDF Download → RAG Ingestion → Audit Extraction → Result
    """

    def __init__(self, groq_api_key: str):
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY is required")
        self.groq_api_key = groq_api_key
        self.scraper      = ScreenerScraper()
        self.db           = Database()
        # LRU cache: session_id → RAGEngine, max size = config.MAX_ENGINES
        self._engines: OrderedDict[str, RAGEngine] = OrderedDict()

    # ── Session management (LRU) ──────────────────────────────────────────────

    def _get_engine(self, session_id: str) -> RAGEngine:
        """Get or create a RAGEngine. Evicts the oldest entry when cache is full."""
        if session_id in self._engines:
            # Move to end (most-recently-used)
            self._engines.move_to_end(session_id)
            return self._engines[session_id]

        # Evict if at capacity
        if len(self._engines) >= config.MAX_ENGINES:
            evicted_id, _ = self._engines.popitem(last=False)
            logger.info("Evicted session %s from engine cache", evicted_id)

        engine = RAGEngine(groq_api_key=self.groq_api_key, session_id=session_id)
        self._engines[session_id] = engine
        return engine

    @staticmethod
    def _make_session_id(company_name: str) -> str:
        """Stable session ID from company name (SHA-256 truncated)."""
        clean = company_name.strip().lower().replace(" ", "_")
        return hashlib.sha256(clean.encode()).hexdigest()[:16]

    # ── Input validation ──────────────────────────────────────────────────────

    @staticmethod
    def _validate_company(name: str) -> str:
        """Clean and validate the company name."""
        name = name.strip()
        if not name:
            raise ValueError("Company name cannot be empty")
        if len(name) > config.MAX_COMPANY_NAME_LEN:
            raise ValueError(
                f"Company name too long (max {config.MAX_COMPANY_NAME_LEN} characters)"
            )
        allowed = set(config.ALLOWED_COMPANY_CHARS)
        invalid = [c for c in name if c not in allowed]
        if invalid:
            raise ValueError(
                f"Company name contains invalid characters: {set(invalid)}"
            )
        return name

    # ── Main streaming pipeline ───────────────────────────────────────────────

    def analyze_stream(self, company_name: str):
        """
        Generator yielding SSE-ready dicts as the pipeline executes.

        Event shapes:
            {"type": "progress", "stage": str, "message": str}
            {"type": "progress", "stage": str, "message": str, "data": dict}
            {"type": "result",   "data": dict}
            {"type": "error",    "message": str}
        """
        # ── Validate input ────────────────────────────────────────────────────
        try:
            company_name = self._validate_company(company_name)
        except ValueError as exc:
            yield {"type": "error", "message": str(exc)}
            return

        # ── Step 1: Search ────────────────────────────────────────────────────
        yield {
            "type":    "progress",
            "stage":   "searching",
            "message": f"Searching for '{company_name}' on Screener.in...",
        }
        try:
            company = self.scraper.search_company(company_name)
        except CompanyNotFoundError:
            yield {
                "type":    "error",
                "message": (
                    f"Company '{company_name}' not found on Screener.in. "
                    "Try a different name or ticker symbol."
                ),
            }
            return
        except ScraperError as exc:
            yield {"type": "error", "message": f"Search failed: {exc}"}
            return

        yield {
            "type":    "progress",
            "stage":   "found",
            "message": f"Found: {company['name']}",
            "data":    {"company_name": company["name"], "path": company["path"]},
        }

        # ── Step 2: Get report links ──────────────────────────────────────────
        yield {
            "type":    "progress",
            "stage":   "finding_reports",
            "message": "Looking for annual report PDFs...",
        }
        try:
            reports = self.scraper.get_annual_report_links(company["path"])
        except ScraperError as exc:
            yield {"type": "error", "message": f"Could not find reports: {exc}"}
            return

        if not reports:
            yield {
                "type":    "error",
                "message": (
                    f"No annual reports found for {company['name']}. "
                    "They may not be publicly listed on Screener."
                ),
            }
            return

        yield {
            "type":    "progress",
            "stage":   "reports_found",
            "message": f"Found {len(reports)} annual report(s)",
            "data":    {"reports": [r["title"] for r in reports]},
        }

        # ── Step 3: Download latest report ────────────────────────────────────
        latest = reports[0]
        yield {
            "type":    "progress",
            "stage":   "downloading",
            "message": f"Downloading: {latest['title']}...",
        }
        try:
            pdf_path = self.scraper.download_pdf(
                url=latest["url"],
                company_name=company["name"],
                title=latest["title"],
            )
        except ScraperError as exc:
            yield {"type": "error", "message": f"Download failed: {exc}"}
            return

        yield {
            "type":    "progress",
            "stage":   "downloaded",
            "message": "Downloaded successfully",
            "data":    {
                "file": os.path.basename(pdf_path),
                "size_mb": round(os.path.getsize(pdf_path) / 1_048_576, 1),
            },
        }

        # ── Step 4: Ingest into RAG engine ────────────────────────────────────
        yield {
            "type":    "progress",
            "stage":   "ingesting",
            "message": "Reading and indexing document...",
        }
        session_id = self._make_session_id(company["name"])
        engine     = self._get_engine(session_id)

        try:
            ingest_result = engine.ingest_pdf(pdf_path)
        except Exception as exc:
            yield {"type": "error", "message": f"PDF analysis failed: {exc}"}
            return

        if ingest_result.get("status") == "error":
            yield {
                "type":    "error",
                "message": f"Could not read PDF: {ingest_result.get('reason')}",
            }
            return

        status_label = (
            "Already indexed — skipping re-ingestion"
            if ingest_result.get("status") == "skipped"
            else f"Processed {ingest_result.get('page_count', '?')} pages → "
                 f"{ingest_result.get('chunk_count', '?')} chunks"
        )
        yield {
            "type":    "progress",
            "stage":   "ingested",
            "message": status_label,
            "data":    ingest_result,
        }

        # ── Step 5: Company summary (soft-fail) ───────────────────────────────
        yield {
            "type":    "progress",
            "stage":   "summarizing",
            "message": "Generating company profile...",
        }
        try:
            summary = engine.get_company_summary()
        except Exception as exc:
            logger.warning("Summary extraction failed (non-fatal): %s", exc)
            summary = {"company_name": company["name"]}

        # ── Step 6: Extract audit report ──────────────────────────────────────
        yield {
            "type":    "progress",
            "stage":   "extracting",
            "message": "Extracting Independent Auditor's Report (15-30 s)...",
        }
        try:
            audit_data = engine.extract_audit_report()
        except Exception as exc:
            yield {"type": "error", "message": f"Audit extraction failed: {exc}"}
            return

        # ── Step 7: Save to DB ────────────────────────────────────────────────
        analysis_id = self.db.save_analysis(
            company_name=company["name"],
            company_query=company_name,
            pdf_path=pdf_path,
            report_title=latest["title"],
            result=audit_data,
            status="complete",
        )

        # ── Done ──────────────────────────────────────────────────────────────
        yield {
            "type": "result",
            "data": {
                "analysis_id":       analysis_id,
                "company":           company,
                "summary":           summary,
                "report_title":      latest["title"],
                "available_reports": [r["title"] for r in reports],
                "ingestion":         ingest_result,
                "audit_report":      audit_data,
                "session_id":        session_id,
            },
        }

    # ── Q&A ───────────────────────────────────────────────────────────────────

    def ask_question(self, session_id: str, question: str) -> str:
        """Ask a follow-up question about the analysed document."""
        if not question or len(question) > config.MAX_QUESTION_LEN:
            return f"Question must be between 1 and {config.MAX_QUESTION_LEN} characters."

        engine = self._engines.get(session_id)
        if not engine:
            # Try to reload from persisted disk index
            engine = RAGEngine(groq_api_key=self.groq_api_key, session_id=session_id)
            if engine.vector_store.total_vectors == 0:
                return "No document loaded for this session. Please analyse a company first."
            self._engines[session_id] = engine

        return engine.ask_question(question)

    # ── History helpers ───────────────────────────────────────────────────────

    def get_history(self, limit: int = 20) -> list:
        return self.db.get_history(limit=limit)

    def get_analysis(self, analysis_id: int) -> dict | None:
        return self.db.get_analysis(analysis_id)

    def get_stats(self) -> dict:
        return self.db.get_stats()
