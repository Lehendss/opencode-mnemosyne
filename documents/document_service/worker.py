import hashlib
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .config import (
    DATABASE_URL,
    EMBEDDING_MODEL,
    JOB_MAX_ATTEMPTS,
    MAX_DOCUMENT_PAGES,
    MINIO_BUCKET,
    OCR_MIN_CHARS,
)
from .embeddings import embed, vector_literal
from .storage import client as minio_client


def connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def claim_job():
    with connection() as database:
        with database.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_job
                SET status = 'queued', locked_at = NULL, lease_token = NULL,
                    available_at = now(), updated_at = now(),
                    last_error = 'Recovered stale running job'
                WHERE status = 'running' AND locked_at < now() - interval '30 minutes'
                """
            )
            cursor.execute(
                """
                SELECT j.job_id, j.document_id, j.attempts, d.object_key
                FROM document_job j
                JOIN document d ON d.document_id = j.document_id
                WHERE j.status = 'queued' AND j.available_at <= now()
                ORDER BY j.created_at
                FOR UPDATE OF j SKIP LOCKED
                LIMIT 1
                """
            )
            job = cursor.fetchone()
            if not job:
                return None
            lease_token = uuid.uuid4()
            cursor.execute(
                """
                UPDATE document_job
                SET status = 'running', attempts = attempts + 1, locked_at = now(),
                    lease_token = %s, updated_at = now()
                WHERE job_id = %s
                """,
                (lease_token, job["job_id"]),
            )
            cursor.execute(
                "UPDATE document SET status = 'processing', updated_at = now() WHERE document_id = %s",
                (job["document_id"],),
            )
            job["attempts"] += 1
            job["lease_token"] = lease_token
            return job


def page_count(path: str) -> int:
    result = subprocess.run(
        ["pdfinfo", path], check=True, timeout=30, capture_output=True, text=True
    )
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if not match:
        raise ValueError("PDF page count is unavailable")
    count = int(match.group(1))
    if count < 1 or count > MAX_DOCUMENT_PAGES:
        raise ValueError("PDF has %s pages; configured maximum is %s" % (count, MAX_DOCUMENT_PAGES))
    return count


def embedded_page(path: str, number: int):
    process = subprocess.Popen(
        ["pdftotext", "-f", str(number), "-l", str(number), "-layout", "-enc", "UTF-8", path, "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    timed_out = threading.Event()

    def terminate_for_timeout():
        timed_out.set()
        process.kill()

    timer = threading.Timer(60, terminate_for_timeout)
    timer.start()
    try:
        output = process.stdout.read(150001)
        if process.stdout.read(1):
            process.terminate()
        return_code = process.wait(timeout=60)
        if timed_out.is_set():
            raise subprocess.TimeoutExpired(process.args, 60)
        if return_code not in (0, -15):
            raise subprocess.CalledProcessError(return_code, process.args)
        return output.decode("utf-8", errors="replace")
    except Exception:
        process.kill()
        process.wait()
        raise
    finally:
        timer.cancel()
        if process.stdout:
            process.stdout.close()


def ocr_page(path: str, number: int, directory: str) -> str:
    prefix = str(Path(directory) / ("page-%s" % number))
    subprocess.run(
        [
            "pdftoppm", "-f", str(number), "-l", str(number), "-singlefile",
            "-png", "-scale-to", "4000", path, prefix,
        ],
        check=True,
        timeout=90,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    image = "%s.png" % prefix
    try:
        result = subprocess.run(
            ["tesseract", image, "stdout", "-l", "spa+eng", "--psm", "3"],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        return result.stdout
    finally:
        Path(image).unlink(missing_ok=True)


def normalize(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()[:150000]


def process(job):
    with tempfile.TemporaryDirectory(prefix="document-worker-") as directory:
        source = str(Path(directory) / "source.pdf")
        minio_client().fget_object(MINIO_BUCKET, job["object_key"], source)
        count = page_count(source)
        pages = []
        for index in range(count):
            text = normalize(embedded_page(source, index + 1))
            source_type = "embedded"
            if len(text) < OCR_MIN_CHARS:
                text = normalize(ocr_page(source, index + 1, directory))
                source_type = "ocr" if text else "empty"
            pages.append((index + 1, text, source_type))

        non_empty = [page[1] for page in pages if page[1]]
        vectors = iter(embed(non_empty))
        records = []
        for number, text, source_type in pages:
            vector = vector_literal(next(vectors)) if text else None
            records.append(
                (
                    job["document_id"], number, text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(), source_type,
                    EMBEDDING_MODEL if text else None, vector,
                )
            )

        with connection() as database:
            with database.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1 FROM document_job
                    WHERE job_id = %s AND status = 'running' AND lease_token = %s
                    FOR UPDATE
                    """,
                    (job["job_id"], job["lease_token"]),
                )
                if not cursor.fetchone():
                    return
                cursor.execute("DELETE FROM document_page WHERE document_id = %s", (job["document_id"],))
                cursor.executemany(
                    """
                    INSERT INTO document_page (
                        document_id, page_number, content, content_sha256,
                        text_source, embedding_model, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    records,
                )
                cursor.execute(
                    """
                    UPDATE document
                    SET status = 'ready', page_count = %s, error_code = NULL,
                        error_message = NULL, updated_at = now()
                    WHERE document_id = %s
                    """,
                    (count, job["document_id"]),
                )
                cursor.execute(
                    """
                    UPDATE document_job
                    SET status = 'completed', locked_at = NULL, lease_token = NULL, updated_at = now()
                    WHERE job_id = %s AND lease_token = %s
                    """,
                    (job["job_id"], job["lease_token"]),
                )


def heartbeat(job, stop):
    while not stop.wait(20):
        try:
            with connection() as database:
                database.execute(
                    """
                    UPDATE document_job SET locked_at = now(), updated_at = now()
                    WHERE job_id = %s AND status = 'running' AND lease_token = %s
                    """,
                    (job["job_id"], job["lease_token"]),
                )
        except Exception as error:
            print("Document job heartbeat failed: %s" % error, flush=True)


def fail(job, error):
    message = str(error).replace("\x00", "")[:2000]
    final = job["attempts"] >= JOB_MAX_ATTEMPTS
    with connection() as database:
        with database.cursor() as cursor:
            cursor.execute(
                """
                UPDATE document_job
                SET status = %s, locked_at = NULL, lease_token = NULL, last_error = %s,
                    available_at = now() + (%s * interval '1 minute'), updated_at = now()
                WHERE job_id = %s AND status = 'running' AND lease_token = %s
                RETURNING job_id
                """,
                (
                    "failed" if final else "queued", message, min(30, 2 ** job["attempts"]),
                    job["job_id"], job["lease_token"],
                ),
            )
            if not cursor.fetchone():
                return
            cursor.execute(
                """
                UPDATE document
                SET status = %s, error_code = 'processing_failed', error_message = %s, updated_at = now()
                WHERE document_id = %s
                """,
                ("failed" if final else "queued", message, job["document_id"]),
            )


def main():
    while True:
        try:
            job = claim_job()
            if not job:
                time.sleep(2)
                continue
            try:
                stop = threading.Event()
                heartbeat_thread = threading.Thread(target=heartbeat, args=(job, stop), daemon=True)
                heartbeat_thread.start()
                try:
                    process(job)
                finally:
                    stop.set()
                    heartbeat_thread.join(timeout=2)
            except Exception as error:
                fail(job, error)
        except Exception as error:
            print("Document worker loop failed: %s" % error, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
