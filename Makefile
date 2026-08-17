.PHONY: setup up down status logs test smoke reindex purge-noise backup restore-test

setup:
	./scripts/setup.sh

up:
	docker compose up -d --build

down:
	docker compose down

status:
	docker compose ps

logs:
	docker compose logs -f worker mcp

test:
	node --test plugin/index.test.js
	PYTHONPATH=worker python3 -m unittest discover -s worker/tests -v

smoke:
	./scripts/smoke-test.sh

reindex:
	./scripts/reindex-memory.sh

purge-noise:
	./scripts/purge-noise.sh

backup:
	./scripts/backup.sh

restore-test:
	./scripts/restore-test.sh "$$(ls -t backups/opencode-memory-*.tar.zst | head -n 1)"
