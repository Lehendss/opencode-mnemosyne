#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  READER_PASSWORD="$(openssl rand -hex 24)"
  MINIO_PASSWORD="$(openssl rand -hex 24)"
  DOCUMENT_TOKEN="$(openssl rand -hex 32)"
  DOCUMENT_DB_PASSWORD="$(openssl rand -hex 24)"
  DOCUMENT_MINIO_PASSWORD="$(openssl rand -hex 24)"
  cat > "$ENV_FILE" <<EOF
POSTGRES_DB=opencode_memory
POSTGRES_USER=memory_writer
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
MEMORY_READER_PASSWORD=$READER_PASSWORD
MINIO_ROOT_USER=memory_admin
MINIO_ROOT_PASSWORD=$MINIO_PASSWORD
MINIO_BUCKET=memory-raw
OLLAMA_URL=http://host.docker.internal:11434
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSION=1024
EMBEDDING_BATCH_SIZE=4
EMBEDDING_TIMEOUT_SECONDS=180
OUTBOX_ROOT=/data/outbox
ARCHIVE_RETENTION_DAYS=30
RETRY_DELAY_SECONDS=300
MAX_MEMORY_CHARS=24000
MCP_PORT=8787
WEBUI_PORT=8788
DOCUMENT_PORT=8789
DOCUMENT_API_TOKEN=$DOCUMENT_TOKEN
DOCUMENT_DB_PASSWORD=$DOCUMENT_DB_PASSWORD
DOCUMENT_MINIO_USER=document_service
DOCUMENT_MINIO_PASSWORD=$DOCUMENT_MINIO_PASSWORD
MAX_DOCUMENT_BYTES=52428800
MAX_DOCUMENT_PAGES=500
MAX_PAGES_PER_READ=8
MAX_PAGES_PER_VIEW=4
MAX_PAGE_RESPONSE_CHARS=12000
MAX_READ_RESPONSE_CHARS=48000
OCR_MIN_CHARS=40
DOCUMENT_JOB_MAX_ATTEMPTS=3
EOF
fi

if ! grep -q '^DOCUMENT_DB_PASSWORD=' "$ENV_FILE"; then
  printf 'DOCUMENT_DB_PASSWORD=%s\n' "$(openssl rand -hex 24)" >> "$ENV_FILE"
fi

if ! grep -q '^DOCUMENT_MINIO_PASSWORD=' "$ENV_FILE"; then
  printf 'DOCUMENT_MINIO_PASSWORD=%s\n' \
    "$(openssl rand -hex 24)" >> "$ENV_FILE"
fi

if ! grep -q '^DOCUMENT_MINIO_USER=' "$ENV_FILE"; then
  printf 'DOCUMENT_MINIO_USER=document_service\n' >> "$ENV_FILE"
fi

if ! grep -q '^DOCUMENT_API_TOKEN=' "$ENV_FILE"; then
  printf '\nDOCUMENT_PORT=8789\nDOCUMENT_API_TOKEN=%s\nMAX_DOCUMENT_BYTES=52428800\nMAX_DOCUMENT_PAGES=500\nMAX_PAGES_PER_READ=8\nMAX_PAGES_PER_VIEW=4\nMAX_PAGE_RESPONSE_CHARS=12000\nMAX_READ_RESPONSE_CHARS=48000\nOCR_MIN_CHARS=40\nDOCUMENT_JOB_MAX_ATTEMPTS=3\n' \
    "$(openssl rand -hex 32)" >> "$ENV_FILE"
fi

set -a
source "$ENV_FILE"
set +a
TOKEN_DIR="$HOME/.config/opencode-memory"
mkdir -p "$TOKEN_DIR"
printf '%s\n' "$DOCUMENT_API_TOKEN" > "$TOKEN_DIR/document-token"
printf 'http://127.0.0.1:%s\n' "${DOCUMENT_PORT:-8789}" > "$TOKEN_DIR/document-url"
printf '%s\n' "${MAX_DOCUMENT_BYTES:-52428800}" > "$TOKEN_DIR/document-max-bytes"
chmod 700 "$TOKEN_DIR"
chmod 600 "$TOKEN_DIR/document-token" "$TOKEN_DIR/document-url" "$TOKEN_DIR/document-max-bytes"

mkdir -p \
  "$ROOT/data/outbox/pending" \
  "$ROOT/data/outbox/processing" \
  "$ROOT/data/outbox/retry" \
  "$ROOT/data/outbox/archive" \
  "$ROOT/data/outbox/failed" \
  "$ROOT/data/postgres" \
  "$ROOT/data/minio" \
  "$ROOT/data/document-cache" \
  "$ROOT/backups"
chmod 700 "$ROOT/data" "$ROOT/data/outbox" "$ROOT/data/outbox"/* "$ROOT/backups"

npm --prefix "$ROOT" install --ignore-scripts --no-audit --no-fund --loglevel=error >/dev/null

echo "Local configuration and protected data directories are ready."
