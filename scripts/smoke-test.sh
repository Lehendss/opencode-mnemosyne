#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
EVENT_ID="smoke-$(date +%s)-$RANDOM"
PROJECT_ID="$(printf 'smoke-project' | sha256sum | cut -d' ' -f1)"
PENDING="$ROOT/data/outbox/pending/$EVENT_ID.json"

cat > "$PENDING.tmp" <<EOF
{"schema_version":1,"event_id":"$EVENT_ID","event_type":"memory.session.snapshot","occurred_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","captured_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","project_id":"$PROJECT_ID","project_label":"smoke-project","session_id":"smoke-session","message_id":"smoke-assistant","plugin_version":"smoke","payload_sha256":"smoke","payload":{"session":{"id":"smoke-session","title":"Fix duplicate invoices"},"messages":[{"info":{"id":"smoke-user","role":"user"},"parts":[{"id":"smoke-user-part","messageID":"smoke-user","type":"text","text":"Prefiero soluciones verificadas. Corrige el bug de facturas duplicadas."}]},{"info":{"id":"smoke-assistant","parentID":"smoke-user","role":"assistant"},"parts":[{"id":"smoke-tool","messageID":"smoke-assistant","type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"make test"},"output":"7 tests passed"}},{"id":"smoke-answer","messageID":"smoke-assistant","type":"text","text":"La solucion funciona: se agrego una clave idempotente y las pruebas pasaron."}]}]}}
EOF
mv "$PENDING.tmp" "$PENDING"

for _ in {1..90}; do
  COUNT="$(docker compose --project-directory "$ROOT" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM memory WHERE source_id = 'smoke-user' AND kind = 'bug_resolution' AND importance >= 0.9;")"
  if [[ "$COUNT" == "1" ]]; then
    break
  fi
  sleep 2
done

[[ "${COUNT:-0}" == "1" ]]

for _ in {1..30}; do
  if [[ ! -e "$PENDING" && ! -e "$ROOT/data/outbox/processing/$EVENT_ID.json" ]]; then
    break
  fi
  sleep 1
done

[[ ! -e "$PENDING" ]]
[[ ! -e "$ROOT/data/outbox/processing/$EVENT_ID.json" ]]
ARCHIVED_COUNT="$(find "$ROOT/data/outbox/archive" -type f -name "$EVENT_ID.json" | wc -l | tr -d ' ')"
[[ "$ARCHIVED_COUNT" == "1" ]]

docker compose --project-directory "$ROOT" exec -T postgres psql \
  -U memory_reader -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM memory WHERE source_id = 'smoke-user' AND kind = 'bug_resolution';" \
  | grep -qx 1

if docker compose --project-directory "$ROOT" exec -T postgres psql \
  -U memory_reader -d "$POSTGRES_DB" -c "DELETE FROM memory WHERE session_id = 'smoke-session';" >/dev/null 2>&1; then
  echo "Read-only role unexpectedly accepted a write." >&2
  exit 1
fi

curl -fsS -o /dev/null -X POST "http://127.0.0.1:${MCP_PORT}/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}'

docker compose --project-directory "$ROOT" exec -T \
  -e MCP_ACCEPTANCE_SMOKE=1 mcp python -m memory_mcp.acceptance

echo "End-to-end smoke test passed."
