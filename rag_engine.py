"""
rag_engine.py — RAG engine: PDF ingestion, chunking, embedding, search, LLM generation.

Key improvements over v1:
- Uses central config for all constants and paths
- Embedding model is properly thread-safe (loaded once per process)
- LRU session cache in AnalysisPipeline (not here)
- Proper retry + timeout on LLM calls
"""

import json
import os
import re
import shutil
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import numpy as np
import faiss
import groq
from pypdf import PdfReader

import config
from prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
    QA_SYSTEM_PROMPT,
    QA_USER_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
)

logger = logging.getLogger(__name__)

# ── Module-level constants (from config) ──────────────────────────────────────
DEFAULT_MODEL      = config.LLM_MODEL
CHUNK_SIZE         = config.CHUNK_SIZE
CHUNK_OVERLAP      = config.CHUNK_OVERLAP
MAX_SEARCH_RESULTS = config.MAX_SEARCH_RESULTS
DATA_DIR           = str(config.INDEXES_DIR)


# ── Token estimation (heuristic, avoids tiktoken dependency) ─────────────────
def estimate_tokens(text: str) -> int:
    """~1.33 tokens per word for Llama-family models. Conservative estimate."""
    return int(len(text.split()) * 1.4)   # bumped from 1.33 for safety margin


# ── Embedding model singleton per process ────────────────────────────────────
_embedding_model = None
_embedding_lock  = None


def _get_lock():
    global _embedding_lock
    if _embedding_lock is None:
        import threading
        _embedding_lock = threading.Lock()
    return _embedding_lock


def get_embedding_model():
    """Load embedding model once per process, thread-safe."""
    global _embedding_model
    if _embedding_model is not None:
        return _embedding_model

    with _get_lock():
        if _embedding_model is not None:   # double-checked locking
            return _embedding_model

        from sentence_transformers import SentenceTransformer

        # Prefer locally cached model, fall back to HuggingFace download
        local_path = Path(__file__).parent / "models" / "all-mpnet-base-v2"
        if local_path.is_dir():
            logger.info("Loading embedding model from local path: %s", local_path)
            try:
                _embedding_model = SentenceTransformer(str(local_path), device="cpu")
                return _embedding_model
            except Exception as exc:
                logger.warning("Local model failed (%s), falling back to download", exc)

        logger.info("Downloading embedding model: all-MiniLM-L6-v2")
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
        return _embedding_model


# ── Vector Store ──────────────────────────────────────────────────────────────
class VectorStore:
    """FAISS-backed vector store with chunk metadata side-array."""

    def __init__(self, embedding_dim: int):
        self.index = faiss.IndexFlatIP(embedding_dim)   # inner-product ≡ cosine for normalized vecs
        self.metadata: List[Dict[str, Any]] = []

    def add(self, embeddings: np.ndarray, metadata: List[Dict[str, Any]]):
        if embeddings.shape[0] == 0:
            return
        self.index.add(embeddings.astype("float32"))
        self.metadata.extend(metadata)

    def search(
        self, query_embedding: np.ndarray, top_k: int = MAX_SEARCH_RESULTS
    ) -> List[Dict[str, Any]]:
        if self.index.ntotal == 0:
            return []
        k = min(top_k * 2, self.index.ntotal)
        scores, indices = self.index.search(query_embedding.astype("float32"), k)

        results, seen = [], set()
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1 or idx in seen:
                continue
            seen.add(idx)
            meta = self.metadata[idx].copy()
            meta["score"] = float(score)
            results.append(meta)
            if len(results) >= top_k:
                break

        return sorted(results, key=lambda x: x["score"], reverse=True)

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        faiss.write_index(self.index, path + ".faiss")
        with open(path + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump(self.metadata, fh, ensure_ascii=False)

    @classmethod
    def load(cls, path: str, embedding_dim: int) -> "VectorStore":
        store = cls(embedding_dim)
        faiss_p = path + ".faiss"
        meta_p  = path + ".meta.json"
        if os.path.exists(faiss_p) and os.path.exists(meta_p):
            store.index = faiss.read_index(faiss_p)
            with open(meta_p, encoding="utf-8") as fh:
                store.metadata = json.load(fh)
        return store

    @property
    def total_vectors(self) -> int:
        return self.index.ntotal


# ── Text chunking ─────────────────────────────────────────────────────────────
def create_chunks(
    pages: List[str],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[Dict[str, Any]]:
    """Sliding-window token-aware chunker with sentence-level overflow handling."""
    chunks: List[Dict[str, Any]] = []
    current_lines: List[str] = []
    current_tokens: int = 0
    current_page: int = 1
    overlap_lines: List[str] = []

    def flush(page: int):
        nonlocal current_lines, current_tokens, overlap_lines
        if not current_lines:
            return
        text = "\n\n".join(current_lines)
        chunks.append({
            "text": text,
            "page": page,
            "chunk_id": len(chunks),
            "token_count": estimate_tokens(text),
        })
        # Carry-forward overlap
        overlap_lines, overlap_tokens = [], 0
        for line in reversed(current_lines):
            lt = estimate_tokens(line)
            if overlap_tokens + lt > chunk_overlap:
                break
            overlap_lines.insert(0, line)
            overlap_tokens += lt
        current_lines.clear()
        current_tokens = 0

    for page_num, page_text in enumerate(pages, 1):
        current_page = page_num
        paragraphs = [p.strip() for p in page_text.split("\n") if p.strip()]

        for para in paragraphs:
            if len(para) < 20:
                continue
            n = estimate_tokens(para)

            if n > chunk_size:
                flush(page_num)
                if overlap_lines:
                    current_lines.extend(overlap_lines)
                    current_tokens = sum(estimate_tokens(l) for l in overlap_lines)
                    overlap_lines.clear()
                for sent in re.split(r"(?<=[.!?])\s+", para):
                    if not sent.strip():
                        continue
                    st = estimate_tokens(sent)
                    if current_tokens + st > chunk_size:
                        flush(page_num)
                        if overlap_lines:
                            current_lines.extend(overlap_lines)
                            current_tokens = sum(estimate_tokens(l) for l in overlap_lines)
                            overlap_lines.clear()
                    current_lines.append(sent)
                    current_tokens += st
            elif current_tokens + n > chunk_size:
                flush(page_num)
                if overlap_lines:
                    current_lines.extend(overlap_lines)
                    current_tokens = sum(estimate_tokens(l) for l in overlap_lines)
                    overlap_lines.clear()
                current_lines.append(para)
                current_tokens += n
            else:
                if not current_lines and overlap_lines:
                    current_lines.extend(overlap_lines)
                    current_tokens = sum(estimate_tokens(l) for l in overlap_lines)
                    overlap_lines.clear()
                current_lines.append(para)
                current_tokens += n

    flush(current_page)
    return chunks


# ── RAG Engine ────────────────────────────────────────────────────────────────
class RAGEngine:
    """
    One instance per analysis session (identified by session_id).
    Handles PDF ingestion, FAISS indexing, and LLM generation.
    """

    def __init__(self, groq_api_key: str, session_id: str = "default"):
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY is required")

        self.session_id  = session_id
        self.persist_dir = Path(DATA_DIR) / session_id
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.llm     = groq.Client(api_key=groq_api_key)
        self.embedder = get_embedding_model()
        self.emb_dim  = self.embedder.get_sentence_embedding_dimension()

        store_path = str(self.persist_dir / "store")
        self.vector_store = VectorStore.load(store_path, self.emb_dim)
        self._loaded_files = self._load_registry()

    # ── Registry (tracks which files are already indexed) ────────────────────
    def _registry_path(self) -> Path:
        return self.persist_dir / "loaded_files.txt"

    def _load_registry(self) -> set:
        p = self._registry_path()
        return set(p.read_text(encoding="utf-8").splitlines()) if p.exists() else set()

    def _save_registry(self):
        self._registry_path().write_text("\n".join(self._loaded_files), encoding="utf-8")

    # ── PDF Ingestion ─────────────────────────────────────────────────────────
    def ingest_pdf(self, file_path: str) -> Dict[str, Any]:
        """Parse → chunk → embed → index a PDF."""
        abs_path = os.path.abspath(file_path)

        if abs_path in self._loaded_files:
            return {
                "status": "skipped",
                "reason": "already loaded",
                "file": os.path.basename(file_path),
                "page_count": 0,
                "chunk_count": self.vector_store.total_vectors,
            }

        pages = self._extract_pdf(abs_path)
        if not pages:
            return {
                "status": "error",
                "reason": "no extractable text",
                "file": os.path.basename(file_path),
            }

        chunks = create_chunks(pages)

        texts = [c["text"] for c in chunks]
        embeddings = self.embedder.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=64,
        )

        file_label = os.path.basename(abs_path)
        metadata = [
            {
                "text":     chunk["text"],
                "document": file_label,
                "page":     chunk.get("page", 1),
                "chunk_id": chunk["chunk_id"],
            }
            for chunk in chunks
        ]

        self.vector_store.add(embeddings, metadata)
        self.vector_store.save(str(self.persist_dir / "store"))
        self._loaded_files.add(abs_path)
        self._save_registry()

        logger.info("Ingested %s: %d pages → %d chunks", file_label, len(pages), len(chunks))
        return {
            "status":      "success",
            "file":        file_label,
            "page_count":  len(pages),
            "chunk_count": len(chunks),
        }

    def _extract_pdf(self, file_path: str) -> List[str]:
        """Extract text from each page of a PDF."""
        try:
            reader = PdfReader(file_path)
            return [
                (page.extract_text() or "").strip()
                for page in reader.pages
                if (page.extract_text() or "").strip()
            ]
        except Exception as exc:
            logger.exception("Failed to read PDF: %s", file_path)
            raise RuntimeError(f"Could not read PDF: {exc}") from exc

    # ── Search ────────────────────────────────────────────────────────────────
    def search(self, query: str, top_k: int = MAX_SEARCH_RESULTS) -> List[Dict[str, Any]]:
        """Semantic search over indexed documents."""
        if self.vector_store.total_vectors == 0:
            return []
        q_emb = self.embedder.encode([query], normalize_embeddings=True)
        return self.vector_store.search(q_emb, top_k=top_k)

    # ── LLM Helpers ───────────────────────────────────────────────────────────
    def _call_llm(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        """Call Groq LLM with retry on transient errors."""
        for attempt in range(3):
            try:
                response = self.llm.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[
                        {"role": "system",  "content": system_prompt},
                        {"role": "user",    "content": user_message},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=90,
                )
                return response.choices[0].message.content.strip()
            except groq.RateLimitError:
                if attempt < 2:
                    import time
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except Exception as exc:
                logger.exception("LLM call failed (attempt %d)", attempt + 1)
                if attempt == 2:
                    raise RuntimeError(f"LLM call failed after 3 attempts: {exc}") from exc
                import time
                time.sleep(2)

    def _build_context(self, results: List[Dict[str, Any]], max_chars: int = config.MAX_CONTEXT_CHARS) -> str:
        """Build a labelled context string from search results."""
        parts, total = [], 0
        for r in results:
            snippet = r["text"][:1_500]
            entry   = f"[Source: {r['document']}, Page {r['page']}]\n{snippet}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
        return "\n\n---\n\n".join(parts)

    # ── Extraction ────────────────────────────────────────────────────────────
    def extract_audit_report(self) -> Dict[str, Any]:
        """
        Multi-query audit extraction pipeline.
        7 targeted semantic searches → top-15 deduplicated chunks → LLM → JSON.
        """
        search_queries = [
            "Independent Auditor's Report on Consolidated Financial Statements",
            "Auditor opinion basis qualified unmodified",
            "Key Audit Matters revenue recognition goodwill impairment",
            "Emphasis of matter going concern material uncertainty",
            "Internal financial controls Section 143(3)(i)",
            "CARO Companies Auditor Report Order 2020",
            "Auditor signature partner membership number firm registration UDIN",
        ]

        all_results, seen_chunks = [], set()
        for query in search_queries:
            for r in self.search(query, top_k=5):
                key = (r["document"], r["chunk_id"])
                if key not in seen_chunks:
                    seen_chunks.add(key)
                    all_results.append(r)

        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        top = all_results[:20]   # increased from 15 for better coverage

        if not top:
            return {"error": "No audit-related content found in the document"}

        context  = self._build_context(top, max_chars=18_000)
        user_msg = EXTRACTION_USER_PROMPT.format(context=context)
        raw      = self._call_llm(EXTRACTION_SYSTEM_PROMPT, user_msg, temperature=0.05)
        return self._parse_json_response(raw)

    def get_company_summary(self) -> Dict[str, Any]:
        """Quick company profile extraction."""
        results = self.search("company name revenue profit industry overview", top_k=5)
        if not results:
            return {"error": "No content found"}
        context  = self._build_context(results, max_chars=5_000)
        user_msg = SUMMARY_USER_PROMPT.format(context=context)
        raw      = self._call_llm(SUMMARY_SYSTEM_PROMPT, user_msg, temperature=0.1)
        return self._parse_json_response(raw)

    def ask_question(self, question: str) -> str:
        """RAG-powered Q&A."""
        results = self.search(question, top_k=MAX_SEARCH_RESULTS)
        if not results:
            return (
                "I don't have relevant information in the loaded document. "
                "Please make sure the PDF has been analysed first."
            )
        context  = self._build_context(results)
        user_msg = QA_USER_PROMPT.format(question=question, context=context)
        return self._call_llm(QA_SYSTEM_PROMPT, user_msg, temperature=0.2)

    # ── JSON Parser ───────────────────────────────────────────────────────────
    def _parse_json_response(self, raw: str) -> Dict[str, Any]:
        """Strip markdown fences and parse JSON from LLM response."""
        cleaned = raw.strip()
        # Remove opening fence
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        # Handle json prefix
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try extracting the first JSON object
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse LLM JSON response; returning raw text")
            return {"raw_response": raw, "parse_error": True}

    # ── Utility ───────────────────────────────────────────────────────────────
    def loaded_documents(self) -> List[str]:
        return [os.path.basename(p) for p in self._loaded_files]

    def reset(self):
        """Wipe all data for this session."""
        shutil.rmtree(self.persist_dir, ignore_errors=True)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.vector_store = VectorStore(self.emb_dim)
        self._loaded_files.clear()
