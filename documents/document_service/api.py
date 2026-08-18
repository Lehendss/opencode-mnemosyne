import asyncio
import hashlib
import hmac
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Union

import psycopg
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.background import BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from minio.error import S3Error
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from .config import (
    DATABASE_URL,
    DOCUMENT_API_TOKEN,
    EMBEDDING_MODEL,
    MAX_DOCUMENT_BYTES,
    MAX_PAGE_RESPONSE_CHARS,
    MAX_PAGES_PER_READ,
    MAX_READ_RESPONSE_CHARS,
    MINIO_BUCKET,
)
from .embeddings import embed, vector_literal
from .storage import client as minio_client


app = FastAPI(title="OpenCode Document RAG", docs_url=None, redoc_url=None)
heavy_request_slots = asyncio.Semaphore(2)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    project_id: str = Field(min_length=16, max_length=128)
    limit: int = Field(default=8, ge=1, le=20)


def authorize(authorization: Optional[str] = Header(default=None)):
    expected = "Bearer %s" % DOCUMENT_API_TOKEN
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid document service token")


def connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@app.middleware("http")
async def limit_heavy_requests(request, call_next):
    path = request.url.path
    heavy = (
        (request.method == "POST" and path == "/v1/documents")
        or path.endswith("/download")
        or "/page/" in path
    )
    if not heavy:
        return await call_next(request)
    try:
        await asyncio.wait_for(heavy_request_slots.acquire(), timeout=2)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=429, content={"detail": "Document service is busy; retry shortly"})
    try:
        return await call_next(request)
    finally:
        heavy_request_slots.release()


def clean_filename(value: str) -> str:
    name = Path(value or "document.pdf").name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return (name or "document.pdf")[:180]


def require_access(cursor, document_id: Union[str, uuid.UUID], project_id: str):
    cursor.execute(
        """
        SELECT d.document_id, d.content_sha256, d.media_type, d.byte_size,
               d.page_count, d.object_key, d.status, d.extraction_version,
               d.error_code, d.error_message, d.created_at, d.updated_at,
               dp.original_filename, dp.title, dp.project_label
        FROM document d
        JOIN document_project dp ON dp.document_id = d.document_id
        WHERE d.document_id = %s AND dp.project_id = %s
        """,
        (str(document_id), project_id),
    )
    record = cursor.fetchone()
    if not record:
        raise HTTPException(status_code=404, detail="Document not found in this project")
    return record


@app.get("/health")
def health():
    with connection() as database:
        database.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/v1/documents", dependencies=[Depends(authorize)])
def ingest_document(
    file: UploadFile = File(...),
    project_id: str = Form(..., min_length=16, max_length=128),
    project_label: Optional[str] = Form(default=None, max_length=200),
    title: Optional[str] = Form(default=None, max_length=500),
    source_path_sha256: Optional[str] = Form(default=None, max_length=64),
):
    digest = hashlib.sha256()
    size = 0
    first = b""
    temporary = tempfile.NamedTemporaryFile(prefix="document-upload-", suffix=".pdf", delete=False)
    try:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            if not first:
                first = chunk[:8]
            size += len(chunk)
            if size > MAX_DOCUMENT_BYTES:
                raise HTTPException(status_code=413, detail="PDF exceeds the configured size limit")
            digest.update(chunk)
            temporary.write(chunk)
        temporary.close()
        if size == 0 or not first.startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="Only valid PDF files are accepted")

        content_sha256 = digest.hexdigest()
        object_key = "documents/%s/%s.pdf" % (content_sha256[:2], content_sha256)
        storage = minio_client()
        object_existed = True
        try:
            storage.stat_object(MINIO_BUCKET, object_key)
        except S3Error as error:
            if error.code not in ("NoSuchKey", "NoSuchObject"):
                raise
            object_existed = False
        storage.fput_object(
            MINIO_BUCKET,
            object_key,
            temporary.name,
            content_type="application/pdf",
            metadata={"sha256": content_sha256},
        )

        try:
            with connection() as database:
                with database.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO document (
                            content_sha256, original_filename, byte_size, object_key
                        ) VALUES (%s, %s, %s, %s)
                        ON CONFLICT (content_sha256) DO UPDATE
                        SET updated_at = document.updated_at
                        RETURNING document_id, status, page_count, created_at
                        """,
                        (content_sha256, clean_filename(file.filename or "document.pdf"), size, object_key),
                    )
                    document = cursor.fetchone()
                    cursor.execute(
                        """
                        INSERT INTO document_project (
                            document_id, project_id, project_label, original_filename,
                            title, source_path_sha256
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (document_id, project_id) DO UPDATE SET
                            project_label = COALESCE(EXCLUDED.project_label, document_project.project_label),
                            original_filename = EXCLUDED.original_filename,
                            title = COALESCE(EXCLUDED.title, document_project.title),
                            source_path_sha256 = COALESCE(EXCLUDED.source_path_sha256, document_project.source_path_sha256)
                        """,
                        (
                            document["document_id"],
                            project_id,
                            project_label,
                            clean_filename(file.filename or "document.pdf"),
                            title,
                            source_path_sha256,
                        ),
                    )
                    if document["status"] in ("queued", "failed"):
                        cursor.execute(
                            """
                            INSERT INTO document_job (document_id)
                            VALUES (%s)
                            ON CONFLICT DO NOTHING
                            """,
                            (document["document_id"],),
                        )
                        cursor.execute(
                            """
                            UPDATE document
                            SET status = 'queued', error_code = NULL, error_message = NULL, updated_at = now()
                            WHERE document_id = %s
                            """,
                            (document["document_id"],),
                        )
        except Exception:
            if not object_existed:
                try:
                    storage.remove_object(MINIO_BUCKET, object_key)
                except Exception:
                    pass
            raise
        return {
            "document_id": str(document["document_id"]),
            "status": "ready" if document["status"] == "ready" else "queued",
            "page_count": document["page_count"],
            "deduplicated": document["status"] == "ready",
            "content_sha256": content_sha256,
        }
    finally:
        try:
            temporary.close()
        except Exception:
            pass
        Path(temporary.name).unlink(missing_ok=True)


@app.post("/v1/search", dependencies=[Depends(authorize)])
def search_documents(request: SearchRequest):
    query_embedding = None
    try:
        query_embedding = vector_literal(embed([request.query])[0])
    except Exception:
        pass

    with connection() as database:
        with database.cursor() as cursor:
            if query_embedding:
                cursor.execute(
                    """
                    SELECT d.document_id, dp.original_filename, dp.title, p.page_number,
                           left(p.content, 1400) AS snippet, p.text_source,
                           (0.60 * (1 - (p.embedding <=> %s::vector)) +
                            0.25 * ts_rank_cd(p.search_document, plainto_tsquery('simple', %s)) +
                            0.15 * GREATEST(similarity(coalesce(dp.original_filename, ''), %s), similarity(coalesce(dp.title, ''), %s))) AS score
                    FROM document_page p
                    JOIN document d ON d.document_id = p.document_id
                    JOIN document_project dp ON dp.document_id = d.document_id
                    WHERE dp.project_id = %s AND d.status = 'ready' AND p.embedding IS NOT NULL
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    (query_embedding, request.query, request.query, request.query, request.project_id, request.limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT d.document_id, dp.original_filename, dp.title, p.page_number,
                           left(p.content, 1400) AS snippet, p.text_source,
                           ts_rank_cd(p.search_document, plainto_tsquery('simple', %s)) AS score
                    FROM document_page p
                    JOIN document d ON d.document_id = p.document_id
                    JOIN document_project dp ON dp.document_id = d.document_id
                    WHERE dp.project_id = %s AND d.status = 'ready'
                      AND (p.search_document @@ plainto_tsquery('simple', %s)
                           OR coalesce(dp.original_filename, '') %% %s OR coalesce(dp.title, '') %% %s)
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    (request.query, request.project_id, request.query, request.query, request.query, request.limit),
                )
            rows = cursor.fetchall()
    for row in rows:
        row["document_id"] = str(row["document_id"])
        row["score"] = float(row["score"] or 0)
    return {"query": request.query, "results": rows}


@app.get("/v1/documents/{document_id}", dependencies=[Depends(authorize)])
def get_document(document_id: uuid.UUID, project_id: str = Query(...)):
    with connection() as database:
        with database.cursor() as cursor:
            record = require_access(cursor, document_id, project_id)
    record["document_id"] = str(record["document_id"])
    record.pop("object_key", None)
    return record


@app.get("/v1/documents/{document_id}/pages", dependencies=[Depends(authorize)])
def get_pages(document_id: uuid.UUID, pages: List[int] = Query(...), project_id: str = Query(...)):
    selected = sorted(set(pages))
    if not selected or len(selected) > MAX_PAGES_PER_READ or any(page < 1 for page in selected):
        raise HTTPException(status_code=400, detail="Request between 1 and %s pages" % MAX_PAGES_PER_READ)
    with connection() as database:
        with database.cursor() as cursor:
            require_access(cursor, document_id, project_id)
            cursor.execute(
                """
                SELECT page_number, content, text_source
                FROM document_page
                WHERE document_id = %s AND page_number = ANY(%s)
                ORDER BY page_number
                """,
                (str(document_id), selected),
            )
            rows = cursor.fetchall()
    remaining = MAX_READ_RESPONSE_CHARS
    for row in rows:
        original_length = len(row["content"])
        allowed = min(MAX_PAGE_RESPONSE_CHARS, remaining)
        row["content"] = row["content"][:allowed]
        row["content_length"] = original_length
        row["truncated"] = len(row["content"]) < original_length
        remaining -= len(row["content"])
    return {
        "document_id": document_id,
        "pages": rows,
        "response_truncated": any(row["truncated"] for row in rows),
    }


def materialize_original(document_id: uuid.UUID, project_id: str):
    directory = tempfile.mkdtemp(prefix="document-download-")
    target = os.path.join(directory, "%s.pdf" % document_id)
    with connection() as database:
        with database.cursor() as cursor:
            record = require_access(cursor, document_id, project_id)
    minio_client().fget_object(MINIO_BUCKET, record["object_key"], target)
    return target, record


def remove_download(path: str):
    try:
        Path(path).unlink(missing_ok=True)
        Path(path).parent.rmdir()
    except OSError:
        pass


@app.get("/v1/documents/{document_id}/download", dependencies=[Depends(authorize)])
def download_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    project_id: str = Query(...),
):
    target, record = materialize_original(document_id, project_id)
    background_tasks.add_task(remove_download, target)
    return FileResponse(target, media_type="application/pdf", filename=record["original_filename"] or "document.pdf")


@app.get("/v1/documents/{document_id}/page/{page_number}.jpg", dependencies=[Depends(authorize)])
def render_page(
    document_id: uuid.UUID,
    page_number: int,
    background_tasks: BackgroundTasks,
    project_id: str = Query(...),
):
    if page_number < 1:
        raise HTTPException(status_code=400, detail="Invalid page number")
    source, record = materialize_original(document_id, project_id)
    if record["page_count"] and page_number > record["page_count"]:
        remove_download(source)
        raise HTTPException(status_code=404, detail="Page not found")
    target = str(Path(source).with_suffix(".jpg"))
    try:
        subprocess.run(
            [
                "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                "-singlefile", "-jpeg", "-scale-to", "2400", source, str(Path(target).with_suffix("")),
            ],
            check=True,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as error:
        remove_download(source)
        remove_download(target)
        raise HTTPException(status_code=422, detail="Could not render page") from error
    background_tasks.add_task(remove_download, source)
    background_tasks.add_task(remove_download, target)
    return FileResponse(target, media_type="image/jpeg", filename="page-%s.jpg" % page_number)
