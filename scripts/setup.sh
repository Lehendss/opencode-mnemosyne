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
OUTBOX_HOST_PATH=./data/outbox
BACKUP_ROOT=./backups
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
OUTBOX_HOST_PATH="${OUTBOX_HOST_PATH:-./data/outbox}"
if [[ "$OUTBOX_HOST_PATH" != /* ]]; then
  OUTBOX_HOST_PATH="$ROOT/${OUTBOX_HOST_PATH#./}"
fi
BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
if [[ "$BACKUP_ROOT" != /* ]]; then
  BACKUP_ROOT="$ROOT/${BACKUP_ROOT#./}"
fi
mkdir -p "$ROOT/data"

canonicalize_storage_path() {
  local name="$1"
  local path="${2%/}"
  local parent base
  if [[ -d "$path" ]]; then
    (cd -P "$path" && pwd)
    return
  fi
  parent="$(dirname "$path")"
  base="$(basename "$path")"
  if [[ ! -d "$parent" || "$base" == -* ]]; then
    echo "$name requires an existing parent directory and a safe basename: $path" >&2
    exit 1
  fi
  parent="$(cd -P "$parent" && pwd)"
  printf '%s/%s\n' "$parent" "$base"
}

OUTBOX_HOST_PATH="$(canonicalize_storage_path OUTBOX_HOST_PATH "$OUTBOX_HOST_PATH")"
BACKUP_ROOT="$(canonicalize_storage_path BACKUP_ROOT "$BACKUP_ROOT")"

validate_storage_path() {
  local name="$1"
  local path="${2%/}"
  local relative volume_root
  if [[ -z "$path" || "$path" == "/" || "$path" == "$ROOT" || "$path" == "$HOME" ]]; then
    echo "$name points to an unsafe directory: $path" >&2
    exit 1
  fi
  if [[ "$path" == /Volumes/* ]]; then
    relative="${path#/Volumes/}"
    volume_root="/Volumes/${relative%%/*}"
    if [[ "$path" == "$volume_root" ]] || ! /usr/sbin/diskutil info "$volume_root" >/dev/null 2>&1; then
      echo "$name requires a mounted external volume and a directory below its root: $path" >&2
      exit 1
    fi
  fi
}

validate_storage_path OUTBOX_HOST_PATH "$OUTBOX_HOST_PATH"
validate_storage_path BACKUP_ROOT "$BACKUP_ROOT"
if [[ "$(basename "$OUTBOX_HOST_PATH")" != "outbox" ]]; then
  echo "OUTBOX_HOST_PATH must end in /outbox to keep backups portable." >&2
  exit 1
fi
if [[ "$OUTBOX_HOST_PATH" == "$BACKUP_ROOT" ||
      "$OUTBOX_HOST_PATH" == "$BACKUP_ROOT"/* ||
      "$BACKUP_ROOT" == "$OUTBOX_HOST_PATH"/* ]]; then
  echo "OUTBOX_HOST_PATH and BACKUP_ROOT must not overlap." >&2
  exit 1
fi
TOKEN_DIR="$HOME/.config/opencode-memory"
mkdir -p "$TOKEN_DIR"
printf '%s\n' "$DOCUMENT_API_TOKEN" > "$TOKEN_DIR/document-token"
printf 'http://127.0.0.1:%s\n' "${DOCUMENT_PORT:-8789}" > "$TOKEN_DIR/document-url"
printf '%s\n' "${MAX_DOCUMENT_BYTES:-52428800}" > "$TOKEN_DIR/document-max-bytes"
chmod 700 "$TOKEN_DIR"
chmod 600 "$TOKEN_DIR/document-token" "$TOKEN_DIR/document-url" "$TOKEN_DIR/document-max-bytes"

mkdir -p \
  "$OUTBOX_HOST_PATH/pending" \
  "$OUTBOX_HOST_PATH/processing" \
  "$OUTBOX_HOST_PATH/retry" \
  "$OUTBOX_HOST_PATH/archive" \
  "$OUTBOX_HOST_PATH/failed" \
  "$ROOT/data/postgres" \
  "$ROOT/data/minio" \
  "$ROOT/data/document-cache" \
  "$BACKUP_ROOT"
chmod 700 "$ROOT/data" "$OUTBOX_HOST_PATH" "$OUTBOX_HOST_PATH"/* "$BACKUP_ROOT"

npm --prefix "$ROOT" install --ignore-scripts --no-audit --no-fund --loglevel=error >/dev/null

echo "Local configuration and protected data directories are ready."
