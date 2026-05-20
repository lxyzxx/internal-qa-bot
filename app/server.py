from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import STATIC_DIR, settings
from app.embedding import EmbeddingConfig
from app.evaluation import evaluation_case_from_dict, run_evaluation
from app.qdrant_index import NullVectorIndex, QdrantConfig, QdrantVectorIndex
from app.rag import RAGService
from app.storage import Storage


storage = Storage(settings.database_path)
vector_index = QdrantVectorIndex(
    QdrantConfig(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.embedding_dimensions,
    ),
    EmbeddingConfig(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    ),
)
rag_service = RAGService(storage, vector_index)


class DocumentRequest(BaseModel):
    title: str
    content: str
    visibility_roles: list[str] = Field(default_factory=lambda: ["public"])


class BatchDocumentsRequest(BaseModel):
    documents: list[DocumentRequest]


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None
    user_roles: list[str] = Field(default_factory=lambda: ["public"])


class EvaluationRequest(BaseModel):
    cases: list[dict[str, Any]] | None = None


def seed_sample_data(
    storage_instance: Storage = storage,
    vector_index_instance: NullVectorIndex | QdrantVectorIndex = vector_index,
) -> None:
    sample_path = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "sample_faq.md"
    if not sample_path.exists():
        return

    documents = storage_instance.list_documents()
    if storage_instance.is_empty():
        result = storage_instance.add_document(
            "示例内部问答 FAQ",
            sample_path.read_text(encoding="utf-8"),
        )
        sync_vector_index(vector_index_instance, storage_instance, int(result["id"]))
        return

    for document in documents:
        if document["title"] == "示例客服 FAQ":
            try:
                vector_index_instance.delete_document(int(document["id"]))
            except Exception:
                pass
            storage_instance.delete_document(int(document["id"]))
            result = storage_instance.add_document(
                "示例内部问答 FAQ",
                sample_path.read_text(encoding="utf-8"),
            )
            sync_vector_index(vector_index_instance, storage_instance, int(result["id"]))
            return


def sync_vector_index(
    vector_index_instance: NullVectorIndex | QdrantVectorIndex,
    storage_instance: Storage,
    document_id: int,
) -> dict[str, Any]:
    if not vector_index_instance.enabled:
        return {"vector_indexed": False, "vector_index_status": "disabled"}

    try:
        vector_index_instance.upsert_chunks(storage_instance.list_chunks(document_id))
    except Exception as exc:
        return {
            "vector_indexed": False,
            "vector_index_status": "failed",
            "vector_index_error": str(exc),
        }
    return {"vector_indexed": True, "vector_index_status": "ok"}


def rebuild_vector_index(
    vector_index_instance: NullVectorIndex | QdrantVectorIndex,
    storage_instance: Storage,
) -> dict[str, Any]:
    documents = storage_instance.list_documents()
    if not vector_index_instance.enabled:
        return {
            "vector_indexed": False,
            "vector_index_status": "disabled",
            "documents_total": len(documents),
            "documents_succeeded": 0,
            "documents_failed": 0,
            "results": [],
        }

    results = []
    succeeded = 0
    failed = 0
    for document in documents:
        document_id = int(document["id"])
        result = sync_vector_index(vector_index_instance, storage_instance, document_id)
        item = {
            "document_id": document_id,
            "title": document["title"],
            **result,
        }
        results.append(item)
        if result["vector_indexed"]:
            succeeded += 1
        else:
            failed += 1

    return {
        "vector_indexed": failed == 0,
        "vector_index_status": "ok" if failed == 0 else "partial_failed",
        "documents_total": len(documents),
        "documents_succeeded": succeeded,
        "documents_failed": failed,
        "results": results,
    }


def add_document_with_vector_sync(
    storage_instance: Storage,
    vector_index_instance: NullVectorIndex | QdrantVectorIndex,
    document: DocumentRequest,
) -> dict[str, Any]:
    result = storage_instance.add_document(
        document.title,
        document.content,
        document.visibility_roles,
    )
    return {
        **result,
        **sync_vector_index(vector_index_instance, storage_instance, int(result["id"])),
    }


def add_documents_batch(
    storage_instance: Storage,
    vector_index_instance: NullVectorIndex | QdrantVectorIndex,
    documents: list[DocumentRequest],
) -> dict[str, Any]:
    if not documents:
        raise ValueError("documents is required")

    results = []
    succeeded = 0
    failed = 0
    for index, document in enumerate(documents):
        try:
            result = add_document_with_vector_sync(
                storage_instance,
                vector_index_instance,
                document,
            )
            results.append({"index": index, "status": "ok", **result})
            succeeded += 1
        except ValueError as exc:
            results.append(
                {
                    "index": index,
                    "status": "failed",
                    "title": document.title,
                    "error": str(exc),
                }
            )
            failed += 1

    return {
        "documents_total": len(documents),
        "documents_succeeded": succeeded,
        "documents_failed": failed,
        "results": results,
    }


def validate_admin_token(
    expected_token: str,
    authorization: str | None,
    x_admin_token: str | None,
) -> None:
    if not expected_token:
        return

    provided_token = x_admin_token or ""
    if authorization and authorization.lower().startswith("bearer "):
        provided_token = authorization[7:].strip()

    if not hmac.compare_digest(provided_token, expected_token):
        raise HTTPException(status_code=401, detail="invalid admin token")


def create_app(
    storage_instance: Storage = storage,
    rag_service_instance: RAGService = rag_service,
    vector_index_instance: NullVectorIndex | QdrantVectorIndex = vector_index,
    admin_api_token: str = settings.admin_api_token,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        seed_sample_data(storage_instance, vector_index_instance)
        yield

    def require_admin(request: Request) -> None:
        validate_admin_token(
            admin_api_token,
            request.headers.get("authorization"),
            request.headers.get("x-admin-token"),
        )

    api = FastAPI(
        title="Internal QA Bot",
        version="0.1.0",
        lifespan=lifespan,
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Admin-Token"],
    )

    @api.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/documents")
    async def list_documents() -> dict[str, list[dict[str, Any]]]:
        return {"documents": storage_instance.list_documents()}

    @api.post("/api/documents", status_code=201)
    async def add_document(payload: DocumentRequest, request: Request) -> dict[str, Any]:
        require_admin(request)
        try:
            return add_document_with_vector_sync(storage_instance, vector_index_instance, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/api/documents/batch", status_code=201)
    async def add_documents_batch_endpoint(
        payload: BatchDocumentsRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_admin(request)
        try:
            return add_documents_batch(
                storage_instance,
                vector_index_instance,
                payload.documents,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/api/vector-index/rebuild")
    async def rebuild_vector_index_endpoint(request: Request) -> dict[str, Any]:
        require_admin(request)
        return rebuild_vector_index(vector_index_instance, storage_instance)

    @api.post("/api/evaluations/run")
    async def run_evaluation_endpoint(
        payload: EvaluationRequest,
        request: Request,
    ) -> dict[str, Any]:
        require_admin(request)
        cases = None
        if payload.cases is not None:
            try:
                cases = [evaluation_case_from_dict(item) for item in payload.cases]
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"invalid evaluation case: {exc}") from exc
        return run_evaluation(rag_service_instance, cases)

    @api.post("/api/chat")
    async def chat(payload: ChatRequest) -> dict[str, Any]:
        try:
            return rag_service_instance.answer(
                payload.question,
                payload.session_id,
                payload.user_roles,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - last-resort API guard
            raise HTTPException(status_code=500, detail=f"internal server error: {exc}") from exc

    api.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return api


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
