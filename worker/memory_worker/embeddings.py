from typing import List

import requests


class OllamaEmbeddings:
    def __init__(self, base_url: str, model: str, dimension: int):
        self.base_url = base_url
        self.model = model
        self.dimension = dimension

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        response = requests.post(
            "%s/api/embed" % self.base_url,
            json={"model": self.model, "input": texts, "truncate": True},
            timeout=180,
        )
        response.raise_for_status()
        embeddings = response.json()["embeddings"]
        for embedding in embeddings:
            if len(embedding) != self.dimension:
                raise ValueError(
                    "Embedding dimension %s does not match configured %s"
                    % (len(embedding), self.dimension)
                )
        return embeddings

    def ready(self) -> bool:
        response = requests.get("%s/api/tags" % self.base_url, timeout=5)
        response.raise_for_status()
        names = {model["name"].split(":")[0] for model in response.json().get("models", [])}
        return self.model.split(":")[0] in names
