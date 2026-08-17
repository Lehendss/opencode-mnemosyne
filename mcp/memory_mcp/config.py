import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    ollama_url: str
    embedding_model: str
    embedding_dimension: int
    port: int

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ["DATABASE_URL"],
            ollama_url=os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "bge-m3"),
            embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "1024")),
            port=int(os.getenv("MCP_PORT", "8787")),
        )
