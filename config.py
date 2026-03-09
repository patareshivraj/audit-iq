"""
config.py — Central configuration for the Audit Intelligence Platform.

All environment variables and constants live here so nothing is scattered
across individual modules.
"""

import os
import secrets
from pathlib import Path

# ── Load .env if python-dotenv is available ──────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass  # python-dotenv not installed — rely on shell env vars


# ── Base directories ──────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"
INDEXES_DIR = DATA_DIR / "indexes"
LOG_PATH    = DATA_DIR / "server.log"
DB_PATH     = DATA_DIR / "audit.db"

# Ensure runtime directories exist
for _d in (DATA_DIR, REPORTS_DIR, INDEXES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ── API / LLM ─────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
LLM_MODEL:    str = os.environ.get("LLM_MODEL", "llama-3.1-8b-instant")


# ── Server ────────────────────────────────────────────────────────────────────
PORT:       int = int(os.environ.get("PORT", 8000))
SECRET_KEY: str = os.environ.get("SECRET_KEY", secrets.token_hex(32))


# ── RAG Engine ────────────────────────────────────────────────────────────────
CHUNK_SIZE:        int = 1000   # approximate tokens per chunk
CHUNK_OVERLAP:     int = 150    # overlap tokens between chunks
MAX_SEARCH_RESULTS: int = 8     # results per query
MAX_ENGINES:       int = int(os.environ.get("MAX_ENGINES", 10))  # LRU cap
MAX_CONTEXT_CHARS: int = 15_000  # characters fed to LLM per extraction call


# ── Scraper ───────────────────────────────────────────────────────────────────
SCREENER_BASE_URL: str = "https://www.screener.in"
SCRAPER_TIMEOUT:   int = 20     # seconds, page fetch
DOWNLOAD_TIMEOUT:  int = 60     # seconds, PDF download
MAX_REPORTS:       int = 1      # only fetch the most-recent annual report


# ── Input validation ──────────────────────────────────────────────────────────
MAX_COMPANY_NAME_LEN: int = 120
MAX_QUESTION_LEN:     int = 2000
ALLOWED_COMPANY_CHARS: str = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789 .,&-_()'\"/"
)
