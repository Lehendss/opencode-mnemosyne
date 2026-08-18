import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .embeddings import OllamaEmbeddings
from .normalization import memories_from_envelope, session_from_envelope, tombstones_from_envelope
from .storage import Database, RawStorage


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("memory-worker")
RUNNING = True


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def ensure_directories(root: Path):
    for name in ("pending", "processing", "retry", "archive", "failed"):
        (root / name).mkdir(parents=True, exist_ok=True, mode=0o700)


def recover_stale(root: Path):
    threshold = time.time() - 300
    for path in (root / "processing").glob("*.json"):
        if path.stat().st_mtime < threshold:
            os.replace(path, root / "pending" / path.name)


def recover_retries(root: Path, delay_seconds: int):
    threshold = time.time() - delay_seconds
    for path in (root / "retry").glob("*.json"):
        if path.stat().st_mtime >= threshold:
            continue
        pending = root / "pending" / path.name
        if pending.exists():
            path.unlink()
        else:
            os.replace(path, pending)


def archive_path(root: Path, filename: str) -> Path:
    today = datetime.now(timezone.utc)
    directory = (
        root
        / "archive"
        / ("%04d" % today.year)
        / ("%02d" % today.month)
        / ("%02d" % today.day)
    )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / filename


def process(path: Path, settings: Settings, raw_storage, database, embedder, on_progress=None):
    with path.open("r", encoding="utf-8") as handle:
        envelope = json.load(handle)
    if envelope.get("schema_version") != 1:
        raise ValueError("Unsupported schema version")
    if not envelope.get("event_id") or not envelope.get("project_id"):
        raise ValueError("Invalid event envelope")

    raw_key, raw_hash, payload_size = raw_storage.store(envelope)
    memories = memories_from_envelope(envelope, settings.max_memory_chars)
    embeddings = embedder.embed([memory["content"] for memory in memories], on_progress)
    database.persist(
        envelope,
        raw_key,
        raw_hash,
        payload_size,
        session_from_envelope(envelope),
        memories,
        embeddings,
        list(tombstones_from_envelope(envelope)),
    )


def prune_archive(root: Path, days: int):
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    for path in (root / "archive").glob("**/*.json"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < threshold:
            path.unlink()


def write_health(root: Path, status: str, error=None, progress=None, include_counts=True):
    health = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error": str(error) if error else None,
        "progress": progress,
    }
    if include_counts:
        health["pending"] = len(list((root / "pending").glob("*.json")))
        health["retry"] = len(list((root / "retry").glob("*.json")))
        health["failed"] = len(list((root / "failed").glob("*.json")))
    Path("/tmp/worker-health.json").write_text(json.dumps(health), encoding="utf-8")


def main():
    settings = Settings.from_env()
    ensure_directories(settings.outbox_root)
    recover_stale(settings.outbox_root)
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
        settings.embedding_batch_size,
        settings.embedding_timeout_seconds,
    )
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_prune = 0
    last_retry_recovery = 0

    while RUNNING:
        try:
            if time.time() - last_retry_recovery > 30:
                recover_retries(settings.outbox_root, settings.retry_delay_seconds)
                last_retry_recovery = time.time()
            pending = next(settings.outbox_root.joinpath("pending").glob("*.json"), None)
            if pending is None:
                write_health(settings.outbox_root, "ok")
                time.sleep(1)
                continue
            claimed = settings.outbox_root / "processing" / pending.name
            try:
                os.replace(pending, claimed)
            except FileNotFoundError:
                continue
            try:
                write_health(
                    settings.outbox_root,
                    "processing",
                    progress={"event": claimed.name, "completed": 0, "total": None},
                )
                process(
                    claimed,
                    settings,
                    raw_storage,
                    database,
                    embedder,
                    lambda completed, total: write_health(
                        settings.outbox_root,
                        "processing",
                        progress={"event": claimed.name, "completed": completed, "total": total},
                        include_counts=False,
                    ),
                )
                os.replace(claimed, archive_path(settings.outbox_root, claimed.name))
                write_health(settings.outbox_root, "ok")
            except (ValueError, json.JSONDecodeError) as error:
                LOGGER.exception("Permanent failure for %s", claimed.name)
                os.replace(claimed, settings.outbox_root / "failed" / claimed.name)
                (settings.outbox_root / "failed" / (claimed.name + ".error")).write_text(str(error), encoding="utf-8")
                write_health(settings.outbox_root, "degraded", error)
            except Exception as error:
                LOGGER.exception("Transient failure for %s", claimed.name)
                retry = settings.outbox_root / "retry" / claimed.name
                os.replace(claimed, retry)
                os.utime(retry, None)
                write_health(settings.outbox_root, "degraded", error)
                time.sleep(5)
            if time.time() - last_prune > 3600:
                prune_archive(settings.outbox_root, settings.archive_retention_days)
                last_prune = time.time()
        except Exception as error:
            LOGGER.exception("Worker loop failure")
            write_health(settings.outbox_root, "degraded", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
