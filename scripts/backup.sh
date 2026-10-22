#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
bash "$ROOT/scripts/check-storage.sh"
RESTIC_ENV="$HOME/.config/opencode-memory/restic.env"
if [[ -f "$RESTIC_ENV" ]]; then
  source "$RESTIC_ENV"
fi
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT/backups}"
if [[ "$BACKUP_ROOT" != /* ]]; then
  BACKUP_ROOT="$ROOT/${BACKUP_ROOT#./}"
fi
OUTBOX_SOURCE="${OUTBOX_HOST_PATH:-./data/outbox}"
if [[ "$OUTBOX_SOURCE" != /* ]]; then
  OUTBOX_SOURCE="$ROOT/${OUTBOX_SOURCE#./}"
fi
BACKUP_ROOT="${BACKUP_ROOT%/}"
OUTBOX_SOURCE="${OUTBOX_SOURCE%/}"

canonicalize_existing_directory() {
  local name="$1"
  local path="$2"
  if [[ ! -d "$path" ]]; then
    echo "$name directory does not exist: $path" >&2
    exit 1
  fi
  (cd -P "$path" && pwd)
}

BACKUP_ROOT="$(canonicalize_existing_directory BACKUP_ROOT "$BACKUP_ROOT")"
OUTBOX_SOURCE="$(canonicalize_existing_directory OUTBOX_HOST_PATH "$OUTBOX_SOURCE")"

validate_storage_path() {
  local name="$1"
  local path="$2"
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

validate_storage_path OUTBOX_HOST_PATH "$OUTBOX_SOURCE"
validate_storage_path BACKUP_ROOT "$BACKUP_ROOT"
if [[ "$(basename "$OUTBOX_SOURCE")" != "outbox" ]]; then
  echo "OUTBOX_HOST_PATH must end in /outbox to keep backups portable." >&2
  exit 1
fi
if [[ "$OUTBOX_SOURCE" == "$BACKUP_ROOT" ||
      "$OUTBOX_SOURCE" == "$BACKUP_ROOT"/* ||
      "$BACKUP_ROOT" == "$OUTBOX_SOURCE"/* ]]; then
  echo "OUTBOX_HOST_PATH and BACKUP_ROOT must not overlap." >&2
  exit 1
fi
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DESTINATION="$BACKUP_ROOT/$STAMP"
LOCK_DIR="$BACKUP_ROOT/.backup.lock"

umask 077
if ! mkdir "$LOCK_DIR"; then
  if [[ -d "$LOCK_DIR" ]]; then
    echo "Another backup is running." >&2
    exit 0
  fi
  echo "Cannot create backup lock directory: $LOCK_DIR" >&2
  exit 1
fi
PAUSED_SERVICES=()
restart_worker() {
  if [[ ${#PAUSED_SERVICES[@]} -gt 0 ]]; then
    docker compose --project-directory "$ROOT" start "${PAUSED_SERVICES[@]}" >/dev/null
    PAUSED_SERVICES=()
  fi
}
cleanup() {
  rm -rf "$LOCK_DIR" "$DESTINATION.tmp"
  restart_worker
}
trap cleanup EXIT

mkdir -p "$DESTINATION.tmp/minio"

RUNNING_SERVICES="$(docker compose --project-directory "$ROOT" ps --status running --services worker documents document-worker)"
while IFS= read -r service; do
  if [[ -n "$service" ]]; then
    PAUSED_SERVICES+=("$service")
  fi
done <<< "$RUNNING_SERVICES"
if [[ ${#PAUSED_SERVICES[@]} -gt 0 ]]; then
  docker compose --project-directory "$ROOT" stop "${PAUSED_SERVICES[@]}" >/dev/null
fi

OPENCODE_BIN="${OPENCODE_BIN:-$HOME/.opencode/bin/opencode}"
if [[ ! -x "$OPENCODE_BIN" ]]; then
  OPENCODE_BIN="$(command -v opencode)"
fi
OPENCODE_DB="$("$OPENCODE_BIN" db path)"
sqlite3 "$OPENCODE_DB" ".timeout 10000" ".backup '$DESTINATION.tmp/opencode.sqlite'"
[[ "$(sqlite3 "$DESTINATION.tmp/opencode.sqlite" "PRAGMA quick_check;")" == "ok" ]]

docker compose --project-directory "$ROOT" exec -T postgres \
  pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom \
  > "$DESTINATION.tmp/postgres.dump"

docker compose --project-directory "$ROOT" run --rm \
  --entrypoint /bin/sh \
  -v "$DESTINATION.tmp/minio:/backup" \
  minio-init -c \
  'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc --quiet mirror --overwrite "local/$MINIO_BUCKET" /backup'

[[ -d "$OUTBOX_SOURCE" ]]
tar -C "$(dirname "$OUTBOX_SOURCE")" -cf "$DESTINATION.tmp/outbox.tar" -- "$(basename "$OUTBOX_SOURCE")"
tar -C "$ROOT" --exclude='*/__pycache__' --exclude='*.pyc' -cf "$DESTINATION.tmp/config-source.tar" \
  .env.example .gitignore Makefile README.md compose.yaml package.json package-lock.json \
  database documents launchd mcp plugin scripts worker
mkdir -p "$DESTINATION.tmp/opencode-config"
python3 "$ROOT/scripts/sanitize-config.py" \
  "$HOME/.config/opencode/opencode.json" \
  "$DESTINATION.tmp/opencode-config/opencode.json"
cp "$HOME/.config/opencode/MEMORY.md" "$DESTINATION.tmp/opencode-config/MEMORY.md"

SESSION_COUNT="$(sqlite3 "$DESTINATION.tmp/opencode.sqlite" "SELECT COUNT(*) FROM session;")"
LATEST_SESSION="$(sqlite3 "$DESTINATION.tmp/opencode.sqlite" "SELECT id FROM session ORDER BY time_updated DESC LIMIT 1;")"
cat > "$DESTINATION.tmp/manifest.json" <<EOF
{
  "backup_timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "opencode_version": "$("$OPENCODE_BIN" --version)",
  "session_count": $SESSION_COUNT,
  "latest_session_id": "$LATEST_SESSION",
  "sqlite_quick_check": "ok"
}
EOF

(
  cd "$DESTINATION.tmp"
  sha256sum opencode.sqlite postgres.dump outbox.tar config-source.tar \
    opencode-config/opencode.json opencode-config/MEMORY.md manifest.json > SHA256SUMS
)
mv "$DESTINATION.tmp" "$DESTINATION"
restart_worker
tar -C "$BACKUP_ROOT" -cf - "$STAMP" | zstd -q -T0 -o "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst"
rm -rf "$DESTINATION"
(
  cd "$BACKUP_ROOT"
  sha256sum "opencode-memory-$STAMP.tar.zst" > "opencode-memory-$STAMP.tar.zst.sha256"
)

find "$BACKUP_ROOT" -type f -name 'opencode-memory-*.tar.zst' -mtime +30 -delete
find "$BACKUP_ROOT" -type f -name 'opencode-memory-*.tar.zst.sha256' -mtime +30 -delete

if command -v restic >/dev/null 2>&1 && [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  restic backup --tag opencode-memory "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst" \
    "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst.sha256"
  restic forget --tag opencode-memory --group-by host,tags \
    --keep-daily 30 --keep-weekly 12 --keep-monthly 12 --prune
fi

echo "Verified backup: $BACKUP_ROOT/opencode-memory-$STAMP.tar.zst"
