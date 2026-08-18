import { createHash, randomUUID } from "node:crypto"
import { homedir } from "node:os"

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
const MAX_STRING_CHARS = Number(process.env.OPENCODE_MEMORY_MAX_STRING_CHARS || 200000)

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex")
}

export function redactString(value) {
  let result = value.replaceAll("\u0000", "").split(homedir()).join("~")
  for (const pattern of SECRET_PATTERNS) result = result.replace(pattern, "[REDACTED]")
  if (result.length > MAX_STRING_CHARS) {
    return `${result.slice(0, MAX_STRING_CHARS)}\n[TRUNCATED ${result.length - MAX_STRING_CHARS} CHARS]`
  }
  return result
}

export function sanitize(value, key = "", seen = new WeakSet()) {
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

export function eventIdentity(eventType, payload) {
  const props = payload?.properties || payload || {}
  const info = props.info || {}
  const part = props.part || {}
  return {
    session: props.sessionID || props.session?.id || info.sessionID || info.id || part.sessionID || null,
    message: props.messageID || info.id || part.messageID || null,
    part: props.partID || part.id || null,
  }
}
