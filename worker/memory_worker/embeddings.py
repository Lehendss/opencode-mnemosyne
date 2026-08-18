from typing import Callable, List, Optional

import requests


class OllamaEmbeddings:
    def __init__(self, base_url: str, model: str, dimension: int, batch_size: int = 4, timeout: int = 180):
        self.base_url = base_url
        self.model = model
        self.dimension = dimension
        self.batch_size = max(1, batch_size)
        self.timeout = max(1, timeout)

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
            response = requests.post(
                "%s/api/embed" % self.base_url,
                json={"model": self.model, "input": batch, "truncate": True},
                timeout=self.timeout,
            )
            response.raise_for_status()
            batch_embeddings = response.json()["embeddings"]
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
