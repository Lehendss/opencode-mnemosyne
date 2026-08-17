#!/bin/bash
set -e

trap 'kill 0' TERM INT

echo "Starting OpenCode Memory services..."

python -m uvicorn memory_mcp.webui:app --host 0.0.0.0 --port 8788 --log-level warning &
python -m memory_mcp.server &

wait
