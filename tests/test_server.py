import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.rag import RAGService
from app.qdrant_index import NullVectorIndex
from app.server import create_app, validate_admin_token
from app.storage import Storage


class RecordingVectorIndex:
    enabled = True

    def __init__(self) -> None:
        self.upserted_titles: list[str] = []

    def upsert_chunks(self, chunks) -> None:
        self.upserted_titles.extend(chunk.title for chunk in chunks)

    def delete_document(self, document_id: int) -> None:
        return

    def search(self, query: str, limit: int = 8) -> dict:
        return {}


async def request(
    app,
    method: str,
    path: str,
    payload: dict | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    for key, value in (extra_headers or {}).items():
        headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    received = False
    messages = []

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    return int(start["status"]), response_headers, response_body


class ServerTest(unittest.TestCase):
    def make_app(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        return create_app(storage, RAGService(storage, vector_index), vector_index)

    def test_health_endpoint(self) -> None:
        status, _, body = asyncio.run(request(self.make_app(), "GET", "/api/health"))

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_document_endpoints(self) -> None:
        app = self.make_app()
        create_status, _, create_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents",
                {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
            )
        )
        list_status, _, list_body = asyncio.run(request(app, "GET", "/api/documents"))

        self.assertEqual(create_status, 201)
        self.assertEqual(json.loads(create_body)["title"], "会议室预约制度")
        self.assertEqual(list_status, 200)
        self.assertTrue(
            any(doc["title"] == "会议室预约制度" for doc in json.loads(list_body)["documents"])
        )

    def test_document_endpoint_syncs_vector_index(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = RecordingVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index)

        status, _, body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents",
                {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
            )
        )

        self.assertEqual(status, 201)
        self.assertTrue(json.loads(body)["vector_indexed"])
        self.assertEqual(vector_index.upserted_titles, ["会议室预约制度"])

    def test_batch_document_endpoint_imports_documents(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = RecordingVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index)

        status, _, body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents/batch",
                {
                    "documents": [
                        {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
                        {"title": "VPN 申请流程", "content": "VPN 申请需要填写权限申请单。"},
                    ]
                },
            )
        )

        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["documents_total"], 2)
        self.assertEqual(data["documents_succeeded"], 2)
        self.assertEqual(data["documents_failed"], 0)
        self.assertEqual([item["status"] for item in data["results"]], ["ok", "ok"])
        self.assertEqual(vector_index.upserted_titles, ["会议室预约制度", "VPN 申请流程"])

    def test_batch_document_endpoint_reports_per_document_failures(self) -> None:
        app = self.make_app()

        status, _, body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents/batch",
                {
                    "documents": [
                        {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
                        {"title": "", "content": "标题为空会失败。"},
                    ]
                },
            )
        )

        data = json.loads(body)
        self.assertEqual(status, 201)
        self.assertEqual(data["documents_total"], 2)
        self.assertEqual(data["documents_succeeded"], 1)
        self.assertEqual(data["documents_failed"], 1)
        self.assertEqual([item["status"] for item in data["results"]], ["ok", "failed"])
        self.assertEqual(data["results"][1]["error"], "title is required")

    def test_batch_document_endpoint_rejects_empty_batch(self) -> None:
        app = self.make_app()

        status, _, body = asyncio.run(
            request(app, "POST", "/api/documents/batch", {"documents": []})
        )

        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["detail"], "documents is required")

    def test_admin_token_validation_is_disabled_without_config(self) -> None:
        validate_admin_token("", None, None)

    def test_admin_token_accepts_authorization_bearer(self) -> None:
        validate_admin_token("secret-token", "Bearer secret-token", None)

    def test_admin_token_accepts_x_admin_token(self) -> None:
        validate_admin_token("secret-token", None, "secret-token")

    def test_admin_token_rejects_missing_or_invalid_token(self) -> None:
        with self.assertRaises(HTTPException) as context:
            validate_admin_token("secret-token", None, None)

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.detail, "invalid admin token")

    def test_admin_token_protects_document_create_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents",
                {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
            )
        )
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents",
                {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
                {"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 201)
        self.assertEqual(json.loads(ok_body)["title"], "会议室预约制度")

    def test_admin_token_protects_batch_document_create_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents/batch",
                {
                    "documents": [
                        {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"}
                    ]
                },
            )
        )
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents/batch",
                {
                    "documents": [
                        {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"}
                    ]
                },
                {"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 201)
        self.assertEqual(json.loads(ok_body)["documents_succeeded"], 1)

    def test_admin_token_protects_vector_rebuild_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(
            request(app, "POST", "/api/vector-index/rebuild")
        )
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/vector-index/rebuild",
                extra_headers={"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 200)
        self.assertEqual(json.loads(ok_body)["vector_index_status"], "disabled")

    def test_vector_index_rebuild_disabled(self) -> None:
        app = self.make_app()

        status, _, body = asyncio.run(request(app, "POST", "/api/vector-index/rebuild"))

        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "vector_indexed": False,
                "vector_index_status": "disabled",
                "documents_total": 0,
                "documents_succeeded": 0,
                "documents_failed": 0,
                "results": [],
            },
        )

    def test_vector_index_rebuild_syncs_existing_documents(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        storage.add_document("会议室预约制度", "会议室预约需要提前 1 个工作日。")
        storage.add_document("VPN 申请流程", "VPN 申请需要填写权限申请单。")
        vector_index = RecordingVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index)

        status, _, body = asyncio.run(request(app, "POST", "/api/vector-index/rebuild"))

        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["vector_indexed"])
        self.assertEqual(data["vector_index_status"], "ok")
        self.assertEqual(data["documents_total"], 2)
        self.assertEqual(data["documents_succeeded"], 2)
        self.assertEqual(data["documents_failed"], 0)
        self.assertEqual(vector_index.upserted_titles, ["VPN 申请流程", "会议室预约制度"])
        self.assertEqual(
            [item["title"] for item in data["results"]],
            ["VPN 申请流程", "会议室预约制度"],
        )

    def test_evaluation_endpoint_runs_custom_cases(self) -> None:
        app = self.make_app()
        create_status, _, _ = asyncio.run(
            request(
                app,
                "POST",
                "/api/documents",
                {"title": "会议室预约制度", "content": "会议室预约需要提前 1 个工作日。"},
            )
        )
        eval_status, _, eval_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/evaluations/run",
                {
                    "cases": [
                        {
                            "id": "meeting_room",
                            "question": "会议室预约制度要求提前多久？",
                            "expected_sources": ["会议室预约制度"],
                            "expected_answer_terms": ["会议室预约"],
                        }
                    ]
                },
            )
        )

        data = json.loads(eval_body)
        self.assertEqual(create_status, 201)
        self.assertEqual(eval_status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["passed"], 1)

    def test_admin_token_protects_evaluation_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(
            request(app, "POST", "/api/evaluations/run", {"cases": []})
        )
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/evaluations/run",
                {"cases": []},
                {"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 200)
        self.assertEqual(json.loads(ok_body)["total"], 0)

    def test_feedback_endpoints_record_and_list_feedback(self) -> None:
        app = self.make_app()

        create_status, _, create_body = asyncio.run(
            request(
                app,
                "POST",
                "/api/feedback",
                {
                    "session_id": "session-1",
                    "question": "会议室预约制度要求提前多久？",
                    "answer": "需要提前 1 个工作日。",
                    "rating": "up",
                    "comment": "",
                },
            )
        )
        list_status, _, list_body = asyncio.run(request(app, "GET", "/api/feedback"))

        self.assertEqual(create_status, 201)
        self.assertEqual(json.loads(create_body)["rating"], "up")
        self.assertEqual(list_status, 200)
        self.assertEqual(
            json.loads(list_body)["feedback"][0]["question"],
            "会议室预约制度要求提前多久？",
        )

    def test_admin_token_protects_feedback_list_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(request(app, "GET", "/api/feedback"))
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "GET",
                "/api/feedback",
                extra_headers={"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 200)
        self.assertEqual(json.loads(ok_body)["feedback"], [])

    def test_metrics_endpoint_returns_runtime_summary(self) -> None:
        with patch("app.rag.generate_chat_answer", return_value="你好"):
            app = self.make_app()
            asyncio.run(request(app, "POST", "/api/chat", {"question": "你好"}))
            status, _, body = asyncio.run(request(app, "GET", "/api/metrics"))

        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(data["queries"], 1)
        self.assertEqual(data["routes"][0]["route_layer"], "general_chat")

    def test_admin_token_protects_metrics_when_configured(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        storage = Storage(Path(temp_dir.name) / "app.db")
        vector_index = NullVectorIndex()
        app = create_app(storage, RAGService(storage, vector_index), vector_index, "secret-token")

        missing_status, _, missing_body = asyncio.run(request(app, "GET", "/api/metrics"))
        ok_status, _, ok_body = asyncio.run(
            request(
                app,
                "GET",
                "/api/metrics",
                extra_headers={"x-admin-token": "secret-token"},
            )
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(json.loads(missing_body)["detail"], "invalid admin token")
        self.assertEqual(ok_status, 200)
        self.assertEqual(json.loads(ok_body)["queries"], 0)

    def test_chat_endpoint(self) -> None:
        with patch("app.rag.generate_chat_answer", return_value="你好"):
            status, _, body = asyncio.run(
                request(self.make_app(), "POST", "/api/chat", {"question": "你好"})
            )

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["answer"], "你好")
        self.assertEqual(data["route"]["handler"], "llm_chat")


if __name__ == "__main__":
    unittest.main()
