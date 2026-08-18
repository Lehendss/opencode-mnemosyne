import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    ollama_url: str
    embedding_model: str
    embedding_dimension: int
    embedding_batch_size: int
    embedding_timeout_seconds: int
    outbox_root: Path
    archive_retention_days: int
    retry_delay_seconds: int
    max_memory_chars: int

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ["DATABASE_URL"],
            minio_endpoint=os.environ["MINIO_ENDPOINT"],
            minio_access_key=os.environ["MINIO_ACCESS_KEY"],
            minio_secret_key=os.environ["MINIO_SECRET_KEY"],
            minio_bucket=os.getenv("MINIO_BUCKET", "memory-raw"),
            ollama_url=os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "bge-m3"),
            embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
            embedding_batch_size=max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "4"))),
            embedding_timeout_seconds=max(1, int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "180"))),
            outbox_root=Path(os.getenv("OUTBOX_ROOT", "/data/outbox")),
            archive_retention_days=int(os.getenv("ARCHIVE_RETENTION_DAYS", "30")),
            retry_delay_seconds=max(1, int(os.getenv("RETRY_DELAY_SECONDS", "300"))),
            max_memory_chars=int(os.getenv("MAX_MEMORY_CHARS", "24000")),
        )
