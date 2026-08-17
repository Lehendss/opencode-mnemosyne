#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

umask 077
if [[ ! -f "$ENV_FILE" ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 24)"
  READER_PASSWORD="$(openssl rand -hex 24)"
  MINIO_PASSWORD="$(openssl rand -hex 24)"
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
OUTBOX_ROOT=/data/outbox
ARCHIVE_RETENTION_DAYS=30
MAX_MEMORY_CHARS=24000
MCP_PORT=8787
EOF
fi

mkdir -p \
  "$ROOT/data/outbox/pending" \
  "$ROOT/data/outbox/processing" \
  "$ROOT/data/outbox/archive" \
  "$ROOT/data/outbox/failed" \
  "$ROOT/data/postgres" \
  "$ROOT/data/minio" \
  "$ROOT/backups"
chmod 700 "$ROOT/data" "$ROOT/data/outbox" "$ROOT/data/outbox"/* "$ROOT/backups"

echo "Local configuration and protected data directories are ready."
