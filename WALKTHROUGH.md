# 📘 Audit Intelligence Platform — Deep Technical Walkthrough

> **Project**: Audit Intelligence Platform  
> **Location**: `d:\TDTL OFFICE\audit_platform\`  
> **Language**: Python 3.10+  
> **Total Source Lines**: ~1,700 (backend) + ~1,385 (frontend)  
> **Last Verified**: 2026-02-28 — Full pipeline tested against TCS Annual Report FY2025

---

## Table of Contents

1. [Project Origin & Motivation](#1-project-origin--motivation)
2. [System Architecture](#2-system-architecture)
3. [Module-by-Module Deep Dive](#3-module-by-module-deep-dive)
   - 3.1 [server.py — Web Server & API Layer](#31-serverpy--web-server--api-layer)
   - 3.2 [pipeline.py — Orchestration Engine](#32-pipelinepy--orchestration-engine)
   - 3.3 [scraper.py — Screener.in Web Scraper](#33-scraperpy--screenerin-web-scraper)
   - 3.4 [rag_engine.py — RAG (Retrieval-Augmented Generation) Core](#34-rag_enginepy--rag-core)
   - 3.5 [prompts.py — LLM Prompt Engineering](#35-promptspy--llm-prompt-engineering)
   - 3.6 [database.py — SQLite Storage Layer](#36-databasepy--sqlite-storage-layer)
   - 3.7 [templates/index.html — Frontend UI](#37-templatesindexhtml--frontend-ui)
4. [Data Flow — End-to-End Pipeline Trace](#4-data-flow--end-to-end-pipeline-trace)
5. [The RAG Strategy Explained](#5-the-rag-strategy-explained)
6. [Prompt Engineering Design Decisions](#6-prompt-engineering-design-decisions)
7. [Frontend Architecture & SSE](#7-frontend-architecture--sse)
8. [File Storage & Persistence](#8-file-storage--persistence)
9. [Error Handling Philosophy](#9-error-handling-philosophy)
10. [Configuration & Environment Variables](#10-configuration--environment-variables)
11. [API Reference](#11-api-reference)
12. [Verified Test Run](#12-verified-test-run)
13. [Known Limitations](#13-known-limitations)

---

## 1. Project Origin & Motivation

This platform was born from two **separate, disconnected projects**:

| Original Project  | Location                        | What It Did                                                          |
| ----------------- | ------------------------------- | -------------------------------------------------------------------- |
| **screnner(pdf)** | `d:\TDTL OFFICE\screnner(pdf)\` | Scraped Screener.in to download annual report PDFs                   |
| **pdf 1**         | `d:\TDTL OFFICE\pdf 1\pdf\`     | RAG chatbot that ingested PDFs and extracted audit data via Groq LLM |

The problem: these two were **completely manual**. A user had to:

1. Run the scraper separately to download a PDF
2. Manually copy the PDF path
3. Upload it to the chatbot
4. Type the extraction prompt manually
5. Parse the output themselves

The **Audit Intelligence Platform** eliminates all manual steps by fusing both projects into a single automated pipeline accessible through a web UI.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        USER (Browser)                              │
│     ┌──────────────┐     ┌──────────────┐     ┌───────────────┐   │
│     │ Search Input  │     │  Progress UI │     │ Results + Q&A │   │
│     └──────┬───────┘     └──────▲───────┘     └───────▲───────┘   │
│            │ POST /api/analyze  │ SSE Events          │ JSON      │
└────────────┼────────────────────┼─────────────────────┼───────────┘
             │                    │                     │
┌────────────▼────────────────────┼─────────────────────┼───────────┐
│                         server.py (Flask)                         │
│  ┌─────────┐  ┌──────────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ GET /   │  │ /api/analyze │  │ /api/ask │  │ /api/history │   │
│  └─────────┘  └──────┬───────┘  └────┬─────┘  └──────┬───────┘   │
└──────────────────────┼───────────────┼───────────────┼────────────┘
                       │               │               │
┌──────────────────────▼───────────────▼───────────────┼────────────┐
│                      pipeline.py                     │            │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │            │
│  │ Step 1-3:   │→ │ Step 4-5:    │→ │ Step 6-7:  │  │            │
│  │ Scrape &    │  │ Ingest &     │  │ Extract &  │  │            │
│  │ Download    │  │ Embed        │  │ Save       │  │            │
│  └──────┬──────┘  └──────┬───────┘  └─────┬──────┘  │            │
│         │                │                │          │            │
└─────────┼────────────────┼────────────────┼──────────┼────────────┘
          │                │                │          │
  ┌───────▼──────┐  ┌──────▼───────┐  ┌────▼────┐  ┌─▼──────────┐
  │  scraper.py  │  │ rag_engine.py│  │prompts.py│  │database.py │
  │              │  │              │  │          │  │            │
  │ Screener.in  │  │ pypdf        │  │ System   │  │ SQLite     │
  │ API + HTML   │  │ FAISS        │  │ prompts  │  │ analyses   │
  │ PDF download │  │ Sentence-TX  │  │ Schemas  │  │ table      │
  │              │  │ Groq LLM     │  │          │  │            │
  └──────────────┘  └──────────────┘  └──────────┘  └────────────┘
```

### Key Design Principles

1. **Generator-based streaming**: `pipeline.analyze_stream()` is a Python generator. Each step `yield`s a progress event, which Flask streams to the browser via SSE. No threads, no queues — just a simple generator.
2. **Session-based RAG engines**: Each company gets a unique `session_id` (MD5 hash of its name). The FAISS index and embeddings persist on disk, so re-analyzing the same company skips the ingestion step.
3. **Separation of scraping from intelligence**: `scraper.py` knows nothing about AI. `rag_engine.py` knows nothing about Screener.in. `pipeline.py` is the only module that connects them.

---

## 3. Module-by-Module Deep Dive

### 3.1 `server.py` — Web Server & API Layer

**File**: [server.py](file:///d:/TDTL%20OFFICE/audit_platform/server.py) (212 lines)  
**Role**: HTTP entry point. Serves the UI and exposes the REST + SSE API.

#### Startup Sequence

```python
# Line 19-21: Data directory is created FIRST (before logging, which writes to it)
_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(_data_dir, exist_ok=True)

# Line 24-31: Dual logging — stdout + file
logging.basicConfig(
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(_data_dir, "server.log")),
    ],
)
```

#### Pipeline Singleton

```python
# Line 40-53: Lazy-initialized singleton
_pipeline = None

def get_pipeline():
    global _pipeline
    if _pipeline is None:
        api_key = os.environ.get("GROQ_API_KEY", "")
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not set.")
        from pipeline import AnalysisPipeline          # deferred import
        _pipeline = AnalysisPipeline(groq_api_key=api_key)
    return _pipeline
```

**Why deferred import?** The `AnalysisPipeline` constructor loads the embedding model (~500MB). By deferring the import to the first API call, the server starts instantly and only loads the model when actually needed.

#### The SSE Endpoint (`/api/analyze`)

```python
# Line 67-101
@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    company = data.get("company", "").strip()

    def generate():
        for event in pipeline.analyze_stream(company):
            yield f"data: {json.dumps(event)}\n\n"       # SSE format

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**How SSE works here**: The browser opens a `fetch()` request. Flask holds the connection open and writes `data: {...}\n\n` lines as the pipeline progresses. The browser reads them in real-time via `ReadableStream`. Each SSE line is a JSON object with a `type` field (`progress`, `result`, or `error`).

#### Other Endpoints

| Route                    | Method | Purpose                            |
| ------------------------ | ------ | ---------------------------------- |
| `GET /`                  | GET    | Serve `templates/index.html`       |
| `POST /api/ask`          | POST   | Follow-up Q&A on a loaded document |
| `GET /api/history`       | GET    | List past analyses (last 20)       |
| `GET /api/analysis/<id>` | GET    | Fetch a specific past result       |

---

### 3.2 `pipeline.py` — Orchestration Engine

**File**: [pipeline.py](file:///d:/TDTL%20OFFICE/audit_platform/pipeline.py) (241 lines)  
**Role**: The central coordinator that connects scraping → ingestion → extraction.

#### Class: `AnalysisPipeline`

**Constructor** (line 32-38):

- Initializes `ScreenerScraper`, `Database`, and a dict of `RAGEngine` instances keyed by session ID.
- The `_engines` dict acts as an in-memory cache so that follow-up Q&A questions reuse the same loaded FAISS index.

#### Session ID Generation

```python
# Line 49-52
def _make_session_id(self, company_name: str) -> str:
    clean = company_name.strip().lower().replace(" ", "_")
    return hashlib.md5(clean.encode()).hexdigest()[:12]
```

This ensures that "TCS", "tcs", and " TCS " all map to the same session. The 12-character hex digest is short enough for directory names but collision-resistant enough for practical use.

#### The 7-Step Pipeline (`analyze_stream`)

This is the heart of the system (lines 54-220). It's a **Python generator** that yields progress events:

| Step | Stage ID          | What Happens                                                        |
| ---- | ----------------- | ------------------------------------------------------------------- |
| 1    | `searching`       | Calls `scraper.search_company(name)`                                |
| 2    | `finding_reports` | Calls `scraper.get_annual_report_links(path)`                       |
| 3    | `downloading`     | Calls `scraper.download_pdf(url, name, title)`                      |
| 4    | `ingesting`       | Calls `engine.ingest_pdf(pdf_path)` → parse + chunk + embed + index |
| 5    | `summarizing`     | Calls `engine.get_company_summary()` → quick LLM profile            |
| 6    | `extracting`      | Calls `engine.extract_audit_report()` → structured JSON extraction  |
| 7    | (implicit)        | Calls `db.save_analysis(...)` → persist to SQLite                   |

**Error handling**: Every step is wrapped in `try/except`. If any step fails, the generator yields an `error` event and returns — the UI displays the error message and stops. The summary step (5) is **soft-fail**: if it errors, the pipeline continues with a default company name.

#### Follow-Up Q&A

```python
# Line 222-232
def ask_question(self, session_id: str, question: str) -> str:
    engine = self._engines.get(session_id)
    if not engine:
        # Try to reload from disk
        engine = RAGEngine(groq_api_key=self.groq_api_key, session_id=session_id)
        if engine.vector_store.total_vectors == 0:
            return "No document loaded for this session."
        self._engines[session_id] = engine
    return engine.ask_question(question)
```

If the server restarts, the in-memory `_engines` dict is empty. But the FAISS index is persisted to disk, so `RAGEngine` can reload it. This means Q&A survives server restarts.

---

### 3.3 `scraper.py` — Screener.in Web Scraper

**File**: [scraper.py](file:///d:/TDTL%20OFFICE/audit_platform/scraper.py) (214 lines)  
**Role**: Finds and downloads annual report PDFs from Screener.in.

#### Class: `ScreenerScraper`

Uses a persistent `requests.Session` with realistic browser headers (line 20-24).

#### Method: `search_company(query)`

```python
search_url = f"{BASE_URL}/api/company/search/?q={quote(query)}"
```

- Calls Screener's internal search API (JSON response).
- URL-encodes the query using `urllib.parse.quote` to prevent URL injection.
- Returns the first match: `{"path": "/company/TCS/consolidated/", "name": "..."}`.
- Raises `CompanyNotFoundError` if no results.

#### Method: `get_annual_report_links(company_path)`

This is the most complex method. It uses a **3-layer resilience strategy** to extract PDF links:

1. **Primary**: `soup.select_one("section#documents")` → find the documents section.
2. **Secondary**: `docs_section.select_one("div.documents.annual-reports")` → find the annual reports container.
3. **Fallback**: Search for any `<h3>` containing "Annual" and use its parent div.

From the target container, it extracts `<a>` tags with URLs containing `.pdf` or `annualreport`, capped at 5 reports.

**URL normalization** (lines 136-138):

```python
if not url.startswith("http"):
    url = f"https:{url}" if url.startswith("//") else f"{BASE_URL}{url}"
```

Handles protocol-relative (`//cdn.example.com/report.pdf`), relative (`/reports/2024.pdf`), and absolute URLs.

#### Method: `download_pdf(url, company_name, title)`

- Creates a sanitized directory: `data/reports/Tata_Consultancy_Services_Ltd/`.
- **Caching**: Skips download if file exists and is >1KB (line 180).
- **Validation**: Checks `Content-Type` header to reject HTML responses (line 194). Rejects files <5KB as invalid.
- Uses chunked streaming (`iter_content(chunk_size=16384)`) for memory efficiency.
- Random delays (`time.sleep(random.uniform(0.5, 1.5))`) between requests to be respectful to the server.

---

### 3.4 `rag_engine.py` — RAG Core

**File**: [rag_engine.py](file:///d:/TDTL%20OFFICE/audit_platform/rag_engine.py) (486 lines)  
**Role**: The AI brain — PDF parsing, chunking, embedding, vector search, and LLM generation.

This is the largest and most complex module.

#### Token Estimation (line 48-50)

```python
def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.33)
```

Uses a heuristic: ~1.33 tokens per word for Llama-family models. This avoids depending on `tiktoken` (which is GPT-specific) or downloading a separate tokenizer.

#### Embedding Model Singleton (line 56-79)

```python
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    # ... tries local path first, then downloads all-MiniLM-L6-v2
```

- **Singleton pattern**: The model (~100MB in memory) is loaded once per process.
- **Local-first strategy**: Checks `./models/all-mpnet-base-v2/` before downloading (useful if you want to bundle a model).
- **Fallback**: Downloads `all-MiniLM-L6-v2` from HuggingFace (384-dimensional embeddings).

#### Class: `VectorStore` (lines 85-137)

A thin wrapper around FAISS with metadata management:

```python
class VectorStore:
    def __init__(self, embedding_dim):
        self.index = faiss.IndexFlatIP(embedding_dim)  # Inner Product = cosine similarity
        self.metadata = []                              # parallel array of chunk metadata
```

- **`IndexFlatIP`**: Brute-force inner product search. With normalized embeddings (which `sentence-transformers` produces when `normalize_embeddings=True`), inner product equals cosine similarity.
- **Search with deduplication** (line 98-116): Fetches `top_k * 2` results, then deduplicates by index (in case FAISS returns the same vector twice due to floating-point rounding).
- **Persistence**: `save()` writes two files: `store.faiss` (the FAISS index binary) and `store.meta.json` (chunk text + page numbers).

#### Chunking Algorithm: `create_chunks()` (lines 143-223)

This is a **sliding-window chunker with token-aware boundaries**:

1. Splits each PDF page into paragraphs (by `\n`).
2. Skips fragments shorter than 20 characters.
3. Accumulates paragraphs until the chunk reaches `CHUNK_SIZE` (~1000 tokens).
4. When overflowing, "flushes" the current chunk and carries forward the last `CHUNK_OVERLAP` (~150 tokens) worth of lines into the next chunk.
5. For oversized paragraphs (>1000 tokens), splits on sentence boundaries (`[.!?]`).

**Why overlap matters**: Without overlap, a key audit sentence like "We identified a material uncertainty related to going concern" could be split across two chunks, making neither chunk semantically complete. The 150-token overlap ensures at least 2-3 sentences bridge each boundary.

#### Class: `RAGEngine` (lines 229-486)

One instance per analyzed company (identified by `session_id`).

**Constructor** (line 235-252):

- Initializes the Groq LLM client, loads the embedding model, and either loads an existing FAISS index from disk or creates a new one.
- A file registry (`loaded_files.txt`) tracks which PDFs have been ingested, enabling **incremental indexing** — re-running the pipeline skips already-loaded PDFs.

**`ingest_pdf(file_path)`** (lines 271-335):

1. Checks the file registry → returns `"skipped"` if already loaded.
2. Extracts text from each PDF page using `pypdf.PdfReader`.
3. Chunks the text using `create_chunks()`.
4. Generates embeddings using the sentence-transformer model.
5. Adds embeddings + metadata to the FAISS index.
6. Persists the index to disk and updates the file registry.

**`extract_audit_report()`** (lines 394-434) — The Multi-Query Extraction Strategy:

This is the most important method. Instead of a single search query, it runs **7 targeted queries** to maximize the chance of finding all audit-relevant sections:

```python
search_queries = [
    "Independent Auditor's Report on Consolidated Financial Statements",
    "Auditor opinion basis qualified unmodified",
    "Key Audit Matters revenue recognition",
    "Emphasis of matter going concern material uncertainty",
    "Internal financial controls Section 143",
    "CARO Companies Auditor Report Order",
    "Auditor signature membership number firm registration UDIN",
]
```

Each query returns its top 5 chunks. Results are de-duplicated by `(document, chunk_id)`, sorted by relevance score, and the top 15 are compiled into a context string sent to the LLM.

**Why 7 queries?** Annual reports are 200-400 pages. A single query like "auditor's report" would only find the most semantically similar chunks — likely the opinion paragraph. But the KAMs, signature block, and IFC opinion are written in very different language. Separate queries for each section type ensure comprehensive coverage.

**`ask_question(question)`** (lines 447-456):

- Standard RAG: embed the question → search the index → build context → call LLM with `QA_SYSTEM_PROMPT`.

**`_parse_json_response(raw)`** (lines 458-472):

- Strips markdown code fences (`\`\`\`json ... \`\`\``).
- Attempts `json.loads()`. If parsing fails, returns the raw text with a `parse_error` flag so the UI can still display it.

---

### 3.5 `prompts.py` — LLM Prompt Engineering

**File**: [prompts.py](file:///d:/TDTL%20OFFICE/audit_platform/prompts.py) (187 lines)  
**Role**: All LLM instructions in one place, separated from code logic.

This module contains 6 prompt constants organized into 3 pairs (system + user):

| Pair                            | Purpose                                   | Temperature               |
| ------------------------------- | ----------------------------------------- | ------------------------- |
| `EXTRACTION_SYSTEM/USER_PROMPT` | Structured audit report extraction → JSON | 0.05 (near-deterministic) |
| `QA_SYSTEM/USER_PROMPT`         | Follow-up Q&A in natural language         | 0.2 (slightly creative)   |
| `SUMMARY_SYSTEM/USER_PROMPT`    | Quick company profile → JSON              | 0.1                       |

#### The Extraction Prompt (lines 14-119)

This is the most carefully engineered prompt. Key design decisions:

1. **Persona**: "Senior Chartered Accountant with 20+ years". This biases the model toward authoritative, domain-correct answers.

2. **Negative instructions**: The "STRICTLY IGNORE" list is critical. Without it, the LLM often confuses the Standalone auditor's report with the Consolidated one (two separate reports in the same PDF).

3. **Classification rules**: Explicit definitions of Unmodified/Qualified/Adverse/Disclaimer prevent the model from inventing its own terminology.

4. **Disambiguation**: "Emphasis of Matter ≠ Qualification" — LLMs frequently conflate these. This rule ensures an Unmodified opinion with EOM is correctly classified as Unmodified.

5. **Schema with examples**: The signature block includes examples like `"Deloitte Haskins & Sells LLP"` and `"Rajesh Kumar"` to prevent the model from confusing subsidiary company names with the signing partner's name (a bug observed in the first test run).

6. **Strict JSON output**: "Return ONLY valid JSON. No markdown fencing." — prevents the model from wrapping the response in explanatory text.

---

### 3.6 `database.py` — SQLite Storage Layer

**File**: [database.py](file:///d:/TDTL%20OFFICE/audit_platform/database.py) (105 lines)  
**Role**: Persistent storage for analysis history and cached results.

#### Schema

```sql
CREATE TABLE analyses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name    TEXT NOT NULL,
    company_query   TEXT NOT NULL,       -- original user input
    pdf_path        TEXT,
    report_title    TEXT,
    result_json     TEXT,                -- full extraction JSON
    status          TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL,       -- UTC ISO format
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_company ON analyses(company_name);
CREATE INDEX idx_status  ON analyses(status);
```

- **Parameterized queries** everywhere (lines 58-63) — no SQL injection risk.
- `sqlite3.Row` factory for dict-like row access.
- `get_latest_for_company()` enables future caching: skip the full pipeline if a recent analysis exists.

---

### 3.7 `templates/index.html` — Frontend UI

**File**: [index.html](file:///d:/TDTL%20OFFICE/audit_platform/templates/index.html) (1,385 lines)  
**Role**: Single-page app with embedded CSS and JavaScript.

#### Layout

A CSS Grid with 3 zones:

```
┌──────────── Header (sticky, full width) ────────────┐
├─── Sidebar (300px) ──┬─── Main Content (1fr) ───────┤
│  History list        │  Search → Progress → Results  │
│                      │  → Q&A                        │
└──────────────────────┴───────────────────────────────┘
```

Mobile-responsive: sidebar hides below 768px.

#### Design System (CSS variables, lines 15-44)

```css
--bg-primary: #06060b; /* Near-black background */
--gradient-primary: linear-gradient(135deg, #6366f1, #8b5cf6, #a855f7);
--font: "Inter", sans-serif; /* Google Fonts */
--font-mono: "JetBrains Mono"; /* For code/data display */
```

Visual style: **Dark glassmorphism** with `backdrop-filter: blur(20px)` on cards, subtle `box-shadow` glows, and indigo/violet gradient accents.

#### JavaScript Architecture

Five key functions control the UI:

1. **`startAnalysis()`**: Sends `POST /api/analyze`, reads the response as a `ReadableStream`, parses SSE lines, and dispatches to `handleEvent()`.

2. **`handleEvent(event)`**: Routes events by type — `progress` → `updateProgressStep()`, `result` → `renderResults()`, `error` → `showError()`.

3. **`renderResults(data)`**: Builds the result cards dynamically. Creates distinct card types for opinion badges (color-coded by type), KAM lists (with violet left-border), going concern flags, IFC opinions, and the signature grid.

4. **`askQuestion()`**: Sends `POST /api/ask` with the `session_id` from the last analysis. Displays user/assistant messages in a chat-like interface.

5. **`loadHistory()`**: Fetches `GET /api/history` on page load and after each analysis. Populates the sidebar with clickable past analyses.

---

## 4. Data Flow — End-to-End Pipeline Trace

Here is the exact sequence of events when a user types "Infosys" and clicks Analyze:

```
Browser                    server.py            pipeline.py          scraper.py              rag_engine.py
   │                          │                     │                     │                        │
   │─POST /api/analyze───────▶│                     │                     │                        │
   │  {"company":"Infosys"}   │                     │                     │                        │
   │                          │──get_pipeline()────▶│                     │                        │
   │                          │                     │                     │                        │
   │                          │◀─AnalysisPipeline──│                     │                        │
   │                          │                     │                     │                        │
   │                          │──analyze_stream()──▶│                     │                        │
   │                          │                     │──search_company()──▶│                        │
   │                          │                     │                     │─GET screener.in/api/──▶│
   │◀─SSE: searching─────────│◀─yield progress────│                     │  company/search/?q=..   │
   │                          │                     │◀─{"path":"/company/ │                        │
   │◀─SSE: found──────────────│◀─yield progress────│   INFY/cons..",     │                        │
   │                          │                     │   "name":"Infosys"} │                        │
   │                          │                     │                     │                        │
   │                          │                     │──get_annual_report──▶                        │
   │◀─SSE: finding_reports────│◀─yield progress────│  _links()           │                        │
   │                          │                     │◀─[{title,url}, ...]──                        │
   │◀─SSE: reports_found──────│◀─yield progress────│                     │                        │
   │                          │                     │                     │                        │
   │                          │                     │──download_pdf()────▶│                        │
   │◀─SSE: downloading────────│◀─yield progress────│                     │─GET pdf URL (stream)──▶│
   │                          │                     │◀─file_path──────────│                        │
   │◀─SSE: downloaded─────────│◀─yield progress────│                     │                        │
   │                          │                     │                     │                        │
   │                          │                     │─────────────ingest_pdf()────────────────────▶│
   │◀─SSE: ingesting──────────│◀─yield progress────│                     │      pypdf → chunks    │
   │                          │                     │                     │      → embeddings      │
   │                          │                     │◀────────────{status, page_count, chunk_count}│
   │◀─SSE: ingested───────────│◀─yield progress────│                     │                        │
   │                          │                     │                     │                        │
   │                          │                     │─────────────extract_audit_report()──────────▶│
   │◀─SSE: extracting─────────│◀─yield progress────│                     │  7 search queries      │
   │                          │                     │                     │  → top 15 chunks       │
   │                          │                     │                     │  → Groq LLM call       │
   │                          │                     │◀────────────{audit_report JSON}──────────────│
   │                          │                     │                     │                        │
   │                          │                     │──db.save_analysis()─│                        │
   │                          │                     │                     │                        │
   │◀─SSE: result─────────────│◀─yield result──────│                     │                        │
   │  {audit_report, summary, │                     │                     │                        │
   │   session_id, ...}       │                     │                     │                        │
   │                          │                     │                     │                        │
   │──renderResults()         │                     │                     │                        │
   │  (display cards)         │                     │                     │                        │
```

---

## 5. The RAG Strategy Explained

### Why RAG Instead of Sending the Full PDF?

Annual reports are 200-400 pages (~100K-200K tokens). No LLM can process this in a single context window. RAG solves this by:

1. **Chunking**: Breaking the PDF into ~200 pieces of ~1000 tokens each.
2. **Embedding**: Converting each chunk into a 384-dimensional vector that captures its semantic meaning.
3. **Indexing**: Storing vectors in FAISS for instant similarity search.
4. **Retrieval**: For each query, finding the ~5 most semantically relevant chunks.
5. **Generation**: Sending only those ~15 chunks (15,000 chars) to the LLM — well within context limits.

### Multi-Query vs Single-Query

A typical RAG system uses **one query**. This platform uses **seven** because audit reports have sections written in fundamentally different styles:

| Section           | Language Style                       | Why a Separate Query Helps            |
| ----------------- | ------------------------------------ | ------------------------------------- |
| Opinion paragraph | Formal legal ("In our opinion...")   | Generic audit query would find this   |
| Key Audit Matters | Technical ("Revenue recognition...") | Different vocabulary from opinion     |
| Going Concern     | Alarm-style ("material uncertainty") | Very specific phrase-based            |
| Signature block   | Structured data (names, numbers)     | No semantic similarity to "audit"     |
| CARO              | Legal citation ("Order, 2020")       | Highly specific legislative reference |

Without multi-query, the signature block and CARO sections would almost never be retrieved.

---

## 6. Prompt Engineering Design Decisions

### Temperature Selection

| Task             | Temperature | Reasoning                                                                   |
| ---------------- | ----------- | --------------------------------------------------------------------------- |
| Audit extraction | 0.05        | Near-deterministic. Two runs on the same PDF should produce identical JSON. |
| Company summary  | 0.1         | Slight flexibility for phrasing, but still factual.                         |
| Q&A              | 0.2         | Allow natural language variation in answers.                                |

### Negative Instructions > Positive Instructions

The prompt spends more words on what to **ignore** than what to extract. This is intentional. LLMs tend to be "eager to please" — if you ask for an auditor's report, they'll pull from any section that mentions auditing, including the standalone report, the CARO annexure, or the secretarial audit. The explicit ignore list prevents this.

### Schema as Instruction

The JSON schema in the prompt serves dual purpose:

1. **Structural**: Tells the LLM exactly what fields to return.
2. **Educational**: The field descriptions (e.g., `"type": "<Unmodified|Qualified|Adverse|Disclaimer>"`) teach the model valid values, reducing classification errors.

---

## 7. Frontend Architecture & SSE

### Server-Sent Events (SSE) Flow

SSE is a simpler alternative to WebSockets for server-to-client streaming:

1. Browser sends a normal `fetch()` POST request.
2. Server responds with `Content-Type: text/event-stream`.
3. Server writes lines in the format `data: {json}\n\n`.
4. Browser reads the response body as a `ReadableStream`, parsing each `data:` line.

The frontend uses the low-level `ReadableStream` API (not `EventSource`) because SSE via `EventSource` only supports GET requests, and the analyze endpoint requires POST.

### UI State Machine

```
IDLE → SEARCHING → FOUND → FINDING_REPORTS → REPORTS_FOUND
     → DOWNLOADING → DOWNLOADED → INGESTING → INGESTED
     → SUMMARIZING → EXTRACTING → RESULT
                                   ↓
                              Q&A ENABLED
```

Each state transition is driven by an SSE event. The `updateProgressStep()` function marks completed steps with ✓ and animates the current step with a spinner.

---

## 8. File Storage & Persistence

After running the pipeline, the `data/` directory looks like:

```
data/
├── audit.db                        # SQLite database
├── server.log                      # Application logs
├── reports/
│   └── Tata_Consultancy_Services_Ltd/
│       └── Financial Year 2025.pdf  # Downloaded PDF
└── indexes/
    └── a1b2c3d4e5f6/              # session_id (MD5 hash)
        ├── store.faiss             # FAISS binary index
        ├── store.meta.json         # Chunk metadata (text + page numbers)
        └── loaded_files.txt        # Registry of ingested file paths
```

**Incremental indexing**: If you analyze "TCS" again, the pipeline downloads the PDF (or skips if cached), then `ingest_pdf()` checks `loaded_files.txt` and returns `{"status": "skipped"}`. Only the LLM call runs again, which takes ~10 seconds instead of the full ~60 seconds.

---

## 9. Error Handling Philosophy

The platform follows a **fail-fast, fail-clear** approach:

1. **Custom exceptions**: `CompanyNotFoundError`, `ScraperError`, `PipelineError` — each maps to a user-friendly message.
2. **Per-step error handling in pipeline**: Each of the 7 steps has its own `try/except`. If step 3 (download) fails, the user sees "Download failed: connection timeout" — not a generic 500 error.
3. **Soft-fail for non-critical steps**: The company summary extraction (step 5) is optional. If it fails, the pipeline continues with a default value instead of aborting.
4. **JSON parse fallback**: If the LLM returns malformed JSON, `_parse_json_response()` returns the raw text with a `parse_error` flag rather than crashing.

---

## 10. Configuration & Environment Variables

| Variable       | Required | Default                | Purpose                           |
| -------------- | -------- | ---------------------- | --------------------------------- |
| `GROQ_API_KEY` | ✅ Yes   | —                      | Groq Cloud API key for LLM access |
| `LLM_MODEL`    | No       | `llama-3.1-8b-instant` | Groq model to use                 |
| `PORT`         | No       | `8000`                 | Server port                       |
| `SECRET_KEY`   | No       | Random hex             | Flask session secret              |

---

## 11. API Reference

### `POST /api/analyze`

Runs the full pipeline. Returns an SSE stream.

**Request:**

```json
{ "company": "TCS" }
```

**SSE Events:**

```
data: {"type": "progress", "stage": "searching", "message": "Searching for 'TCS'..."}
data: {"type": "progress", "stage": "found", "message": "Found: Tata Consultancy Services Ltd.", "data": {"company_name": "...", "path": "..."}}
...
data: {"type": "result", "data": {"company": {...}, "audit_report": {...}, "session_id": "a1b2c3d4e5f6"}}
```

### `POST /api/ask`

Ask a follow-up question about a previously analyzed document.

**Request:**

```json
{ "session_id": "a1b2c3d4e5f6", "question": "What was the revenue?" }
```

**Response:**

```json
{
  "status": "success",
  "question": "What was the revenue?",
  "answer": "According to the consolidated..."
}
```

### `GET /api/history`

Returns the 20 most recent analyses.

**Response:**

```json
{
  "status": "success",
  "history": [
    {
      "id": 1,
      "company_name": "Tata Consultancy Services Ltd.",
      "report_title": "Annual Report 2025",
      "status": "complete",
      "created_at": "2026-02-23T..."
    }
  ]
}
```

### `GET /api/analysis/<id>`

Returns a specific past analysis with the full result JSON.

---

## 12. Verified Test Run

The pipeline was tested end-to-end on **2026-02-23**:

| Step      | Input/Output                              | Time     |
| --------- | ----------------------------------------- | -------- |
| Search    | "TCS" → `Tata Consultancy Services Ltd.`  | ~2s      |
| Scrape    | Found annual report link on Screener.in   | ~2s      |
| Download  | Downloaded 337-page PDF (~25 MB)          | ~15s     |
| Ingest    | 337 pages → 213 chunks → FAISS indexed    | ~20s     |
| Extract   | Groq LLM (Llama 3.1 8B) → structured JSON | ~10s     |
| **Total** |                                           | **~50s** |

**Extracted data verified:**

| Field              | Extracted Value                   | ✅  |
| ------------------ | --------------------------------- | --- |
| Company            | Tata Consultancy Services Limited | ✅  |
| Financial Year End | 31 March 2025                     | ✅  |
| Opinion Type       | Unmodified                        | ✅  |
| Going Concern      | No material uncertainty           | ✅  |
| IFC Opinion        | Unmodified                        | ✅  |
| FRN                | 101248W/W-100022                  | ✅  |
| UDIN               | 25105149BMLWYM7865                | ✅  |
| Report Date        | 10 April 2025                     | ✅  |
| Place              | Mumbai                            | ✅  |

---

## 13. Known Limitations

1. **Screener.in dependency**: Screener's HTML structure may change, breaking the scraper. The layered extraction approach mitigates this but cannot fully prevent it.
2. **Single-user concurrency**: The pipeline runs synchronously in the request thread. Two simultaneous analyses would block each other.
3. **Context window limits**: Very unusual report structures (split across non-adjacent pages) may cause the multi-query strategy to miss a section.
4. **LLM hallucination risk**: While the prompt strongly discourages hallucination, some edge cases (very short reports, non-standard formats) may produce inaccurate results.
5. **No authentication**: The API is open. Anyone with network access can use it.

---

_This walkthrough reflects the codebase as of 2026-02-28. Built by unifying the `screnner(pdf)` scraper and `pdf 1` RAG chatbot into a single automated pipeline._
