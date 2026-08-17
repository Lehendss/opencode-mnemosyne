#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE="$(cd "$(dirname "${1:?Usage: restore-test.sh <backup.tar.zst>}")" && pwd)/$(basename "$1")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
WORK="$(mktemp -d)"
TEST_DB="opencode_memory_restore_test_$$"

cleanup() {
  docker compose --project-directory "$ROOT" exec -T postgres \
    dropdb --if-exists --force --username "$POSTGRES_USER" "$TEST_DB" >/dev/null 2>&1 || true
  rm -rf "$WORK"
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
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('ingestion_event', 'session_record', 'memory');")"
[[ "$TABLE_COUNT" == "3" ]]

EXPECTED_SESSIONS="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_count"])' "$BACKUP_DIR/manifest.json")"
RESTORED_SESSIONS="$(sqlite3 "$BACKUP_DIR/opencode.sqlite" "SELECT COUNT(*) FROM session;")"
[[ "$EXPECTED_SESSIONS" == "$RESTORED_SESSIONS" ]]

while IFS= read -r object; do
  gzip -t "$object"
done < <(find "$BACKUP_DIR/minio" -type f -name '*.gz')

echo "Full restore test passed using temporary database $TEST_DB."
