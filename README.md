# OpenCode Memory

Local-first durable memory and Document RAG for OpenCode. It captures session events without depending on Docker, preserves private PDF originals in MinIO, maintains searchable memories and document pages in PostgreSQL/pgvector, and prevents raw PDFs from inflating OpenCode's SQLite database or model context.

## Data flow

```text
OpenCode plugin -> atomic filesystem outbox -> Python worker
                                            -> MinIO sanitized raw events
                                            -> PostgreSQL + pgvector
                                             -> read-only MCP -> OpenCode

Local PDF -> authenticated document API -> MinIO original
                                      -> durable PostgreSQL job -> extraction/OCR worker
                                                                -> page embeddings + FTS
OpenCode PDF tools --------------------------------------------> selective text or page images
```

The plugin never connects to PostgreSQL or MinIO. If the stack is unavailable, events remain in `data/outbox/pending` and are processed when services return. Embeddings are generated in bounded batches, and transient failures move to `data/outbox/retry` so one large event cannot block the queue.

## Installed integration

The global OpenCode configuration loads:

- Plugin: `file:///path/to/opencode-memory/plugin/index.js`
- MCP: `http://127.0.0.1:8787/mcp`

OpenCode must be restarted after plugin or configuration changes.

Replace `/path/to/opencode-memory` in the examples and LaunchAgent template with the absolute path of your clone.

Normal startup captures only live events. Set `OPENCODE_MEMORY_RECONCILE_ON_START=1` for one launch only when importing pre-existing OpenCode sessions; leaving it enabled makes every startup fetch every historical conversation.

The global instruction file `~/.config/opencode/MEMORY.md` directs agents to search durable memory at the start of non-trivial tasks, verify retrieved history against current code, and cite source identifiers.

## Components

- PostgreSQL 16 with `pgvector`, FTS and trigram search.
- MinIO private bucket for compressed sanitized event envelopes.
- Worker with at-least-once delivery and idempotent database upserts.
- Ollama `bge-m3` multilingual embeddings, generated locally.
- Document RAG API and durable worker with Poppler extraction and Tesseract OCR in Spanish and English.
- Streamable HTTP MCP server using a database role that is read-only by policy and grants.
- Restic encrypted backup on `/Volumes/1TB-SSD/opencode-memory-restic`.
- macOS LaunchAgent backup every 24 hours.

## Operations

```bash
cd ~/opencode-memory
make status
make logs
make test
make smoke
make document-smoke
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

## Document RAG

PDFs are ingested once and deduplicated by SHA-256. The intact original is stored under the private `documents/` MinIO prefix; PostgreSQL stores project access, processing jobs, page text, extraction provenance and embeddings. Jobs survive restarts, retry with bounded backoff and reclaim stale work.

The OpenCode plugin exposes:

- `pdf_ingest`: submit a local PDF and return its durable document ID.
- `pdf_search`: hybrid semantic, full-text, filename and title search across the current project.
- `pdf_read_pages`: return selected page text, capped at 8 pages and 48,000 characters per call.
- `pdf_view_pages`: render at most 4 selected pages to protected local JPEG files for visual inspection.
- `pdf_get`: inspect status and metadata or recover the byte-identical original.

For a new document, call `pdf_ingest`, poll `pdf_get` until `status` is `ready`, search, and read or view only the relevant pages. Multi-document questions use one `pdf_search` across every indexed PDF associated with the current project.

The `tool.execute.before` guard rejects built-in `read` calls for `.pdf` files and files carrying the PDF signature, preventing base64 PDF attachments from entering the session database. For a misnamed file, the guard reads only the five-byte signature and never uploads content. It directs the agent to `pdf_ingest`, which uses OpenCode's normal read permission prompt before accessing the full file. Text PDFs use layout-preserving extraction; pages without a usable text layer are rendered and processed with local OCR.

Document endpoints listen only on `127.0.0.1:8789`, require a random bearer token stored at `~/.config/opencode-memory/document-token`, and filter access by the current project. This is a single-user local boundary; the project hash is not a separate authentication credential. API, migration and extraction run as separate processes. Runtime containers use a non-root user, read-only filesystem, dropped capabilities and resource limits. Their PostgreSQL role cannot read conversation memories, and their MinIO user can access only `documents/*`; owner credentials exist only in one-shot initialization and migration containers.

Run the complete text, OCR, search, render, deduplication, authorization and original-recovery acceptance test with:

```bash
make document-smoke
```

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
- PDF tool responses have page, character and render limits; original bytes never enter memory events.

Redaction is defense in depth, not a guarantee. Do not intentionally ask an agent to expose secrets. Raw events are sanitized originals, not byte-for-byte originals.

## Backups

Each backup contains:

- A consistent SQLite Online Backup of OpenCode.
- PostgreSQL custom-format dump.
- MinIO raw event objects and intact PDF originals.
- Pending, processing, retry, failed and archived outbox files.
- Platform source and sanitized OpenCode integration configuration.
- Manifest and SHA-256 checksums.

Local archives stay under `backups/` for 30 days. Restic retains 30 daily, 12 weekly and 12 monthly snapshots on the external SSD. `restore-test.sh` restores PostgreSQL into a temporary database, validates SQLite session counts, checks compressed event objects, verifies each PDF against the SHA-256 in its object key and confirms every database object reference is present.

The Restic password is stored at `~/.config/opencode-memory/restic-password`. Store a copy in a password manager. Losing both the Mac and that password makes the encrypted external backup unrecoverable.

## Recovery boundaries

- Raw events are the reconstructable source of truth.
- PostgreSQL memories and embeddings are derived and may be rebuilt.
- Deletion events create tombstones in searchable memory.
- External Restic retention means deleted content remains in historical encrypted snapshots until those snapshots expire.
- PDF originals in MinIO and document access metadata in PostgreSQL are sources of truth.
- Extracted text, OCR output and embeddings are reproducible derived data.
- Document deletion is intentionally not exposed by the agent tools; retention remains governed by local and encrypted Restic backups.
