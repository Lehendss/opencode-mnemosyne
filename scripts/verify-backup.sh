#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE="${1:?Usage: verify-backup.sh <backup.tar.zst>}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

sha256sum -c "$ARCHIVE.sha256"
zstd -q -d -c "$ARCHIVE" | tar -C "$WORK" -xf -
BACKUP_DIR="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
(
  cd "$BACKUP_DIR"
  sha256sum -c SHA256SUMS
)
[[ "$(sqlite3 "$BACKUP_DIR/opencode.sqlite" "PRAGMA quick_check;")" == "ok" ]]
pg_restore --list "$BACKUP_DIR/postgres.dump" >/dev/null
tar -tf "$BACKUP_DIR/outbox.tar" >/dev/null
tar -tf "$BACKUP_DIR/config-source.tar" >/dev/null
[[ -s "$BACKUP_DIR/opencode-config/opencode.json" ]]
[[ -s "$BACKUP_DIR/opencode-config/MEMORY.md" ]]

echo "Backup components verified successfully."
