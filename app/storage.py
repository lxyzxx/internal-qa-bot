from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.chunker import chunk_text
from app.retriever import Chunk, ExternalRetrievalScore, tokenize


class Storage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                create table if not exists documents (
                    id integer primary key autoincrement,
                    title text not null,
                    content text not null,
                    visibility_roles text not null default '["public"]',
                    created_at text not null default current_timestamp
                );

                create table if not exists chunks (
                    id integer primary key autoincrement,
                    document_id integer not null references documents(id) on delete cascade,
                    content text not null,
                    position integer not null
                );

                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_id text not null,
                    role text not null,
                    content text not null,
                    created_at text not null default current_timestamp
                );

                create table if not exists feedback (
                    id integer primary key autoincrement,
                    session_id text not null,
                    question text not null,
                    answer text not null,
                    rating text not null,
                    comment text not null default '',
                    created_at text not null default current_timestamp
                );

                create virtual table if not exists chunks_fts using fts5(
                    chunk_id unindexed,
                    document_id unindexed,
                    title,
                    content
                );
                """
            )
            self._ensure_document_visibility_column(db)
            self._rebuild_fts_if_empty(db)

    def _ensure_document_visibility_column(self, db: sqlite3.Connection) -> None:
        columns = {row["name"] for row in db.execute("pragma table_info(documents)").fetchall()}
        if "visibility_roles" not in columns:
            db.execute(
                "alter table documents add column visibility_roles text not null "
                "default '[\"public\"]'"
            )

    def _rebuild_fts_if_empty(self, db: sqlite3.Connection) -> None:
        count = db.execute("select count(*) as total from chunks_fts").fetchone()["total"]
        if count > 0:
            return

        rows = db.execute(
            """
            select c.id, c.document_id, d.title, c.content
            from chunks c
            join documents d on d.id = c.document_id
            order by c.id
            """
        ).fetchall()
        if not rows:
            return

        db.executemany(
            """
            insert into chunks_fts (chunk_id, document_id, title, content)
            values (?, ?, ?, ?)
            """,
            [
                (
                    row["id"],
                    row["document_id"],
                    self._fts_text(row["title"]),
                    self._fts_text(row["content"]),
                )
                for row in rows
            ],
        )

    def _fts_text(self, text: str) -> str:
        return " ".join(tokenize(text))

    def _fts_query(self, query: str) -> str:
        tokens: list[str] = []
        for token in tokenize(query):
            if token not in tokens:
                tokens.append(token)
        return " OR ".join(f'"{token}"' for token in tokens)

    def add_document(
        self,
        title: str,
        content: str,
        visibility_roles: list[str] | None = None,
    ) -> dict[str, Any]:
        chunks = chunk_text(content)
        if not title.strip():
            raise ValueError("title is required")
        if not chunks:
            raise ValueError("content is required")
        roles = self._normalize_roles(visibility_roles)

        with self.connect() as db:
            cursor = db.execute(
                "insert into documents (title, content, visibility_roles) values (?, ?, ?)",
                (title.strip(), content.strip(), json.dumps(roles, ensure_ascii=False)),
            )
            document_id = int(cursor.lastrowid)
            db.executemany(
                "insert into chunks (document_id, content, position) values (?, ?, ?)",
                [(document_id, chunk, index) for index, chunk in enumerate(chunks)],
            )
            rows = db.execute(
                """
                select id, content
                from chunks
                where document_id = ?
                order by position
                """,
                (document_id,),
            ).fetchall()
            db.executemany(
                """
                insert into chunks_fts (chunk_id, document_id, title, content)
                values (?, ?, ?, ?)
                """,
                [
                    (
                        row["id"],
                        document_id,
                        self._fts_text(title.strip()),
                        self._fts_text(row["content"]),
                    )
                    for row in rows
                ],
            )
        return {
            "id": document_id,
            "title": title.strip(),
            "chunk_count": len(chunks),
            "visibility_roles": roles,
        }

    def list_documents(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                select d.id, d.title, d.visibility_roles, d.created_at, count(c.id) as chunk_count
                from documents d
                left join chunks c on c.document_id = d.id
                group by d.id
                order by d.id desc
                """
            ).fetchall()
        return [
            {
                **dict(row),
                "visibility_roles": self._decode_roles(row["visibility_roles"]),
            }
            for row in rows
        ]

    def list_chunks(
        self,
        document_id: int | None = None,
        user_roles: list[str] | None = None,
    ) -> list[Chunk]:
        with self.connect() as db:
            params: tuple[Any, ...] = ()
            where_clause = ""
            if document_id is not None:
                where_clause = "where c.document_id = ?"
                params = (document_id,)
            rows = db.execute(
                f"""
                select c.id, c.document_id, d.title, d.visibility_roles, c.content, c.position
                from chunks c
                join documents d on d.id = c.document_id
                {where_clause}
                order by c.id
                """,
                params,
            ).fetchall()
        visible_rows = [
            row for row in rows if self._roles_visible(row["visibility_roles"], user_roles)
        ]
        return [
            Chunk(
                id=row["id"],
                document_id=row["document_id"],
                title=row["title"],
                content=row["content"],
                position=row["position"],
            )
                for row in visible_rows
        ]

    def delete_document(self, document_id: int) -> None:
        with self.connect() as db:
            db.execute("delete from chunks_fts where document_id = ?", (document_id,))
            db.execute("delete from chunks where document_id = ?", (document_id,))
            db.execute("delete from documents where id = ?", (document_id,))

    def search_chunks_fts(
        self,
        query: str,
        limit: int = 8,
        user_roles: list[str] | None = None,
    ) -> dict[int, ExternalRetrievalScore]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return {}

        with self.connect() as db:
            rows = db.execute(
                """
                select
                    chunk_id,
                    chunks_fts.document_id,
                    d.visibility_roles,
                    bm25(chunks_fts, 1.4, 1.0) as rank
                from chunks_fts
                join documents d on d.id = chunks_fts.document_id
                where chunks_fts match ?
                order by rank
                limit ?
                """,
                (fts_query, limit),
            ).fetchall()

        scores: dict[int, ExternalRetrievalScore] = {}
        for row in rows:
            if not self._roles_visible(row["visibility_roles"], user_roles):
                continue
            rank = abs(float(row["rank"]))
            score = min(3.0, rank * 8.0 + 0.7)
            scores[int(row["chunk_id"])] = ExternalRetrievalScore(
                score=score,
                evidence=("SQLite FTS5/BM25 召回",),
            )
        return scores

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with self.connect() as db:
            db.execute(
                "insert into messages (session_id, role, content) values (?, ?, ?)",
                (session_id, role, content),
            )

    def get_recent_messages(self, session_id: str, limit: int = 8) -> list[dict[str, str]]:
        with self.connect() as db:
            rows = db.execute(
                """
                select role, content
                from messages
                where session_id = ?
                order by id desc
                limit ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def add_feedback(
        self,
        session_id: str,
        question: str,
        answer: str,
        rating: str,
        comment: str = "",
    ) -> dict[str, Any]:
        cleaned_rating = rating.strip().lower()
        if cleaned_rating not in {"up", "down"}:
            raise ValueError("rating must be up or down")
        if not question.strip():
            raise ValueError("question is required")
        if not answer.strip():
            raise ValueError("answer is required")

        with self.connect() as db:
            cursor = db.execute(
                """
                insert into feedback (session_id, question, answer, rating, comment)
                values (?, ?, ?, ?, ?)
                """,
                (
                    session_id.strip(),
                    question.strip(),
                    answer.strip(),
                    cleaned_rating,
                    comment.strip(),
                ),
            )
            feedback_id = int(cursor.lastrowid)
        return {"id": feedback_id, "rating": cleaned_rating}

    def list_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """
                select id, session_id, question, answer, rating, comment, created_at
                from feedback
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def is_empty(self) -> bool:
        with self.connect() as db:
            count = db.execute("select count(*) as total from documents").fetchone()["total"]
        return count == 0

    def _normalize_roles(self, roles: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for role in roles or ["public"]:
            cleaned = role.strip().lower()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized or ["public"]

    def _decode_roles(self, raw_roles: str) -> list[str]:
        try:
            roles = json.loads(raw_roles)
        except json.JSONDecodeError:
            roles = ["public"]
        if not isinstance(roles, list):
            return ["public"]
        return self._normalize_roles([str(role) for role in roles])

    def _roles_visible(self, raw_roles: str, user_roles: list[str] | None) -> bool:
        document_roles = set(self._decode_roles(raw_roles))
        if "public" in document_roles:
            return True
        actor_roles = set(self._normalize_roles(user_roles))
        return bool(document_roles & actor_roles)
