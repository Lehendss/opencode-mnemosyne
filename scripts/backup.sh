#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
RESTIC_ENV="$HOME/.config/opencode-memory/restic.env"
if [[ -f "$RESTIC_ENV" ]]; then
  source "$RESTIC_ENV"
fi
BACKUP_ROOT="$ROOT/backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DESTINATION="$BACKUP_ROOT/$STAMP"
LOCK_DIR="$BACKUP_ROOT/.backup.lock"

umask 077
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another backup is running." >&2
  exit 0
fi
trap 'rm -rf "$LOCK_DIR" "$DESTINATION.tmp"' EXIT

mkdir -p "$DESTINATION.tmp/minio"

OPENCODE_DB="$(opencode db path)"
sqlite3 "$OPENCODE_DB" ".timeout 10000" ".backup '$DESTINATION.tmp/opencode.sqlite'"
[[ "$(sqlite3 "$DESTINATION.tmp/opencode.sqlite" "PRAGMA quick_check;")" == "ok" ]]

docker compose --project-directory "$ROOT" exec -T postgres \
  pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom \
  > "$DESTINATION.tmp/postgres.dump"

docker compose --project-directory "$ROOT" run --rm \
  --entrypoint /bin/sh \
  -v "$DESTINATION.tmp/minio:/backup" \
  minio-init -c \
  'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite "local/$MINIO_BUCKET" /backup'

tar -C "$ROOT/data" -cf "$DESTINATION.tmp/outbox.tar" outbox
tar -C "$ROOT" --exclude='*/__pycache__' --exclude='*.pyc' -cf "$DESTINATION.tmp/config-source.tar" \
  .env.example .gitignore Makefile README.md compose.yaml \
  database launchd mcp plugin scripts worker
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
  "opencode_version": "$(opencode --version)",
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
tar -C "$BACKUP_ROOT" -cf - "$STAMP" | zstd -q -T0 -o "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst"
rm -rf "$DESTINATION"
sha256sum "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst" > "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst.sha256"

find "$BACKUP_ROOT" -type f -name 'opencode-memory-*.tar.zst' -mtime +30 -delete
find "$BACKUP_ROOT" -type f -name 'opencode-memory-*.tar.zst.sha256' -mtime +30 -delete

if command -v restic >/dev/null 2>&1 && [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  restic backup --tag opencode-memory "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst" \
    "$BACKUP_ROOT/opencode-memory-$STAMP.tar.zst.sha256"
  restic forget --tag opencode-memory --group-by host,tags \
    --keep-daily 30 --keep-weekly 12 --keep-monthly 12 --prune
fi

echo "Verified backup: $BACKUP_ROOT/opencode-memory-$STAMP.tar.zst"
