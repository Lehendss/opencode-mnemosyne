#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_WAS_RUNNING=false

if [[ "$(docker compose --project-directory "$ROOT" ps --status running --services worker)" == "worker" ]]; then
  WORKER_WAS_RUNNING=true
  docker compose --project-directory "$ROOT" stop worker
fi

restart_worker() {
  if [[ "$WORKER_WAS_RUNNING" == true ]]; then
    docker compose --project-directory "$ROOT" start worker
  fi
}
trap restart_worker EXIT

docker compose --project-directory "$ROOT" build worker
docker compose --project-directory "$ROOT" run --rm --no-deps worker \
  python -m memory_worker.purge_noise
