#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT/backups}"
if [[ "$BACKUP_ROOT" != /* ]]; then
  BACKUP_ROOT="$ROOT/${BACKUP_ROOT#./}"
fi
if [[ $# -gt 0 ]]; then
  ARCHIVE="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
else
  shopt -s nullglob
  archives=("$BACKUP_ROOT"/opencode-memory-*.tar.zst)
  [[ ${#archives[@]} -gt 0 ]] || { echo "No backup archives found in $BACKUP_ROOT" >&2; exit 1; }
  ARCHIVE="${archives[${#archives[@]}-1]}"
fi
TMP_ROOT="${RESTORE_TMPDIR:-$(dirname "$ARCHIVE")/.tmp}"
mkdir -p "$TMP_ROOT"
WORK="$(mktemp -d "$TMP_ROOT/opencode-memory-restore.XXXXXX")"
export RESTORE_TMPDIR="$TMP_ROOT"
TEST_DB="opencode_memory_restore_test_$$"

cleanup() {
  rm -rf "$WORK"
  docker compose --project-directory "$ROOT" exec -T postgres \
    dropdb --if-exists --force --username "$POSTGRES_USER" "$TEST_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$ROOT/scripts/verify-backup.sh" "$ARCHIVE"
zstd -q -d -c "$ARCHIVE" | tar -C "$WORK" -xf -
BACKUP_DIR="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

docker compose --project-directory "$ROOT" exec -T postgres \
  createdb --username "$POSTGRES_USER" "$TEST_DB"
docker compose --project-directory "$ROOT" exec -T postgres \
  pg_restore --username "$POSTGRES_USER" --dbname "$TEST_DB" \
  < "$BACKUP_DIR/postgres.dump"

TABLE_COUNT="$(docker compose --project-directory "$ROOT" exec -T postgres \
  psql --username "$POSTGRES_USER" --dbname "$TEST_DB" -Atc \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('ingestion_event', 'session_record', 'memory', 'document', 'document_project', 'document_page', 'document_job');")"
[[ "$TABLE_COUNT" == "7" ]]

EXPECTED_SESSIONS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_count"])' "$BACKUP_DIR/manifest.json")"
RESTORED_SESSIONS="$(sqlite3 "$BACKUP_DIR/opencode.sqlite" "SELECT COUNT(*) FROM session;")"
[[ "$EXPECTED_SESSIONS" == "$RESTORED_SESSIONS" ]]

while IFS= read -r object; do
  gzip -t "$object"
done < <(find "$BACKUP_DIR/minio" -type f -name '*.gz')

while IFS= read -r object; do
  expected="$(basename "$object" .pdf)"
  actual="$(sha256sum "$object" | cut -d' ' -f1)"
  [[ "$expected" == "$actual" ]]
done < <(find "$BACKUP_DIR/minio/documents" -type f -name '*.pdf' 2>/dev/null)

while IFS= read -r object_key; do
  [[ -f "$BACKUP_DIR/minio/$object_key" ]]
done < <(docker compose --project-directory "$ROOT" exec -T postgres \
  psql --username "$POSTGRES_USER" --dbname "$TEST_DB" -Atc "SELECT object_key FROM document;")

echo "Full restore test passed using temporary database $TEST_DB."
