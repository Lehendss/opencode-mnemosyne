import argparse
import logging

import psycopg

from .config import Settings
from .embeddings import OllamaEmbeddings
from .normalization import memories_from_envelope, tombstones_from_envelope
from .storage import Database, RawStorage


logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("memory-replay")
RELEVANT_EVENTS = (
    "memory.session.snapshot",
    "message.part.removed",
    "message.removed",
    "session.created",
    "session.deleted",
    "session.updated",
)


def event_records(database_url, after):
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_id, raw_object_key, raw_sha256
                FROM ingestion_event
                WHERE ingested_at >= %s AND event_type = ANY(%s)
                ORDER BY ingested_at, event_id
                """,
                (after, list(RELEVANT_EVENTS)),
            )
            return cursor.fetchall()


def replay_records(records, storage, database, embedder, max_memory_chars):
    changed_count = 0
    for index, (event_id, object_key, expected_hash) in enumerate(records, 1):
        envelope, raw_hash, payload_size = storage.load(object_key)
        if envelope.get("event_id") != event_id or raw_hash != expected_hash:
            raise ValueError("Raw object does not match ingestion event %s" % event_id)
        memories = memories_from_envelope(envelope, max_memory_chars)
        tombstones = list(tombstones_from_envelope(envelope))
        if memories or tombstones:
            embeddings = embedder.embed([memory["content"] for memory in memories])
            database.persist(
                envelope,
                object_key,
                raw_hash,
                payload_size,
                None,
                memories,
                embeddings,
                tombstones,
            )
            changed_count += len(memories)

        if index % 50 == 0 or index == len(records):
            LOGGER.info("Replayed %s/%s events; %s memories written", index, len(records), changed_count)
    return changed_count


def main():
    parser = argparse.ArgumentParser(description="Replay raw events after a verified memory-table restore")
    parser.add_argument("--after", required=True, help="Replay events ingested at or after this timestamp")
    args = parser.parse_args()

    settings = Settings.from_env()
    storage = RawStorage(
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
        settings.embedding_batch_size,
        settings.embedding_timeout_seconds,
    )
    records = event_records(settings.database_url, args.after)
    replay_records(records, storage, database, embedder, settings.max_memory_chars)


if __name__ == "__main__":
    main()
