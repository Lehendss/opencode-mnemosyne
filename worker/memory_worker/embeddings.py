import logging
from typing import Callable, List, Optional

import requests


LOGGER = logging.getLogger("memory-worker")


class OllamaEmbeddings:
    def __init__(self, base_url: str, model: str, dimension: int, batch_size: int = 4, timeout: int = 180):
        self.base_url = base_url
        self.model = model
        self.dimension = dimension
        self.batch_size = max(1, batch_size)
        self.timeout = max(1, timeout)

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        response = requests.post(
            "%s/api/embed" % self.base_url,
            json={"model": self.model, "input": batch, "truncate": True},
            timeout=self.timeout,
        )
        error_text = response.text.lower() if isinstance(response.text, str) else ""
        size_error = response.status_code == 413 or (
            response.status_code == 400 and any(
                marker in error_text
                for marker in ("context length", "input length", "too large", "too long")
            )
        )
        if size_error:
            if len(batch) > 1:
                middle = len(batch) // 2
                return self._embed_batch(batch[:middle]) + self._embed_batch(batch[middle:])
            text = batch[0]
            if len(text) <= 256:
                response.raise_for_status()
            shortened = text[:max(256, len(text) // 2)]
            LOGGER.warning(
                "Embedding input exceeded Ollama limits; shortening from %s to %s characters",
                len(text),
                len(shortened),
            )
            return self._embed_batch([shortened])
        response.raise_for_status()
        return response.json()["embeddings"]

    def embed(
        self,
        texts: List[str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> List[List[float]]:
        if not texts:
            return []
        embeddings = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            batch_embeddings = self._embed_batch(batch)
            if len(batch_embeddings) != len(batch):
                raise ValueError("Ollama returned an unexpected embedding count")
            for embedding in batch_embeddings:
                if len(embedding) != self.dimension:
                    raise ValueError(
                        "Embedding dimension %s does not match configured %s"
                        % (len(embedding), self.dimension)
                    )
            embeddings.extend(batch_embeddings)
            if on_progress:
                on_progress(len(embeddings), len(texts))
        return embeddings

    def ready(self) -> bool:
        response = requests.get("%s/api/tags" % self.base_url, timeout=5)
        response.raise_for_status()
        names = {model["name"].split(":")[0] for model in response.json().get("models", [])}
        return self.model.split(":")[0] in names
