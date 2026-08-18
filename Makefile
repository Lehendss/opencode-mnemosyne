.PHONY: setup up down status logs test smoke document-smoke reindex purge-noise backup restore-test

setup:
	./scripts/setup.sh

up:
	docker compose up -d --build

down:
	docker compose down

status:
	docker compose ps

logs:
	docker compose logs -f worker mcp documents document-worker

test:
	node --test plugin/index.test.js
	PYTHONPATH=worker python3 -m unittest discover -s worker/tests -v
	python3 -m compileall -q documents/document_service

smoke:
	./scripts/smoke-test.sh

document-smoke:
	./scripts/document-smoke-test.sh

reindex:
	./scripts/reindex-memory.sh

purge-noise:
	./scripts/purge-noise.sh

backup:
	./scripts/backup.sh

restore-test:
	./scripts/restore-test.sh "$$(ls -t backups/opencode-memory-*.tar.zst | head -n 1)"
