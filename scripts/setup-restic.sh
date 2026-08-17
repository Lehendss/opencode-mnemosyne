#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="${1:-/Volumes/1TB-SSD/opencode-memory-restic}"
CONFIG_DIR="$HOME/.config/opencode-memory"
PASSWORD_FILE="$CONFIG_DIR/restic-password"
ENV_FILE="$CONFIG_DIR/restic.env"

if [[ ! -d "$(dirname "$REPOSITORY")" ]]; then
  echo "Backup volume is not mounted: $(dirname "$REPOSITORY")" >&2
  exit 1
fi

umask 077
mkdir -p "$CONFIG_DIR" "$REPOSITORY"
if [[ ! -f "$PASSWORD_FILE" ]]; then
  openssl rand -base64 48 > "$PASSWORD_FILE"
fi
cat > "$ENV_FILE" <<EOF
export RESTIC_REPOSITORY=$REPOSITORY
export RESTIC_PASSWORD_FILE=$PASSWORD_FILE
EOF
chmod 600 "$PASSWORD_FILE" "$ENV_FILE"

source "$ENV_FILE"
if ! restic snapshots >/dev/null 2>&1; then
  restic init
fi

echo "Encrypted Restic repository ready at $REPOSITORY"
