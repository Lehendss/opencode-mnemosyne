import { createHash } from "node:crypto"
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, statSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, extname, isAbsolute, join, resolve } from "node:path"

import { tool } from "@opencode-ai/plugin"

const CLIENT_DIR = resolve(process.env.OPENCODE_DOCUMENT_CONFIG_HOME || join(homedir(), ".config", "opencode-memory"))
const TOKEN_FILE = resolve(process.env.OPENCODE_DOCUMENT_TOKEN_FILE || join(CLIENT_DIR, "document-token"))
const MAX_READ_PAGES = 8
const MAX_VIEW_PAGES = 4

function sha256(value) {
  return createHash("sha256").update(value).digest("hex")
}

function projectIdentity(directory) {
  const root = resolve(directory)
  return { project_id: sha256(root), project_label: basename(root) }
}

function token() {
  const environmentToken = process.env.OPENCODE_DOCUMENT_TOKEN
  if (environmentToken) return environmentToken.trim()
  try {
    return readFileSync(TOKEN_FILE, "utf8").trim()
  } catch {
    throw new Error(`Document service token not found at ${TOKEN_FILE}. Run make setup.`)
  }
}

function setting(name, fallback) {
  try {
    return readFileSync(join(CLIENT_DIR, name), "utf8").trim() || fallback
  } catch {
    return fallback
  }
}

function serviceUrl() {
  return (process.env.OPENCODE_DOCUMENT_URL || setting("document-url", "http://127.0.0.1:8789")).replace(/\/$/, "")
}

function maxUploadBytes() {
  return Number(process.env.OPENCODE_DOCUMENT_MAX_BYTES || setting("document-max-bytes", String(50 * 1024 * 1024)))
}

async function request(path, options = {}) {
  const timeout = AbortSignal.timeout(options.timeout || 180000)
  const response = await fetch(`${serviceUrl()}${path}`, {
    ...options,
    signal: timeout,
    headers: {
      Authorization: `Bearer ${token()}`,
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      const message = body.detail || body.message || body.error
      detail = typeof message === "string" ? message : JSON.stringify(message || body)
    } catch {
      // Keep the HTTP status when the service returns a non-JSON error.
    }
    throw new Error(`Document service request failed (${response.status}): ${detail}. Ensure you are using the exact document_id UUID returned by pdf_ingest and valid page numbers.`)
  }
  return response
}

export async function ingestPath(path, directory, worktree, title, ask) {
  const absolute = isAbsolute(path) ? resolve(path) : resolve(directory, path)
  if (ask) {
    await ask({
      permission: "read",
      patterns: [absolute],
      always: [absolute],
      metadata: { purpose: "Index this PDF in the local Document RAG service" },
    })
  }
  if (!existsSync(absolute)) throw new Error(`PDF does not exist: ${absolute}`)
  const size = statSync(absolute).size
  const maximum = maxUploadBytes()
  if (size > maximum) {
    throw new Error(`PDF is ${size} bytes; the configured limit is ${maximum}`)
  }
  const bytes = readFileSync(absolute)
  if (!bytes.subarray(0, 5).equals(Buffer.from("%PDF-"))) throw new Error("The selected file is not a valid PDF")
  const identity = projectIdentity(worktree || directory)
  const form = new FormData()
  form.append("file", new Blob([bytes], { type: "application/pdf" }), basename(absolute))
  form.append("project_id", identity.project_id)
  form.append("project_label", identity.project_label)
  form.append("source_path_sha256", sha256(absolute))
  if (title) form.append("title", title)
  const response = await request("/v1/documents", { method: "POST", body: form, timeout: 300000 })
  return response.json()
}

function format(value) {
  return JSON.stringify(value, null, 2)
}

export function documentTools(rootDirectory) {
  const identity = projectIdentity(rootDirectory)
  return {
    pdf_ingest: tool({
      description: "Index a local PDF into the private Document RAG store. Returns {document_id, status, page_count, deduplicated, content_sha256}. The original is preserved in MinIO and processing continues durably after this call returns. Use 'path' for the PDF location (some models may call it 'file_path'; both are accepted).",
      args: {
        path: tool.schema.string().optional().describe("Absolute or project-relative path to the PDF file. Example: 'informe.pdf' or '/tmp/informe.pdf'."),
        file_path: tool.schema.string().optional().describe("Alias for path. Use one of path or file_path, not both."),
        title: tool.schema.string().optional().describe("Optional human-readable document title"),
      },
      async execute(args, context) {
        const sourcePath = args.path || args.file_path
        if (!sourcePath) throw new Error("Missing required argument: path (or file_path)")
        const result = await ingestPath(
          sourcePath,
          context.directory || rootDirectory,
          context.worktree || rootDirectory,
          args.title,
          context.ask,
        )
        return format(result)
      },
    }),
    pdf_search: tool({
      description: "Search indexed PDFs semantically and lexically across the current project. Use this before reading pages so only relevant excerpts enter context. Returns a list of results, each with document_id, page_number, snippet and score.",
      args: {
        query: tool.schema.string().min(2).describe("Question or concepts to find"),
        limit: tool.schema.number().int().min(1).max(20).optional().describe("Maximum page matches; default 8"),
      },
      async execute(args) {
        const response = await request("/v1/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: args.query, limit: args.limit || 8, project_id: identity.project_id }),
        })
        return format(await response.json())
      },
    }),
    pdf_read_pages: tool({
      description: "Read the extracted text of specific PDF pages. Search first with pdf_search to find the relevant document_id and page numbers. At most 8 pages are accepted per call. The document_id must be the UUID returned by pdf_ingest.",
      args: {
        document_id: tool.schema.string().uuid().describe("UUID of the document returned by pdf_ingest. Example: '957ea761-9488-4e41-8311-a0b6210e00bb'."),
        pages: tool.schema.array(tool.schema.number().int().min(1)).min(1).max(MAX_READ_PAGES).describe("Array of page numbers to read. Example: [1] or [3, 5]."),
      },
      async execute(args) {
        const query = new URLSearchParams({ project_id: identity.project_id })
        for (const page of [...new Set(args.pages)]) query.append("pages", String(page))
        const response = await request(`/v1/documents/${args.document_id}/pages?${query}`)
        return format(await response.json())
      },
    }),
    pdf_view_pages: tool({
      description: "Render selected PDF pages to local JPEG files for visual inspection of diagrams, forms or tables. The document_id must be the UUID returned by pdf_ingest. Call read on the returned image paths. At most 4 pages are accepted.",
      args: {
        document_id: tool.schema.string().uuid().describe("UUID of the document returned by pdf_ingest."),
        pages: tool.schema.array(tool.schema.number().int().min(1)).min(1).max(MAX_VIEW_PAGES).describe("Array of page numbers to render. Example: [1] or [2, 4]."),
      },
      async execute(args) {
        const outputDirectory = join(homedir(), ".cache", "opencode-memory", "documents", args.document_id)
        mkdirSync(outputDirectory, { recursive: true, mode: 0o700 })
        const paths = []
        for (const page of [...new Set(args.pages)]) {
          const response = await request(
            `/v1/documents/${args.document_id}/page/${page}.jpg?project_id=${identity.project_id}`,
          )
          const destination = join(outputDirectory, `page-${page}.jpg`)
          writeFileSync(destination, Buffer.from(await response.arrayBuffer()), { mode: 0o600 })
          paths.push(destination)
        }
        return format({ document_id: args.document_id, image_paths: paths, next: "Use read on these JPEG paths for visual analysis." })
      },
    }),
    pdf_get: tool({
      description: "Get PDF metadata and status, or recover the indexed original to a local path. The document_id must be the UUID returned by pdf_ingest. If destination is omitted, returns metadata including status (queued/processing/ready/failed). Use this to poll until status is ready before calling pdf_search or pdf_read_pages.",
      args: {
        document_id: tool.schema.string().uuid().describe("UUID of the document returned by pdf_ingest."),
        destination: tool.schema.string().optional().describe("Optional absolute or relative local destination path ending in .pdf. If omitted, returns metadata only."),
      },
      async execute(args, context) {
        if (!args.destination) {
          const response = await request(`/v1/documents/${args.document_id}?project_id=${identity.project_id}`)
          return format(await response.json())
        }
        const destination = isAbsolute(args.destination)
          ? resolve(args.destination)
          : resolve(context.directory || rootDirectory, args.destination)
        if (extname(destination).toLowerCase() !== ".pdf") throw new Error("Destination must end in .pdf")
        await context.ask({
          permission: "edit",
          patterns: [destination],
          always: [destination],
          metadata: { purpose: "Recover the original indexed PDF" },
        })
        const response = await request(`/v1/documents/${args.document_id}/download?project_id=${identity.project_id}`)
        mkdirSync(dirname(destination), { recursive: true })
        writeFileSync(destination, Buffer.from(await response.arrayBuffer()), { mode: 0o600, flag: "wx" })
        return format({ document_id: args.document_id, destination })
      },
    }),
  }
}

export function isPdfRead(toolName, args, directory = process.cwd()) {
  const path = args?.filePath || args?.path
  if (toolName !== "read" || typeof path !== "string") return false
  if (extname(path).toLowerCase() === ".pdf") return true
  const absolute = isAbsolute(path) ? path : resolve(directory, path)
  let descriptor
  try {
    descriptor = openSync(absolute, "r")
    const signature = Buffer.alloc(5)
    return readSync(descriptor, signature, 0, signature.length, 0) === 5 && signature.equals(Buffer.from("%PDF-"))
  } catch {
    return false
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

export const DOCUMENT_SYSTEM_GUIDANCE = `PDF handling policy:
- Never use the built-in read tool on a PDF. The plugin blocks raw PDF reads to protect the session database and model context.
- To work with a PDF, follow this exact sequence:
  1. Call pdf_ingest with the correct argument name 'path' (not 'file_path').
  2. Save the returned document_id UUID.
  3. Call pdf_get with that exact document_id until status is 'ready'.
  4. Call pdf_search with your question to find relevant pages.
  5. Call pdf_read_pages with the same document_id and the relevant page numbers.
- Use pdf_view_pages for layout, charts, images, forms, signatures or tables, then read the returned JPEG paths.
- Use pdf_get with a destination only when the intact original must be recovered.
- Do not invent arguments such as collection_name, file_path or wait_for_indexing. Use only the documented arguments.`
