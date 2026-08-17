import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"

import MemoryCapturePlugin from "./index.js"
import { redactString, sanitize, eventIdentity, sha256 } from "./_internals.js"

test("exports exactly one loadable plugin function", () => {
  assert.equal(typeof MemoryCapturePlugin, "function")
})

test("does not capture incremental message part updates", () => {
  const source = readFileSync(new URL("./index.js", import.meta.url), "utf8")
  const capturedEvents = source.slice(
    source.indexOf("const CAPTURED_EVENTS"),
    source.indexOf("])", source.indexOf("const CAPTURED_EVENTS")),
  )
  assert.equal(capturedEvents.includes('"message.part.updated"'), false)
  assert.equal(capturedEvents.includes('"session.idle"'), true)
})

test("redacts secret-like keys recursively", () => {
  assert.deepEqual(sanitize({ nested: { password: "secret" } }), {
    nested: { password: "[REDACTED]" },
  })
})

test("redacts connection strings and bearer tokens", () => {
  const result = redactString(
    "postgresql://user:pass@localhost/db Authorization: Bearer abc.def.ghi",
  )
  assert.equal(result.includes("user:pass"), false)
  assert.equal(result.includes("abc.def.ghi"), false)
})

test("derives identifiers from part events", () => {
  assert.deepEqual(
    eventIdentity("message.part.updated", {
      properties: { part: { id: "p1", messageID: "m1", sessionID: "s1" } },
    }),
    { session: "s1", message: "m1", part: "p1" },
  )
})

test("does not persist model reasoning", () => {
  assert.deepEqual(
    sanitize({
      id: "p1",
      sessionID: "s1",
      messageID: "m1",
      type: "reasoning",
      text: "private reasoning",
    }),
    { id: "p1", sessionID: "s1", messageID: "m1", type: "reasoning", redacted: true },
  )
})

test("redacts commands that access credential files", () => {
  assert.equal(
    sanitize({ command: "cat ~/.config/opencode/auth.json" }).command,
    "[REDACTED SENSITIVE COMMAND]",
  )
})
