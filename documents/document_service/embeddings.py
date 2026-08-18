from typing import List

import requests

from .config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDING_TIMEOUT_SECONDS,
    OLLAMA_URL,
)


def embed(texts: List[str]) -> List[List[float]]:
    result = []
    for offset in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[offset : offset + EMBEDDING_BATCH_SIZE]
        response = requests.post(
            "%s/api/embed" % OLLAMA_URL,
            json={"model": EMBEDDING_MODEL, "input": batch, "truncate": True},
            timeout=EMBEDDING_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        values = response.json()["embeddings"]
        if len(values) != len(batch) or any(len(value) != EMBEDDING_DIMENSION for value in values):
            raise ValueError("Ollama returned embeddings with an unexpected shape")
        result.extend(values)
    return result


def vector_literal(value):
    return "[" + ",".join(str(item) for item in value) + "]"
