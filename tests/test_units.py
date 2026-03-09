"""
tests/test_units.py — Unit tests for the Audit Intelligence Platform.

Run with:
    pytest tests/ -v

Tests:
    - Chunking algorithm edge cases
    - JSON response parser
    - Database CRUD
    - Input validation (pipeline + server)
    - Rate limiter
    - VectorStore search & persistence (with mocked embeddings)
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Make project root importable ─────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402

# Override data directories to a temp location for tests
_tmp_data = tempfile.mkdtemp()
config.DATA_DIR    = Path(_tmp_data) / "data"
config.INDEXES_DIR = config.DATA_DIR / "indexes"
config.REPORTS_DIR = config.DATA_DIR / "reports"
config.DB_PATH     = config.DATA_DIR / "test.db"
for _d in (config.DATA_DIR, config.INDEXES_DIR, config.REPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Chunking
# ──────────────────────────────────────────────────────────────────────────────
from rag_engine import create_chunks, estimate_tokens


class TestEstimateTokens(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_single_word(self):
        self.assertGreater(estimate_tokens("hello"), 0)

    def test_longer_text(self):
        text = " ".join(["word"] * 100)
        self.assertGreater(estimate_tokens(text), 100)


class TestCreateChunks(unittest.TestCase):
    def _make_pages(self, n_pages: int, words_per_page: int) -> list[str]:
        return [" ".join([f"Word{i}_{j}" for j in range(words_per_page)]) for i in range(n_pages)]

    def test_empty_input(self):
        chunks = create_chunks([])
        self.assertEqual(chunks, [])

    def test_single_short_page(self):
        chunks = create_chunks(["Hello world. This is a test paragraph for the chunker."])
        self.assertGreaterEqual(len(chunks), 1)
        self.assertIn("text", chunks[0])
        self.assertIn("chunk_id", chunks[0])
        self.assertIn("page", chunks[0])

    def test_chunk_ids_are_sequential(self):
        pages  = self._make_pages(3, 200)
        chunks = create_chunks(pages)
        ids = [c["chunk_id"] for c in chunks]
        self.assertEqual(ids, list(range(len(ids))))

    def test_very_short_lines_skipped(self):
        # Lines shorter than 20 chars should be dropped
        pages  = ["Hi\nHello\nThis is a proper paragraph that should definitely be included."]
        chunks = create_chunks(pages)
        full_text = " ".join(c["text"] for c in chunks)
        self.assertIn("proper paragraph", full_text)
        self.assertNotIn("Hi\n", full_text)

    def test_overlap_produces_repeated_tokens(self):
        # With overlap, the last sentence of chunk N should appear in chunk N+1
        long_page = ". ".join([f"Sentence number {i} contains some important content" for i in range(60)])
        chunks = create_chunks([long_page])
        if len(chunks) > 1:
            # Find any text shared between consecutive chunks
            end_of_first = set(chunks[0]["text"].split())
            start_of_second = set(chunks[1]["text"].split())
            overlap = end_of_first & start_of_second
            self.assertGreater(len(overlap), 0, "Expected overlap between consecutive chunks")


# ──────────────────────────────────────────────────────────────────────────────
# 2. JSON Response Parser
# ──────────────────────────────────────────────────────────────────────────────
class TestParseJsonResponse(unittest.TestCase):
    """Test RAGEngine._parse_json_response without loading the real model."""

    def _parser(self):
        from rag_engine import RAGEngine
        # Create instance with mocked model/llm to avoid loading real weights
        with patch("rag_engine.get_embedding_model") as mock_emb, \
             patch("rag_engine.groq.Client"):
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_emb.return_value = mock_model
            engine = RAGEngine.__new__(RAGEngine)
            engine.session_id   = "test"
            engine.persist_dir  = Path(_tmp_data) / "test_session"
            engine.persist_dir.mkdir(exist_ok=True)
        return engine

    def test_plain_json(self):
        engine   = self._parser()
        raw      = '{"key": "value", "number": 42}'
        result   = engine._parse_json_response(raw)
        self.assertEqual(result["key"],    "value")
        self.assertEqual(result["number"], 42)

    def test_json_with_markdown_fence(self):
        engine = self._parser()
        raw    = '```json\n{"opinion": "Unmodified"}\n```'
        result = engine._parse_json_response(raw)
        self.assertEqual(result["opinion"], "Unmodified")

    def test_json_with_unnamed_fence(self):
        engine = self._parser()
        raw    = '```\n{"test": true}\n```'
        result = engine._parse_json_response(raw)
        self.assertTrue(result["test"])

    def test_malformed_json_returns_raw(self):
        engine = self._parser()
        raw    = "This is not JSON at all."
        result = engine._parse_json_response(raw)
        self.assertIn("raw_response", result)
        self.assertTrue(result.get("parse_error"))

    def test_json_embedded_in_prose(self):
        engine = self._parser()
        raw    = 'Here is the result: {"company": "TCS"} and that is all.'
        result = engine._parse_json_response(raw)
        self.assertEqual(result.get("company"), "TCS")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Database
# ──────────────────────────────────────────────────────────────────────────────
class TestDatabase(unittest.TestCase):

    def setUp(self):
        from database import Database
        self.db = Database(db_path=str(config.DB_PATH))

    def test_save_and_retrieve(self):
        row_id = self.db.save_analysis(
            company_name="Test Corp Ltd",
            company_query="Test Corp",
            pdf_path="/data/reports/test.pdf",
            report_title="Annual Report 2025",
            result={"opinion": "Unmodified"},
            status="complete",
        )
        self.assertIsInstance(row_id, int)
        self.assertGreater(row_id, 0)

        row = self.db.get_analysis(row_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["company_name"], "Test Corp Ltd")
        self.assertEqual(row["result"]["opinion"], "Unmodified")

    def test_history_returns_list(self):
        self.db.save_analysis("Alpha Corp", "alpha", "/a.pdf", "Report", {}, "complete")
        history = self.db.get_history(limit=10)
        self.assertIsInstance(history, list)
        self.assertGreater(len(history), 0)

    def test_get_nonexistent_analysis(self):
        result = self.db.get_analysis(99_999_999)
        self.assertIsNone(result)

    def test_get_latest_for_company(self):
        self.db.save_analysis("Latest Corp", "lc", "/l.pdf", "R2024", {"y": 2024}, "complete")
        self.db.save_analysis("Latest Corp", "lc", "/l.pdf", "R2025", {"y": 2025}, "complete")
        row = self.db.get_latest_for_company("Latest Corp")
        self.assertIsNotNone(row)
        self.assertEqual(row["result"]["y"], 2025)

    def test_stats(self):
        stats = self.db.get_stats()
        self.assertIn("total",            stats)
        self.assertIn("complete",         stats)
        self.assertIn("unique_companies", stats)

    def test_get_analysis_returns_none_for_missing(self):
        self.assertIsNone(self.db.get_analysis(-1))


# ──────────────────────────────────────────────────────────────────────────────
# 4. Pipeline input validation
# ──────────────────────────────────────────────────────────────────────────────
class TestPipelineValidation(unittest.TestCase):

    def _make_pipeline(self):
        from pipeline import AnalysisPipeline
        # Patch all heavy dependencies
        with patch("pipeline.ScreenerScraper"), \
             patch("pipeline.Database"):
            p = AnalysisPipeline.__new__(AnalysisPipeline)
            p.groq_api_key = "test_key"
            p._engines     = {}
            from database import Database
            with patch.object(Database, "__init__", return_value=None):
                p.db = MagicMock()
        return p

    def test_empty_company_yields_error(self):
        from pipeline import AnalysisPipeline
        p      = self._make_pipeline()
        events = list(p.analyze_stream(""))
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_too_long_company_yields_error(self):
        p      = self._make_pipeline()
        events = list(p.analyze_stream("A" * 200))
        self.assertTrue(any(e["type"] == "error" for e in events))

    def test_valid_company_does_not_error_on_validation(self):
        """Validation should pass; network errors come later."""
        p = self._make_pipeline()
        # Patch scraper to raise immediately so we don't need network
        p.scraper = MagicMock()
        from scraper import CompanyNotFoundError
        p.scraper.search_company.side_effect = CompanyNotFoundError("not found")
        events = list(p.analyze_stream("Infosys"))
        # Should get searching progress, then error — but NOT a validation error
        types = [e["type"] for e in events]
        self.assertIn("progress", types)  # validation passed, got to scraper
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(errors)

    def test_session_id_is_sha256_hex(self):
        sid = AnalysisPipeline._make_session_id("Reliance Industries")
        self.assertEqual(len(sid), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in sid))

    def test_same_name_gives_same_session_id(self):
        self.assertEqual(
            AnalysisPipeline._make_session_id("TCS"),
            AnalysisPipeline._make_session_id("tcs"),
        )
        self.assertEqual(
            AnalysisPipeline._make_session_id(" TCS "),
            AnalysisPipeline._make_session_id("TCS"),
        )


from pipeline import AnalysisPipeline


# ──────────────────────────────────────────────────────────────────────────────
# 5. Rate limiter
# ──────────────────────────────────────────────────────────────────────────────
class TestRateLimiter(unittest.TestCase):

    def setUp(self):
        # Import fresh so _rate_store is accessible
        import server
        self._is_limited = server._is_rate_limited
        # Clear existing state
        server._rate_store.clear()

    def test_under_limit_allowed(self):
        for _ in range(5):
            self.assertFalse(self._is_limited("1.2.3.4", max_calls=5, window_seconds=60))

    def test_over_limit_blocked(self):
        for _ in range(5):
            self._is_limited("9.9.9.9", max_calls=5, window_seconds=60)
        # 6th call should be blocked
        self.assertTrue(self._is_limited("9.9.9.9", max_calls=5, window_seconds=60))

    def test_different_ips_independent(self):
        for _ in range(5):
            self._is_limited("10.0.0.1", max_calls=5, window_seconds=60)
        # 10.0.0.2 should NOT be blocked
        self.assertFalse(self._is_limited("10.0.0.2", max_calls=5, window_seconds=60))


# ──────────────────────────────────────────────────────────────────────────────
# 6. Flask API routes (using test client)
# ──────────────────────────────────────────────────────────────────────────────
class TestAPIRoutes(unittest.TestCase):

    def setUp(self):
        import server
        # Clear rate-store before each test
        server._rate_store.clear()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def test_health_endpoint(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status",  data)
        self.assertIn("version", data)

    def test_analyze_missing_company(self):
        resp = self.client.post(
            "/api/analyze",
            json={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_analyze_empty_company(self):
        resp = self.client.post(
            "/api/analyze",
            json={"company": ""},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_analyze_too_long_company(self):
        resp = self.client.post(
            "/api/analyze",
            json={"company": "X" * 200},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_ask_missing_fields(self):
        resp = self.client.post(
            "/api/ask",
            json={"session_id": "abc123"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_ask_invalid_session_id(self):
        resp = self.client.post(
            "/api/ask",
            json={"session_id": "../../etc/passwd", "question": "test"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_analysis_not_found(self):
        with patch("server.get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.get_analysis.return_value = None
            resp = self.client.get("/api/analysis/99999")
        self.assertEqual(resp.status_code, 404)

    def test_security_headers_present(self):
        resp = self.client.get("/api/health")
        self.assertIn("X-Content-Type-Options", resp.headers)
        self.assertIn("X-Frame-Options",        resp.headers)
        self.assertIn("Content-Security-Policy", resp.headers)

    def test_404_returns_json(self):
        resp = self.client.get("/api/nonexistent")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.get_json())


# ──────────────────────────────────────────────────────────────────────────────
# 7. VectorStore
# ──────────────────────────────────────────────────────────────────────────────
class TestVectorStore(unittest.TestCase):

    def setUp(self):
        import numpy as np
        from rag_engine import VectorStore
        self.dim   = 8
        self.store = VectorStore(self.dim)
        self.np    = np

    def _make_vecs(self, n: int):
        import numpy as np
        vecs = np.random.randn(n, self.dim).astype("float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / norms

    def test_empty_search_returns_empty(self):
        q   = self._make_vecs(1)
        res = self.store.search(q, top_k=5)
        self.assertEqual(res, [])

    def test_add_and_search(self):
        vecs = self._make_vecs(5)
        meta = [{"text": f"chunk {i}", "document": "test.pdf", "page": i, "chunk_id": i}
                for i in range(5)]
        self.store.add(vecs, meta)
        q   = vecs[2:3]  # query with one of the stored vectors
        res = self.store.search(q, top_k=3)
        self.assertGreater(len(res), 0)
        self.assertIn("text",  res[0])
        self.assertIn("score", res[0])

    def test_save_and_load(self):
        import numpy as np
        from rag_engine import VectorStore
        vecs = self._make_vecs(3)
        meta = [{"text": "hello", "document": "d.pdf", "page": 1, "chunk_id": i}
                for i in range(3)]
        self.store.add(vecs, meta)
        path = str(Path(_tmp_data) / "vs_test" / "store")
        Path(path).parent.mkdir(exist_ok=True)
        self.store.save(path)

        loaded = VectorStore.load(path, self.dim)
        self.assertEqual(loaded.total_vectors, 3)
        self.assertEqual(len(loaded.metadata),  3)

    def test_total_vectors(self):
        self.assertEqual(self.store.total_vectors, 0)
        vecs = self._make_vecs(4)
        meta = [{"text": "t", "document": "d.pdf", "page": 1, "chunk_id": i} for i in range(4)]
        self.store.add(vecs, meta)
        self.assertEqual(self.store.total_vectors, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
