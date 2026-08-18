CREATE TABLE IF NOT EXISTS document (
    document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_sha256 text NOT NULL UNIQUE,
    original_filename text NOT NULL,
    media_type text NOT NULL DEFAULT 'application/pdf',
    byte_size bigint NOT NULL CHECK (byte_size > 0),
    page_count integer CHECK (page_count > 0),
    object_key text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    extraction_version text NOT NULL DEFAULT 'pdf-v1',
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_project (
    document_id uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    project_id text NOT NULL,
    project_label text,
    original_filename text,
    title text,
    source_path_sha256 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, project_id)
);

CREATE TABLE IF NOT EXISTS document_page (
    document_id uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    page_number integer NOT NULL CHECK (page_number > 0),
    content text NOT NULL,
    content_sha256 text NOT NULL,
    text_source text NOT NULL CHECK (text_source IN ('embedded', 'ocr', 'empty')),
    embedding_model text,
    embedding vector(1024),
    search_document tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', content)
    ) STORED,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, page_number)
);

CREATE TABLE IF NOT EXISTS document_job (
    job_id bigserial PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES document(document_id) ON DELETE CASCADE,
    job_type text NOT NULL DEFAULT 'ingest' CHECK (job_type = 'ingest'),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    lease_token uuid,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE document_project ADD COLUMN IF NOT EXISTS original_filename text;
ALTER TABLE document_job ADD COLUMN IF NOT EXISTS lease_token uuid;
UPDATE document_project dp
SET original_filename = d.original_filename
FROM document d
WHERE d.document_id = dp.document_id AND dp.original_filename IS NULL;
ALTER TABLE document_project ALTER COLUMN original_filename SET NOT NULL;

CREATE INDEX IF NOT EXISTS document_project_project_idx
    ON document_project (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS document_page_search_idx
    ON document_page USING gin (search_document);
CREATE INDEX IF NOT EXISTS document_page_embedding_idx
    ON document_page USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS document_job_claim_idx
    ON document_job (status, available_at, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS document_job_active_idx
    ON document_job (document_id, job_type)
    WHERE status IN ('queued', 'running');
