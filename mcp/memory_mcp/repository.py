import json
from typing import List, Optional

import psycopg
from psycopg.rows import dict_row
import requests


class MemoryRepository:
    def __init__(self, database_url: str, ollama_url: str, embedding_model: str, dimension: int):
        self.database_url = database_url
        self.ollama_url = ollama_url
        self.embedding_model = embedding_model
        self.dimension = dimension

    def _embed(self, text: str) -> str:
        response = requests.post(
            "%s/api/embed" % self.ollama_url,
            json={"model": self.embedding_model, "input": text, "truncate": True},
            timeout=60,
        )
        response.raise_for_status()
        embedding = response.json()["embeddings"][0]
        if len(embedding) != self.dimension:
            raise ValueError("Unexpected embedding dimension")
        return "[" + ",".join("%.9g" % value for value in embedding) + "]"

    def search(
        self,
        query: str,
        project: Optional[str] = None,
        kinds: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        limit: int = 8,
        verified_only: bool = False,
    ):
        limit = max(1, min(limit, 50))
        query = query.strip()[:2000]
        if not query:
            raise ValueError("query must not be empty")
        vector = self._embed(query)
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT memory_id::text, project_id, project_label, session_id, message_id,
                           kind, title, left(content, 4000) AS content, metadata, occurred_at,
                           importance, confidence, valid_from, valid_until, supersedes::text,
                           1 - (embedding <=> %s::vector) AS semantic_score,
                           ts_rank_cd(search_document, websearch_to_tsquery('simple', %s)) AS lexical_score,
                           (
                               0.60 * (1 - (embedding <=> %s::vector)) +
                               0.20 * (
                                   ts_rank_cd(search_document, websearch_to_tsquery('simple', %s)) /
                                   (1 + ts_rank_cd(search_document, websearch_to_tsquery('simple', %s)))
                               ) +
                               0.08 * similarity(coalesce(title, ''), %s) +
                               0.08 * importance * confidence +
                               0.04 * exp(-greatest(0, extract(epoch FROM (now() - occurred_at))) / 31557600.0)
                           ) AS score
                    FROM memory
                    WHERE deleted_at IS NULL
                      AND (%s::text IS NULL OR project_id = %s OR project_label = %s)
                      AND (%s::text IS NULL OR session_id = %s)
                      AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))
                      AND (%s::boolean = false OR COALESCE((metadata->>'verified')::boolean, false))
                      AND (valid_from IS NULL OR valid_from <= now())
                      AND (valid_until IS NULL OR valid_until > now())
                    ORDER BY score DESC, occurred_at DESC
                    LIMIT %s
                    """,
                    (
                        vector, query, vector, query, query, query,
                        project, project, project,
                        session_id, session_id,
                        kinds, kinds,
                        verified_only,
                        limit,
                    ),
                )
                return [_serialize(row) for row in cursor.fetchall()]

    def get(self, memory_id: str):
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT memory_id::text, project_id, project_label, session_id, message_id,
                           source_type, source_id, kind, title, content, metadata, occurred_at,
                           importance, confidence, valid_from, valid_until, supersedes::text,
                           embedding_model, deleted_at
                    FROM memory WHERE memory_id = %s::uuid
                    """,
                    (memory_id,),
                )
                row = cursor.fetchone()
                return _serialize(row) if row else None

    def recent(self, project: Optional[str] = None, kinds: Optional[List[str]] = None, limit: int = 10):
        limit = max(1, min(limit, 50))
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT memory_id::text, project_id, project_label, session_id, message_id,
                           kind, title, left(content, 2000) AS content, metadata, occurred_at,
                           importance, confidence
                    FROM memory
                    WHERE deleted_at IS NULL
                      AND (%s::text IS NULL OR project_id = %s OR project_label = %s)
                      AND (%s::text[] IS NULL OR kind = ANY(%s::text[]))
                    ORDER BY importance DESC, occurred_at DESC LIMIT %s
                    """,
                    (project, project, project, kinds, kinds, limit),
                )
                return [_serialize(row) for row in cursor.fetchall()]

    def session_summary(self, session_id: str):
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT session_id, project_id, project_label, title, started_at, updated_at,
                           deleted_at, metadata
                    FROM session_record WHERE session_id = %s
                    """,
                    (session_id,),
                )
                session = cursor.fetchone()
                if not session:
                    return None
                cursor.execute(
                    """
                    SELECT memory_id::text, message_id, kind, title, left(content, 2000) AS content,
                           metadata, occurred_at, importance, confidence
                    FROM memory
                    WHERE session_id = %s AND deleted_at IS NULL
                    ORDER BY occurred_at, source_id LIMIT 100
                    """,
                    (session_id,),
                )
                result = _serialize(session)
                result["memories"] = [_serialize(row) for row in cursor.fetchall()]
                return result

    def stats(self):
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT kind, count(*) as cnt FROM memory WHERE deleted_at IS NULL GROUP BY kind ORDER BY cnt DESC"
                )
                return {row["kind"]: row["cnt"] for row in cursor.fetchall()}

    def ready(self):
        with psycopg.connect(self.database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('transaction_read_only'), COUNT(*) FROM memory")
                read_only, _count = cursor.fetchone()
                return read_only == "on"


def _serialize(row):
    if row is None:
        return None
    return json.loads(json.dumps(dict(row), default=str))
