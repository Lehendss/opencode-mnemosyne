import gzip
import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import boto3
import psycopg
from botocore.exceptions import ClientError


class RawStorage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str):
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url="http://%s" % endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    def store(self, envelope: Dict[str, Any]):
        raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
        compressed = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(raw).hexdigest()
        date = datetime.fromisoformat(envelope["occurred_at"].replace("Z", "+00:00"))
        key = "v1/%s/%04d/%02d/%02d/%s.json.gz" % (
            envelope["project_id"], date.year, date.month, date.day, envelope["event_id"]
        )
        try:
            existing = self.client.head_object(Bucket=self.bucket, Key=key)
            if existing.get("Metadata", {}).get("sha256") != digest:
                stored_envelope, stored_digest, stored_size = self.load(key)
                if (
                    stored_envelope.get("event_id") != envelope["event_id"]
                    or stored_envelope.get("payload_sha256") != envelope.get("payload_sha256")
                ):
                    raise ValueError("Object hash mismatch for existing event %s" % envelope["event_id"])
                return key, stored_digest, stored_size
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=compressed,
                ContentType="application/json",
                ContentEncoding="gzip",
                Metadata={
                    "event-id": envelope["event_id"],
                    "schema-version": str(envelope["schema_version"]),
                    "sha256": digest,
                },
            )
        return key, digest, len(raw)

    def ready(self):
        self.client.head_bucket(Bucket=self.bucket)
        return True

    def load(self, key: str):
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        compressed = response["Body"].read()
        raw = gzip.decompress(compressed)
        envelope = json.loads(raw.decode("utf-8"))
        return envelope, hashlib.sha256(raw).hexdigest(), len(raw)


class Database:
    def __init__(self, database_url: str, embedding_model: str):
        self.database_url = database_url
        self.embedding_model = embedding_model

    def persist(
        self,
        envelope: Dict[str, Any],
        raw_object_key: str,
        raw_sha256: str,
        payload_size: int,
        session: Optional[Dict[str, Any]],
        memories: List[Dict[str, Any]],
        embeddings: List[List[float]],
        tombstones: Iterable[Dict[str, str]],
    ):
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingestion_event (
                        event_id, schema_version, event_type, project_id, project_label,
                        session_id, message_id, occurred_at, captured_at, raw_object_key,
                        raw_sha256, payload_size
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        raw_object_key = EXCLUDED.raw_object_key,
                        raw_sha256 = EXCLUDED.raw_sha256
                    WHERE ingestion_event.raw_sha256 = EXCLUDED.raw_sha256
                    """,
                    (
                        envelope["event_id"], envelope["schema_version"], envelope["event_type"],
                        envelope["project_id"], envelope.get("project_label"), envelope.get("session_id"),
                        envelope.get("message_id"), envelope["occurred_at"], envelope["captured_at"],
                        raw_object_key, raw_sha256, payload_size,
                    ),
                )
                if session:
                    cursor.execute(
                        """
                        INSERT INTO session_record (
                            session_id, project_id, project_label, title, directory_hash,
                            opencode_version, started_at, updated_at, deleted_at, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id) DO UPDATE SET
                            title = COALESCE(EXCLUDED.title, session_record.title),
                            updated_at = GREATEST(EXCLUDED.updated_at, session_record.updated_at),
                            deleted_at = COALESCE(EXCLUDED.deleted_at, session_record.deleted_at),
                            metadata = session_record.metadata || EXCLUDED.metadata
                        """,
                        (
                            session["session_id"], session["project_id"], session.get("project_label"),
                            session.get("title"), session.get("directory_hash"), session.get("opencode_version"),
                            session.get("started_at"), session["updated_at"], session.get("deleted_at"),
                            json.dumps(session.get("metadata", {})),
                        ),
                    )
                for item, embedding in zip(memories, embeddings):
                    cursor.execute(
                        """
                        INSERT INTO memory (
                            memory_id, project_id, project_label, session_id, message_id,
                            source_type, source_id, kind, title, content, content_sha256,
                            metadata, importance, confidence, valid_from, occurred_at,
                            embedding_model, embedding
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s::vector
                        )
                        ON CONFLICT (project_id, source_type, source_id) DO UPDATE SET
                            session_id = EXCLUDED.session_id,
                            message_id = EXCLUDED.message_id,
                            kind = EXCLUDED.kind,
                            title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            content_sha256 = EXCLUDED.content_sha256,
                            metadata = EXCLUDED.metadata,
                            importance = EXCLUDED.importance,
                            confidence = EXCLUDED.confidence,
                            valid_from = EXCLUDED.valid_from,
                            occurred_at = EXCLUDED.occurred_at,
                            updated_at = now(),
                            deleted_at = NULL,
                            embedding_model = EXCLUDED.embedding_model,
                            embedding = EXCLUDED.embedding
                        """,
                        (
                            item["memory_id"], item["project_id"], item.get("project_label"),
                            item.get("session_id"), item.get("message_id"), item["source_type"],
                            item["source_id"], item["kind"], item.get("title"), item["content"],
                            item["content_sha256"], json.dumps(item.get("metadata", {})),
                            item.get("importance", 0.5), item.get("confidence", 0.8),
                            item.get("valid_from"), item["occurred_at"], self.embedding_model,
                            _vector(embedding),
                        ),
                    )
                for tombstone in tombstones:
                    if tombstone.get("source_id"):
                        cursor.execute(
                            "UPDATE memory SET deleted_at = %s WHERE project_id = %s AND source_type = %s AND source_id = %s",
                            (envelope["occurred_at"], envelope["project_id"], tombstone["source_type"], tombstone["source_id"]),
                        )
                    elif tombstone.get("message_id"):
                        cursor.execute(
                            "UPDATE memory SET deleted_at = %s WHERE project_id = %s AND message_id = %s",
                            (envelope["occurred_at"], envelope["project_id"], tombstone["message_id"]),
                        )
                    elif tombstone.get("session_id"):
                        cursor.execute(
                            "UPDATE memory SET deleted_at = %s WHERE project_id = %s AND session_id = %s",
                            (envelope["occurred_at"], envelope["project_id"], tombstone["session_id"]),
                        )

    def ready(self):
        with psycopg.connect(self.database_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                return cursor.fetchone() is not None

    def raw_object_keys(self, event_types: List[str]) -> List[str]:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT raw_object_key
                    FROM (
                        SELECT DISTINCT ON (session_id)
                               session_id, raw_object_key, occurred_at
                        FROM ingestion_event
                        WHERE event_type = ANY(%s)
                          AND session_id IS NOT NULL
                        ORDER BY session_id, occurred_at DESC
                    ) latest
                    ORDER BY occurred_at
                    """,
                    (event_types,),
                )
                return [row[0] for row in cursor.fetchall()]


def _vector(values: List[float]) -> str:
    return "[" + ",".join("%.9g" % value for value in values) + "]"
