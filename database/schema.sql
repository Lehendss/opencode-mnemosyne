CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE ingestion_event (
    event_id text PRIMARY KEY,
    schema_version integer NOT NULL,
    event_type text NOT NULL,
    project_id text NOT NULL,
    project_label text,
    session_id text,
    message_id text,
    occurred_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL,
    raw_object_key text NOT NULL,
    raw_sha256 text NOT NULL,
    payload_size integer NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE session_record (
    session_id text PRIMARY KEY,
    project_id text NOT NULL,
    project_label text,
    title text,
    directory_hash text,
    opencode_version text,
    started_at timestamptz,
    updated_at timestamptz,
    archived_at timestamptz,
    deleted_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE memory (
    memory_id uuid PRIMARY KEY,
    project_id text NOT NULL,
    project_label text,
    session_id text,
    message_id text,
    source_type text NOT NULL,
    source_id text NOT NULL,
    kind text NOT NULL,
    title text,
    content text NOT NULL,
    content_sha256 text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    importance real NOT NULL DEFAULT 0.5 CHECK (importance BETWEEN 0 AND 1),
    confidence real NOT NULL DEFAULT 0.8 CHECK (confidence BETWEEN 0 AND 1),
    valid_from timestamptz,
    valid_until timestamptz,
    supersedes uuid REFERENCES memory(memory_id),
    access_count integer NOT NULL DEFAULT 0,
    last_accessed_at timestamptz,
    occurred_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    embedding_model text NOT NULL,
    embedding vector(1024) NOT NULL,
    search_document tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title, '') || ' ' || content)
    ) STORED,
    UNIQUE (project_id, source_type, source_id)
);

CREATE INDEX ingestion_session_idx ON ingestion_event (session_id, occurred_at DESC);
CREATE INDEX session_project_updated_idx ON session_record (project_id, updated_at DESC);
CREATE INDEX memory_project_time_idx ON memory (project_id, occurred_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX memory_session_idx ON memory (session_id) WHERE session_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX memory_metadata_idx ON memory USING gin (metadata);
CREATE INDEX memory_kind_importance_idx ON memory (kind, importance DESC) WHERE deleted_at IS NULL;
CREATE INDEX memory_search_idx ON memory USING gin (search_document);
CREATE INDEX memory_title_trgm_idx ON memory USING gin (title gin_trgm_ops);
CREATE INDEX memory_embedding_idx ON memory USING hnsw (embedding vector_cosine_ops);

SELECT format('CREATE ROLE memory_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memory_reader')
\gexec

ALTER ROLE memory_reader PASSWORD :'reader_password';

GRANT CONNECT ON DATABASE opencode_memory TO memory_reader;
GRANT USAGE ON SCHEMA public TO memory_reader;
GRANT SELECT ON ingestion_event, session_record, memory TO memory_reader;
ALTER ROLE memory_reader SET default_transaction_read_only = on;
ALTER ROLE memory_reader SET statement_timeout = '5s';
