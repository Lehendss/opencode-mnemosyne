import json
import logging

import psycopg

from .config import Settings
from .storage import RawStorage


logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("memory-purge")
NOISE_EVENT_TYPES = ["message.part.delta", "message.part.updated"]


def chunks(values, size):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def main():
    settings = Settings.from_env()
    raw_storage = RawStorage(
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
        settings.minio_bucket,
    )
    with psycopg.connect(settings.database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT raw_object_key FROM ingestion_event WHERE event_type = ANY(%s)",
                (NOISE_EVENT_TYPES,),
            )
            keys = [row[0] for row in cursor.fetchall()]
        for batch in chunks(keys, 1000):
            response = raw_storage.client.delete_objects(
                Bucket=raw_storage.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            if response.get("Errors"):
                raise RuntimeError("MinIO failed to delete raw objects: %s" % response["Errors"])
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM ingestion_event WHERE event_type = ANY(%s)",
                (NOISE_EVENT_TYPES,),
            )
            deleted_events = cursor.rowcount

    deleted_files = 0
    for path in settings.outbox_root.glob("**/*.json"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                envelope = json.load(handle)
            if envelope.get("event_type") in NOISE_EVENT_TYPES:
                path.unlink()
                deleted_files += 1
        except (OSError, json.JSONDecodeError):
            LOGGER.warning("Skipped unreadable outbox file %s", path)

    LOGGER.info(
        "Purged %s database events, %s raw objects, and %s outbox files",
        deleted_events,
        len(keys),
        deleted_files,
    )


if __name__ == "__main__":
    main()
