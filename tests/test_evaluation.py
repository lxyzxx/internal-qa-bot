import tempfile
import unittest
from pathlib import Path

from app.evaluation import EvaluationCase, run_evaluation
from app.rag import RAGService
from app.storage import Storage


class EvaluationTest(unittest.TestCase):
    def test_run_evaluation_scores_sources_terms_and_answer_presence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            storage.add_document(
                "会议室预约制度",
                "会议室预约需要提前 1 个工作日提交申请。",
            )
            service = RAGService(storage)

            result = run_evaluation(
                service,
                [
                    EvaluationCase(
                        id="meeting_room",
                        question="会议室预约制度要求提前多久？",
                        expected_sources=("会议室预约制度",),
                        expected_answer_terms=("会议室预约",),
                    )
                ],
            )

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["failed"], 0)

    def test_run_evaluation_can_check_no_answer_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "app.db")
            service = RAGService(storage)

            result = run_evaluation(
                service,
                [
                    EvaluationCase(
                        id="missing",
                        question="员工宿舍政策标准是什么？",
                        should_have_answer=False,
                    )
                ],
            )

        self.assertEqual(result["passed"], 1)


if __name__ == "__main__":
    unittest.main()
