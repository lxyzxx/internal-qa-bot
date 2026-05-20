import tempfile
import unittest
from pathlib import Path

from app.storage import Storage


class StorageTest(unittest.TestCase):
    def test_search_chunks_fts_returns_bm25_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document(
                "会议室预约制度",
                "会议室预约需要提前 1 个工作日提交申请。取消预约应提前 2 小时操作。",
            )
            storage.add_document("请假制度", "员工请年假需要提前在 OA 系统提交申请。")

            scores = storage.search_chunks_fts("会议室预约需要提前多久")

        self.assertTrue(scores)
        self.assertIn("SQLite FTS5/BM25 召回", next(iter(scores.values())).evidence)

    def test_delete_document_removes_fts_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            result = storage.add_document("会议室预约制度", "会议室预约需要提前 1 个工作日。")

            self.assertTrue(storage.search_chunks_fts("会议室预约"))
            storage.delete_document(int(result["id"]))

            self.assertEqual(storage.search_chunks_fts("会议室预约"), {})

    def test_list_chunks_can_filter_by_document_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            first = storage.add_document("会议室预约制度", "会议室预约需要提前 1 个工作日。")
            storage.add_document("请假制度", "员工请年假需要提前在 OA 系统提交申请。")

            chunks = storage.list_chunks(int(first["id"]))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].title, "会议室预约制度")

    def test_role_filter_hides_private_chunks_and_fts_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document("公开制度", "会议室预约需要提前 1 个工作日。")
            private = storage.add_document(
                "财务制度",
                "财务奖金名单只允许财务角色查看。",
                ["finance"],
            )

            public_chunks = storage.list_chunks(user_roles=["employee"])
            finance_chunks = storage.list_chunks(user_roles=["finance"])
            public_scores = storage.search_chunks_fts("财务奖金", user_roles=["employee"])
            finance_scores = storage.search_chunks_fts("财务奖金", user_roles=["finance"])

        self.assertEqual([chunk.title for chunk in public_chunks], ["公开制度"])
        self.assertIn("财务制度", [chunk.title for chunk in finance_chunks])
        self.assertEqual(public_scores, {})
        self.assertTrue(finance_scores)
        self.assertEqual(private["visibility_roles"], ["finance"])


if __name__ == "__main__":
    unittest.main()
