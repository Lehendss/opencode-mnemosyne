# OpenCode Memory

Local-first durable memory for OpenCode. It captures session events without depending on Docker, stores sanitized originals in MinIO, maintains searchable memories in PostgreSQL/pgvector, and exposes read-only MCP tools to OpenCode.

## Data flow

```text
OpenCode plugin -> atomic filesystem outbox -> Python worker
                                            -> MinIO sanitized raw events
                                            -> PostgreSQL + pgvector
                                            -> read-only MCP -> OpenCode
```

The plugin never connects to PostgreSQL or MinIO. If the stack is unavailable, events remain in `data/outbox/pending` and are processed when services return.

## Installed integration

The global OpenCode configuration loads:

- Plugin: `file:///Users/lehends/opencode-memory/plugin/index.js`
- MCP: `http://127.0.0.1:8787/mcp`

OpenCode must be restarted after plugin or configuration changes.

The global instruction file `~/.config/opencode/MEMORY.md` directs agents to search durable memory at the start of non-trivial tasks, verify retrieved history against current code, and cite source identifiers.

## Components

- PostgreSQL 16 with `pgvector`, FTS and trigram search.
- MinIO private bucket for compressed sanitized event envelopes.
- Worker with at-least-once delivery and idempotent database upserts.
- Ollama `bge-m3` multilingual embeddings, generated locally.
- Streamable HTTP MCP server using a database role that is read-only by policy and grants.
- Restic encrypted backup on `/Volumes/1TB-SSD/opencode-memory-restic`.
- macOS LaunchAgent backup every four hours.

## Operations

```bash
cd ~/opencode-memory
make status
make logs
make test
make smoke
make reindex
make purge-noise
make backup
make restore-test
```

Open `http://127.0.0.1:8788` for the web memory browser -- search, filter by kind, and inspect full content and metadata without opening OpenCode.

Start or rebuild:

```bash
docker compose up -d --build
```

Stop services without deleting data:

```bash
docker compose down
```

OpenCode capture continues while containers are stopped.

## MCP tools

- `memory_search`: hybrid semantic, lexical and metadata retrieval.
- `memory_get`: complete memory record with source references.
- `memory_recent`: recent memories by project.
- `memory_get_session_summary`: session metadata and ordered memories.
- `memory_find_procedures`: verified workflows and bug-resolution sequences.
- `memory_find_decisions`: prior technical decisions.
- `memory_find_preferences`: explicit durable user preferences.
- `memory_find_similar_errors`: related incidents and verified resolutions.

Results include project, session, message, kind, timestamp and scores so agents can cite evidence instead of treating retrieved text as ground truth.

## Intelligent memory

Completed session snapshots produce four long-term memory families:

- **Episodic:** prompts, responses, tools, file changes and session summaries.
- **Semantic:** explicit preferences and technical decisions.
- **Procedural:** one consolidated goal, ordered tool sequence and outcome per user task.
- **Working:** OpenCode's active context plus a custom compaction handoff preserving objectives, decisions, identifiers, errors, checks and next steps.

Procedures are marked with confidence and verification status. Verified procedures receive higher importance; partial attempts remain searchable but rank lower. Retrieval combines embeddings, full-text search, title similarity, importance, confidence and recency decay.

Rebuild structured memories from the latest stored snapshot of every session:

```bash
make reindex
```

Remove historical incremental token-stream events that predate plugin `0.2.1`:

```bash
make purge-noise
```

The command pauses and resumes the worker so database, MinIO and outbox cleanup remain consistent.

## Privacy

Capture performs best-effort redaction before writing to disk:

- Secret-like keys and connection strings are redacted.
- JWTs, bearer tokens and common API key formats are redacted.
- Commands that access `.env`, OpenCode `auth.json` or SSH private keys are omitted.
- Model reasoning parts are not retained.
- Home-directory paths are replaced with `~`.
- Strings and derived memories have hard size limits.

Redaction is defense in depth, not a guarantee. Do not intentionally ask an agent to expose secrets. Raw events are sanitized originals, not byte-for-byte originals.

## Backups

Each backup contains:

- A consistent SQLite Online Backup of OpenCode.
- PostgreSQL custom-format dump.
- MinIO raw objects.
- Pending, processing, failed and archived outbox files.
- Platform source and sanitized OpenCode integration configuration.
- Manifest and SHA-256 checksums.

Local archives stay under `backups/` for 30 days. Restic retains 30 daily, 12 weekly and 12 monthly snapshots on the external SSD. `restore-test.sh` restores PostgreSQL into a temporary database, validates SQLite session counts and checks compressed MinIO objects.

The Restic password is stored at `~/.config/opencode-memory/restic-password`. Store a copy in a password manager. Losing both the Mac and that password makes the encrypted external backup unrecoverable.

## Recovery boundaries

- Raw events are the reconstructable source of truth.
- PostgreSQL memories and embeddings are derived and may be rebuilt.
- Deletion events create tombstones in searchable memory.
- External Restic retention means deleted content remains in historical encrypted snapshots until those snapshots expire.

## Known next step

Current extraction is deterministic and auditable. A later optional local-LLM enrichment stage can merge equivalent decisions, identify superseded rules and produce more concise natural-language procedures without changing capture, provenance or backup layers.
