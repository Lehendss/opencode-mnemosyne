#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
OUTBOX_SOURCE="${OUTBOX_HOST_PATH:-$ROOT/data/outbox}"
if [[ "$OUTBOX_SOURCE" != /* ]]; then
  OUTBOX_SOURCE="$ROOT/${OUTBOX_SOURCE#./}"
fi
python3 "$ROOT/scripts/check-storage.py" \
  PostgreSQL "$ROOT/data/postgres" \
  MinIO "$ROOT/data/minio" \
  Outbox "$OUTBOX_SOURCE"
