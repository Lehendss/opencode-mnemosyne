import assert from "node:assert/strict"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import test from "node:test"

import MemoryCapturePlugin from "./index.js"
import { redactString, sanitize, eventIdentity, sha256 } from "./_internals.js"
import { documentTools, ingestPath, isPdfRead } from "./documents.js"

test("exports exactly one loadable plugin function", () => {
  assert.equal(typeof MemoryCapturePlugin, "function")
})

test("does not reconcile every session on normal startup", async () => {
  let listCalls = 0
  await MemoryCapturePlugin({
    client: {
      session: {
        list: async () => {
          listCalls += 1
          return { data: [] }
        },
      },
      app: { log: async () => undefined },
    },
    project: { worktree: "/tmp/project" },
    directory: "/tmp/project",
  })

  assert.equal(listCalls, 0)
})

test("registers bounded PDF tools", () => {
  const tools = documentTools("/tmp/project")
  assert.deepEqual(Object.keys(tools), [
    "pdf_ingest",
    "pdf_search",
    "pdf_read_pages",
    "pdf_view_pages",
    "pdf_get",
  ])
})

test("detects built-in PDF reads without blocking other files", () => {
  assert.equal(isPdfRead("read", { filePath: "/tmp/report.PDF" }), true)
  assert.equal(isPdfRead("read", { filePath: "/tmp/report.txt" }), false)
  assert.equal(isPdfRead("pdf_read_pages", { path: "/tmp/report.pdf" }), false)
})

test("detects a PDF by signature when the extension is missing", () => {
  const directory = mkdtempSync(join(tmpdir(), "document-signature-"))
  try {
    const path = join(directory, "attachment")
    writeFileSync(path, "%PDF-1.4\n%%EOF\n")
    assert.equal(isPdfRead("read", { filePath: path }), true)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})

test("asks permission before ingesting a relative PDF path", async () => {
  const directory = mkdtempSync(join(tmpdir(), "document-plugin-"))
  const originalFetch = globalThis.fetch
  const originalToken = process.env.OPENCODE_DOCUMENT_TOKEN
  let permissionAsked = false
  try {
    writeFileSync(join(directory, "sample.pdf"), "%PDF-1.4\n%%EOF\n")
    process.env.OPENCODE_DOCUMENT_TOKEN = "test-token"
    globalThis.fetch = async (_url, options) => {
      assert.equal(permissionAsked, true)
      assert.equal(options.body.get("project_id"), sha256(resolve("/tmp/stable-worktree")))
      return new Response(JSON.stringify({ document_id: "00000000-0000-0000-0000-000000000001", status: "queued" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    const result = await ingestPath(
      "sample.pdf",
      directory,
      "/tmp/stable-worktree",
      undefined,
      async ({ permission, patterns }) => {
        assert.equal(permission, "read")
        assert.deepEqual(patterns, [join(directory, "sample.pdf")])
        permissionAsked = true
      },
    )
    assert.equal(result.status, "queued")
  } finally {
    globalThis.fetch = originalFetch
    if (originalToken === undefined) delete process.env.OPENCODE_DOCUMENT_TOKEN
    else process.env.OPENCODE_DOCUMENT_TOKEN = originalToken
    rmSync(directory, { recursive: true, force: true })
  }
})

test("recovers a PDF relative to the tool directory after edit permission", async () => {
  const directory = mkdtempSync(join(tmpdir(), "document-recovery-"))
  const originalFetch = globalThis.fetch
  const originalToken = process.env.OPENCODE_DOCUMENT_TOKEN
  let permissionAsked = false
  try {
    process.env.OPENCODE_DOCUMENT_TOKEN = "test-token"
    globalThis.fetch = async () => new Response("%PDF-1.4\n%%EOF\n", { status: 200 })
    const pdfGet = documentTools(directory).pdf_get
    const result = await pdfGet.execute(
      { document_id: "00000000-0000-0000-0000-000000000001", destination: "recovered.pdf" },
      {
        directory,
        ask: async ({ permission, patterns }) => {
          assert.equal(permission, "edit")
          assert.deepEqual(patterns, [join(directory, "recovered.pdf")])
          permissionAsked = true
        },
      },
    )
    assert.equal(permissionAsked, true)
    assert.equal(readFileSync(join(directory, "recovered.pdf"), "utf8").startsWith("%PDF-"), true)
    assert.equal(JSON.parse(result).destination, join(directory, "recovered.pdf"))
  } finally {
    globalThis.fetch = originalFetch
    if (originalToken === undefined) delete process.env.OPENCODE_DOCUMENT_TOKEN
    else process.env.OPENCODE_DOCUMENT_TOKEN = originalToken
    rmSync(directory, { recursive: true, force: true })
  }
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

test("removes NUL bytes before persistence", () => {
  assert.equal(redactString("before\u0000after"), "beforeafter")
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
