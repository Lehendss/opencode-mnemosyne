import logging
from collections import Counter

from .config import Settings
from .embeddings import OllamaEmbeddings
from .normalization import memories_from_envelope, session_from_envelope, tombstones_from_envelope
from .storage import Database, RawStorage


logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("memory-reindex")
STRUCTURED_KINDS = {"preference", "decision", "procedure", "bug_resolution", "incident"}


def embed_in_batches(embedder, contents, batch_size=32):
    embeddings = []
    for offset in range(0, len(contents), batch_size):
        embeddings.extend(embedder.embed(contents[offset : offset + batch_size]))
    return embeddings


def main():
    settings = Settings.from_env()
    raw_storage = RawStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
    )
    database = Database(settings.database_url, settings.embedding_model)
    embedder = OllamaEmbeddings(
        settings.ollama_url,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    keys = database.raw_object_keys(["memory.session.snapshot"])
    counts = Counter()
    for index, key in enumerate(keys, 1):
        envelope, raw_hash, payload_size = raw_storage.load(key)
        memories = [
            memory
            for memory in memories_from_envelope(envelope, settings.max_memory_chars)
            if memory["kind"] in STRUCTURED_KINDS
        ]
        embeddings = embed_in_batches(embedder, [memory["content"] for memory in memories])
        database.persist(
            envelope,
            key,
            raw_hash,
            payload_size,
            session_from_envelope(envelope),
            memories,
            embeddings,
            list(tombstones_from_envelope(envelope)),
        )
        counts.update(memory["kind"] for memory in memories)
        LOGGER.info("Reindexed %s/%s: %s memories", index, len(keys), len(memories))
    LOGGER.info("Reindex complete: %s", dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
