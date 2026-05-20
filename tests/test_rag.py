import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.retriever import ExternalRetrievalScore
from app.rag import RAGService
from app.storage import Storage


class FakeVectorIndex:
    enabled = True

    def __init__(self, scores: dict[int, ExternalRetrievalScore] | None = None) -> None:
        self.scores = scores or {}

    def search(self, query: str, limit: int = 8) -> dict[int, ExternalRetrievalScore]:
        return self.scores


class FailingVectorIndex:
    enabled = True

    def search(self, query: str, limit: int = 8) -> dict[int, ExternalRetrievalScore]:
        raise RuntimeError("qdrant unavailable")


class RAGServiceTest(unittest.TestCase):
    def test_general_chat_uses_llm_chat_layer_without_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            service = RAGService(storage)

            with patch("app.rag.generate_chat_answer", return_value="普通聊天回答") as chat:
                result = service.answer("今天天气多少")

        chat.assert_called_once()
        self.assertEqual(result["answer"], "普通聊天回答")
        self.assertEqual(result["route"]["layer"], "general_chat")
        self.assertEqual(result["route"]["handler"], "llm_chat")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["chatbot_knowledge"], [])
        self.assertEqual(result["retrieval_trace"], [])

    def test_knowledge_route_includes_bm25_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document(
                "会议室预约制度",
                "会议室预约需要提前 1 个工作日提交申请。取消预约应提前 2 小时操作。",
            )
            service = RAGService(storage)

            result = service.answer("会议室预约需要提前多久？")

        self.assertEqual(result["route"]["layer"], "knowledge_evidence")
        self.assertEqual(result["sources"][0]["title"], "会议室预约制度")
        self.assertIn("SQLite FTS5/BM25 召回", result["sources"][0]["evidence"])
        self.assertIn(
            {"name": "bm25", "label": "SQLite FTS5/BM25 全文召回", "matched_sources": 1},
            result["retrieval_trace"],
        )
        self.assertIn(
            {"name": "context", "label": "相邻 chunk 上下文核验", "matched_sources": 1},
            result["retrieval_trace"],
        )

    def test_knowledge_route_merges_qdrant_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            result = storage.add_document(
                "会议室预约制度",
                "会议室预约需要提前 1 个工作日提交申请。",
            )
            chunk_id = storage.list_chunks(int(result["id"]))[0].id
            service = RAGService(
                storage,
                FakeVectorIndex(
                    {chunk_id: ExternalRetrievalScore(1.2, ("Qdrant 向量召回",))}
                ),
            )

            result = service.answer("会议室预约需要提前多久？")

        self.assertIn("Qdrant 向量召回", result["sources"][0]["evidence"])
        self.assertIn(
            {"name": "qdrant", "label": "Qdrant embedding 向量召回", "matched_sources": 1},
            result["retrieval_trace"],
        )

    def test_qdrant_failure_falls_back_to_existing_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document("会议室预约制度", "会议室预约需要提前 1 个工作日提交申请。")
            service = RAGService(storage, FailingVectorIndex())

            result = service.answer("会议室预约需要提前多久？")

        self.assertEqual(result["sources"][0]["title"], "会议室预约制度")
        self.assertNotIn("Qdrant 向量召回", result["sources"][0]["evidence"])

    def test_knowledge_route_filters_sources_by_user_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document("公开制度", "会议室预约需要提前 1 个工作日。")
            storage.add_document("财务制度", "财务奖金名单只允许财务角色查看。", ["finance"])
            service = RAGService(storage)

            public_result = service.answer("财务制度里的奖金名单", user_roles=["employee"])
            finance_result = service.answer("财务制度里的奖金名单", user_roles=["finance"])

        self.assertNotIn("财务制度", [source["title"] for source in public_result["sources"]])
        self.assertEqual(finance_result["sources"][0]["title"], "财务制度")


if __name__ == "__main__":
    unittest.main()
