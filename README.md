# 🔍 Audit Intelligence Platform

The **Audit Intelligence Platform** is an end-to-end, automated AI system designed to extract, analyze, and structure the "Independent Auditor's Report" from Indian corporate annual reports.

By combining web scraping, RAG (Retrieval-Augmented Generation), and LLM-powered data extraction, this platform takes a company name as input and delivers a structured JSON/UI output containing critical audit findings (opinions, KAMs, going concern status, CARO compliance, etc.) without any manual intervention.

---

## ✨ Key Features

- **Automated Document Acquisition**: Automatically searches Screener.in for a company, locates its most recent annual report, and downloads the PDF.
- **Intelligent RAG Engine**: Parses large annual reports (300+ pages), chunks the text, and generates semantic embeddings using local NLP models (Sentence Transformers).
- **Targeted Audit Search**: Executes specialized multi-query vector searches to locate the exact sections pertaining to the Independent Auditor's Report, dodging irrelevant management commentary.
- **Domain-Expert Extraction**: Uses the Groq LLM (e.g., Llama 3) with a highly specialized Chartered Accountant persona prompt to extract and classify structured data according to the Companies Act, 2013, and ICAI Standards on Auditing.
- **Real-Time Web UI**: A beautiful, dark-themed, glassmorphic web interface that streams the pipeline's progress live using Server-Sent Events (SSE).
- **Follow-Up Q&A**: Once a report is analyzed, users can ask custom questions about the document, answered directly from the RAG context.
- **History & Caching**: SQLite database tracks previous analyses for instant retrieval.

---

## 🏗️ Architecture

The platform is designed with clear separation of concerns across multiple modules:

```text
audit_platform/
├── server.py              # Flask web server (SSE streaming, API endpoints)
├── pipeline.py            # Orchestrator binding Scraper → RAG → LLM
├── scraper.py             # Pure web scraping logic for Screener.in
├── rag_engine.py          # Document parsing, FAISS vector store, LLM integration
├── prompts.py             # Specialized LLM instructions for financial auditing
├── database.py            # SQLite wrapper for caching/history
├── requirements.txt       # Python dependencies
├── templates/
│   └── index.html         # Frontend UI
└── data/                  # Auto-generated storage for PDFs, FAISS indexes, DB
```

### 🔁 The Pipeline Flow

1. **Input**: User enters "TCS".
2. **Search**: `scraper.py` queries the Screener API and resolves to "Tata Consultancy Services Ltd".
3. **Scrape & Download**: Links are extracted, and the latest annual report PDF is downloaded to `data/reports/`.
4. **Ingest**: `rag_engine.py` reads the PDF, chunks the text, computes local embeddings, and updates a FAISS vector index.
5. **Analyze**: The engine queries the index for audit keywords, compiles the context, and sends it to Groq API.
6. **Output**: The extracted JSON is saved to the SQLite DB and displayed on the UI.

---

## 🚀 Getting Started

### Prerequisites

- **OS**: Windows (tested), Linux, or macOS.
- **Python**: 3.10 or higher.
- **Groq API Key**: Get a free API key from [Groq Console](https://console.groq.com/keys).

### 1️⃣ Installation

1. Navigate to the platform directory:

   ```powershell
   cd "d:\audit_resport_platform"
   ```

2. (Optional but recommended) Create and activate a virtual environment:

   ```powershell
   python -m venv env
   .\env\Scripts\activate
   ```

3. Install the required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
   _Note: On the first run, the system will automatically download the local embedding model (`all-MiniLM-L6-v2`) via HuggingFace's `sentence-transformers`._

### 2️⃣ Running the Server

1. Set your Groq API key in the environment variables:

   ```powershell
   $env:GROQ_API_KEY = "gsk_your_api_key_here"
   ```

2. Start the Flask server:

   ```powershell
   python server.py
   ```

3. Open your web browser and go to:
   **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 💻 Usage

### 1. Analyzing a Company

- Enter a company name in the search bar (e.g., "Reliance Industries" or "Infosys").
- Click **Analyze**.
- Watch the progress tracker stream updates as it finds, downloads, and processes the report.
- View the resulting Auditor's Opinion, Key Audit Matters (KAMs), and Signature Block on the dashboard.

### 2. Follow-Up Q&A

- After an analysis is complete, scroll down to the "Ask Follow-up Questions" section.
- You can ask questions like _"What were the key risks identified?"_ or _"Was there any emphasis of matter related to taxation?"_.
- The AI will answer based strictly on the contents of the downloaded PDF.

### 3. Viewing the Raw JSON

- Click the **View JSON** button in the Results section to see the raw extracted JSON schema for programmatic use.

---

## 🔧 Extracted Data Schema

The platform aims to extract the following structured information based on Indian Auditing Standards:

- `report_type`: Formally identified report title.
- `company_name`: Verified registered company name.
- `financial_year_end`: Date of the audited financials.
- `auditor_opinion`: Type (`Unmodified`, `Qualified`, `Adverse`, `Disclaimer`) and Summary.
- `basis_for_opinion`: Excerpt of the basis.
- `key_audit_matters`: List of KAM topics, descriptions, and auditor responses.
- `going_concern`: Flag for material uncertainty regarding going concern.
- `internal_financial_controls`: IFC opinion type.
- `caro_compliance`: Flag for Companies (Auditor's Report) Order mentions.
- `signature_block`: Audit firm, Partner name, FRN, Membership No, UDIN, Date, and Place.

---

## 🛠️ Tech Stack

- **Backend Framework**: Python, Flask, Waitress
- **Web Scraping**: Requests, BeautifulSoup4
- **PDF Processing**: PyPDF
- **Embeddings & Vector Store**: Sentence-Transformers, FAISS (CPU)
- **Large Language Model (LLM)**: Groq API (Llama 3 generation models)
- **Database**: SQLite3
- **Frontend**: Vanilla HTML/CSS/JS with Glassmorphism UI and Server-Sent Events (SSE).

---

## ⚠️ Known Limitations & Disclaimers

1. **Screener Scraping**: The app scrapes Screener.in to find publicly available annual reports. Frequent, rapid requests may trigger rate limits or CAPTCHAs by the provider.
2. **Context Limits**: Annual reports are massive. While RAG significantly narrows down the context, highly unusual report structures might occasionally cause the LLM to miss a section.
3. **Waitress unsuitability for SSE**: Server-Sent Events (streaming UI updates) require unbuffered responses. For the best real-time UI experience, the script currently defaults to Flask's threaded dev server. If deploying to true production, use a WSGI server that supports unbuffered streaming (like Gunicorn with gevent) instead of Waitress.

---

_Built for advanced technical auditing and financial intelligence automation._
