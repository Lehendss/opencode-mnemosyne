#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE="${1:?Usage: verify-backup.sh <backup.tar.zst>}"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"
TMP_ROOT="${RESTORE_TMPDIR:-$(dirname "$ARCHIVE")/.tmp}"
mkdir -p "$TMP_ROOT"
WORK="$(mktemp -d "$TMP_ROOT/opencode-memory-verify.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

EXPECTED_HASH="$(cut -d' ' -f1 "$ARCHIVE.sha256")"
ACTUAL_HASH="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
[[ "$EXPECTED_HASH" == "$ACTUAL_HASH" ]]
echo "$ARCHIVE: OK"
zstd -q -d -c "$ARCHIVE" | tar -C "$WORK" -xf -
BACKUP_DIR="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
(
  cd "$BACKUP_DIR"
  sha256sum -c SHA256SUMS
)
[[ "$(sqlite3 "$BACKUP_DIR/opencode.sqlite" "PRAGMA quick_check;")" == "ok" ]]
pg_restore --list "$BACKUP_DIR/postgres.dump" >/dev/null
tar -tf "$BACKUP_DIR/outbox.tar" > "$WORK/outbox.list"
for directory in pending processing retry archive failed; do
  grep -Eq "^outbox/$directory/?$" "$WORK/outbox.list"
done
tar -tf "$BACKUP_DIR/config-source.tar" >/dev/null
[[ -s "$BACKUP_DIR/opencode-config/opencode.json" ]]
[[ -s "$BACKUP_DIR/opencode-config/MEMORY.md" ]]

echo "Backup components verified successfully."
