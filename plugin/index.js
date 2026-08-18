import { createHash, randomUUID } from "node:crypto"
import { chmodSync, mkdirSync, openSync, closeSync, fsyncSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"

const PLUGIN_VERSION = "0.2.2"
const SCHEMA_VERSION = 1
const MAX_STRING_CHARS = Number(process.env.OPENCODE_MEMORY_MAX_STRING_CHARS || 200000)
const MEMORY_HOME = resolve(
  process.env.OPENCODE_MEMORY_HOME || join(homedir(), "opencode-memory"),
)
const PENDING_DIR = join(MEMORY_HOME, "data", "outbox", "pending")

const SENSITIVE_KEYS = /^(authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)$/i
const SENSITIVE_PATHS = /(^|\/)(\.env(?:\..*)?|auth\.json|credentials?|id_rsa|id_ed25519|.*\.(?:p12|pem|key))(\/|$)/i
const SECRET_PATTERNS = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g,
  /\b(?:sk|pk|ghp|github_pat|xox[baprs]|AKIA)[-_A-Za-z0-9]{16,}\b/g,
  /\bBearer\s+[-._~+/A-Za-z0-9]+=*\b/gi,
  /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g,
  /\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?):\/\/[^\s"']+\b/gi,
  /\b(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+/gi,
]
const CAPTURED_EVENTS = new Set([
  "session.created",
  "session.updated",
  "session.deleted",
  "session.compacted",
  "session.diff",
  "session.idle",
  "message.updated",
  "message.removed",
  "message.part.removed",
  "permission.asked",
  "permission.replied",
  "todo.updated",
  "command.executed",
  "file.edited",
])

function sha256(value) {
  return createHash("sha256").update(value).digest("hex")
}

function redactString(value) {
  let result = value.replaceAll("\u0000", "").split(homedir()).join("~")
  for (const pattern of SECRET_PATTERNS) result = result.replace(pattern, "[REDACTED]")
  if (result.length > MAX_STRING_CHARS) {
    return `${result.slice(0, MAX_STRING_CHARS)}\n[TRUNCATED ${result.length - MAX_STRING_CHARS} CHARS]`
  }
  return result
}

function sanitize(value, key = "", seen = new WeakSet()) {
  if (SENSITIVE_KEYS.test(key)) return "[REDACTED]"
  if (typeof value === "string") {
    if ((key === "path" || key === "filePath" || key === "filename") && SENSITIVE_PATHS.test(value)) {
      return "[REDACTED SENSITIVE PATH]"
    }
    if (key === "command" && /(?:^|[\s"'])(?:[^\s"']*\/)?(?:\.env(?:\.[^\s"']*)?|auth\.json|id_rsa|id_ed25519)(?:$|[\s"'])/i.test(value)) {
      return "[REDACTED SENSITIVE COMMAND]"
    }
    return redactString(value)
  }
  if (value === null || typeof value !== "object") return value
  if (value.type === "reasoning") {
    return {
      id: value.id,
      sessionID: value.sessionID,
      messageID: value.messageID,
      type: "reasoning",
      redacted: true,
    }
  }
  if (seen.has(value)) return "[CIRCULAR]"
  seen.add(value)
  if (Array.isArray(value)) return value.map((item) => sanitize(item, key, seen))
  const output = {}
  for (const [childKey, childValue] of Object.entries(value)) {
    output[childKey] = sanitize(childValue, childKey, seen)
  }
  return output
}

function eventIdentity(eventType, payload) {
  const props = payload?.properties || payload || {}
  const info = props.info || {}
  const part = props.part || {}
  return {
    session: props.sessionID || props.session?.id || info.sessionID || info.id || part.sessionID || null,
    message: props.messageID || info.id || part.messageID || null,
    part: props.partID || part.id || null,
  }
}

function writeEnvelope({ eventType, payload, project, directory, occurredAt }) {
  mkdirSync(PENDING_DIR, { recursive: true, mode: 0o700 })
  chmodSync(dirname(PENDING_DIR), 0o700)
  chmodSync(PENDING_DIR, 0o700)

  const sanitized = sanitize(payload)
  const serializedPayload = JSON.stringify(sanitized)
  const identity = eventIdentity(eventType, sanitized)
  const projectPath = project?.worktree || directory
  const projectId = sha256(resolve(projectPath))
  const stableKey = JSON.stringify({ eventType, projectId, identity, payload: sanitized })
  const eventId = sha256(stableKey)
  const envelope = {
    schema_version: SCHEMA_VERSION,
    event_id: eventId,
    event_type: eventType,
    occurred_at: new Date(occurredAt || Date.now()).toISOString(),
    captured_at: new Date().toISOString(),
    project_id: projectId,
    project_label: basename(projectPath),
    session_id: identity.session,
    message_id: identity.message,
    plugin_version: PLUGIN_VERSION,
    payload_sha256: sha256(serializedPayload),
    payload: sanitized,
  }
  const body = `${JSON.stringify(envelope)}\n`
  const temporary = join(PENDING_DIR, `.${eventId}.${randomUUID()}.tmp`)
  const destination = join(PENDING_DIR, `${eventId}.json`)
  writeFileSync(temporary, body, { encoding: "utf8", mode: 0o600, flag: "wx" })
  const fd = openSync(temporary, "r")
  try {
    fsyncSync(fd)
  } finally {
    closeSync(fd)
  }
  renameSync(temporary, destination)
}

async function reconcileSession(client, sessionID) {
  const [sessionResult, messagesResult] = await Promise.all([
    client.session.get({ path: { id: sessionID } }),
    client.session.messages({ path: { id: sessionID } }),
  ])
  if (sessionResult.error || messagesResult.error) {
    throw new Error(`Reconciliation failed for ${sessionID}`)
  }
  return { session: sessionResult.data, messages: messagesResult.data || [] }
}

async function reconcileExistingSessions(client, project, directory) {
  const result = await client.session.list()
  if (result.error) throw new Error("Could not list sessions for startup reconciliation")
  for (const session of result.data || []) {
    try {
      const snapshot = await reconcileSession(client, session.id)
      writeEnvelope({
        eventType: "memory.session.snapshot",
        payload: snapshot,
        project,
        directory,
        occurredAt: snapshot.session?.time?.updated,
      })
    } catch (error) {
      await client.app.log({
        body: {
          service: "opencode-memory",
          level: "warn",
          message: "Startup reconciliation skipped a session",
          extra: { error: String(error), sessionID: session.id },
        },
      }).catch(() => undefined)
    }
  }
}

export default async ({ client, project, directory }) => {
  reconcileExistingSessions(client, project, directory).catch(async (error) => {
    await client.app.log({
      body: {
        service: "opencode-memory",
        level: "error",
        message: "Startup reconciliation failed",
        extra: { error: String(error) },
      },
    }).catch(() => undefined)
  })

  return {
    event: async ({ event }) => {
      try {
        if (!CAPTURED_EVENTS.has(event.type)) return
        writeEnvelope({
          eventType: event.type,
          payload: event,
          project,
          directory,
          occurredAt: event.properties?.info?.time?.updated || event.properties?.info?.time?.created,
        })

        if (event.type === "session.idle") {
          const snapshot = await reconcileSession(client, event.properties.sessionID)
          writeEnvelope({
            eventType: "memory.session.snapshot",
            payload: snapshot,
            project,
            directory,
            occurredAt: snapshot.session?.time?.updated,
          })
        }
      } catch (error) {
        await client.app.log({
          body: {
            service: "opencode-memory",
            level: "error",
            message: "Failed to persist memory event",
            extra: { error: String(error), eventType: event.type },
          },
        }).catch(() => undefined)
      }
    },
    "experimental.session.compacting": async (_input, output) => {
      output.context.push(`Create a durable handoff summary that preserves:
1. The user's current objective and completion criteria.
2. Decisions made, alternatives rejected, and why.
3. Files, symbols, database objects, APIs, and commands involved.
4. Errors encountered, root causes, and verified fixes.
5. Tests or checks already run and their outcomes.
6. Unfinished work, blockers, risks, and exact next steps.
7. User preferences and project conventions that must continue to apply.
Use concrete identifiers and do not include secrets or hidden reasoning.`)
    },
  }
}
