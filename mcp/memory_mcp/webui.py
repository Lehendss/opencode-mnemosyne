import json

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .config import Settings
from .repository import MemoryRepository

settings = Settings.from_env()
repository = MemoryRepository(
    settings.database_url,
    settings.ollama_url,
    settings.embedding_model,
    settings.embedding_dimension,
)

KIND_LABELS = {
    "procedure": "Procedures",
    "decision": "Decisions",
    "preference": "Preferences",
    "incident": "Incidents",
    "bug_resolution": "Bug Fixes",
    "session_summary": "Sessions",
    "assistant_response": "Responses",
    "user_prompt": "Prompts",
    "tool_result": "Tool Results",
    "file_change": "File Changes",
}

KIND_COLORS = {
    "procedure": "#22c55e",
    "decision": "#3b82f6",
    "preference": "#a855f7",
    "incident": "#ef4444",
    "bug_resolution": "#f97316",
    "session_summary": "#14b8a6",
    "assistant_response": "#6b7280",
    "user_prompt": "#06b6d4",
    "tool_result": "#eab308",
    "file_change": "#ec4899",
}

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenCode Memory</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background: #0d1117; color: #c9d1d9; min-height: 100vh; }
header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
header h1 { font-size: 18px; color: #58a6ff; }
.stats { display: flex; gap: 10px; flex-wrap: wrap; }
.stat { background: #21262d; border: 1px solid #30363d; border-radius: 6px; padding: 6px 12px; font-size: 13px; }
.stat strong { color: #f0f6fc; }
.container { max-width: 960px; margin: 0 auto; padding: 24px; }
.search-bar { display: flex; gap: 8px; margin-bottom: 16px; }
.search-bar input { flex: 1; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; color: #c9d1d9; font-size: 15px; outline: none; }
.search-bar input:focus { border-color: #58a6ff; }
.search-bar button { background: #238636; border: 1px solid #2ea043; color: #fff; border-radius: 6px; padding: 10px 20px; font-size: 14px; cursor: pointer; }
.search-bar button:hover { background: #2ea043; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 20px; }
.filter-chip { background: #21262d; border: 1px solid #30363d; border-radius: 20px; padding: 5px 14px; font-size: 13px; cursor: pointer; color: #8b949e; transition: all 0.15s; user-select: none; }
.filter-chip:hover { border-color: #58a6ff; color: #c9d1d9; }
.filter-chip.active { background: #1f6feb; border-color: #1f6feb; color: #fff; }
.result { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin-bottom: 10px; cursor: pointer; transition: border-color 0.15s; }
.result:hover { border-color: #58a6ff; }
.result-header { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; flex-wrap: wrap; }
.kind-badge { border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600; color: #fff; text-transform: uppercase; flex-shrink: 0; }
.result-title { font-size: 15px; font-weight: 600; color: #f0f6fc; flex: 1; min-width: 120px; }
.result-meta { font-size: 12px; color: #8b949e; }
.result-preview { font-size: 13px; color: #8b949e; line-height: 1.5; margin-bottom: 8px; }
.result-expanded { display: none; margin-top: 12px; }
.result.open .result-expanded { display: block; }
.content-full { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 14px; font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; }
.meta-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12px; color: #8b949e; margin-top: 10px; }
.meta-grid dt { color: #58a6ff; }
.scores { display: flex; gap: 14px; margin-top: 8px; font-size: 12px; color: #8b949e; flex-wrap: wrap; }
.scores span { display: flex; align-items: center; gap: 4px; }
.score-bar { width: 60px; height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 3px; }
.empty { text-align: center; padding: 48px 24px; color: #8b949e; font-size: 15px; }
.section-title { font-size: 14px; font-weight: 600; color: #f0f6fc; margin-bottom: 12px; margin-top: 24px; padding-bottom: 8px; border-bottom: 1px solid #30363d; }
.loading { text-align: center; padding: 40px; color: #8b949e; }
.error { background: #3d1214; border: 1px solid #ef4444; border-radius: 8px; padding: 16px; color: #f87171; font-size: 14px; margin-bottom: 16px; }
</style>
</head>
<body>
<header>
  <h1>OpenCode Memory</h1>
  <div class="stats" id="stats"></div>
</header>
<div class="container">
  <div class="search-bar">
    <input id="searchInput" type="text" placeholder="Search memories... (leave empty to browse)" autofocus>
    <button onclick="doSearch()">Search</button>
  </div>
  <div class="filters" id="filters"></div>
  <div id="error" class="error" style="display:none"></div>
  <div id="results">Loading...</div>
</div>
<script>
var KIND_COLORS = {kind_colors_json};
var KIND_LABELS = {kind_labels_json};
var activeKind = null;

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function escapeHtml(s) { return esc(s); }

async function api(url) {
  var resp = await fetch(url);
  if (!resp.ok) throw new Error(await resp.text());
  return resp.json();
}

async function loadStats() {
  try {
    var data = await api('/api/stats');
    var total = 0;
    for (var k in data.by_kind) total += data.by_kind[k];
    var html = '<div class="stat"><strong>' + total + '</strong> total</div>';
    for (var k in data.by_kind) {
      var label = (data.labels[k] || k).toLowerCase();
      html += '<div class="stat"><strong>' + data.by_kind[k] + '</strong> ' + esc(label) + '</div>';
    }
    document.getElementById('stats').innerHTML = html;

    html = '<span class="filter-chip' + (!activeKind ? ' active' : '') + '" onclick="setKind(null)">All</span>';
    for (var k in data.by_kind) {
      var lbl = data.labels[k] || k;
      html += '<span class="filter-chip' + (activeKind === k ? ' active' : '') + '" onclick="setKind(\\'' + k + '\\')">' + esc(lbl) + ' (' + data.by_kind[k] + ')</span>';
    }
    document.getElementById('filters').innerHTML = html;
  } catch(e) { showError(e.message); }
}

function setKind(kind) {
  activeKind = kind;
  loadStats();
  doSearch();
}

async function doSearch() {
  var q = document.getElementById('searchInput').value.trim();
  var results = document.getElementById('results');
  var error = document.getElementById('error');
  error.style.display = 'none';

  try {
    if (q) {
      results.innerHTML = '<div class="loading">Searching...</div>';
      var url = '/api/search?q=' + encodeURIComponent(q) + '&limit=20';
      if (activeKind) url += '&kind=' + encodeURIComponent(activeKind);
      var data = await api(url);
    } else {
      results.innerHTML = '<div class="section-title">' + (activeKind ? 'Recent: ' + ((KIND_LABELS && KIND_LABELS[activeKind]) || activeKind) : 'Recent memories') + '</div><div class="loading">Loading...</div>';
      var url = '/api/recent?limit=20';
      if (activeKind) url += '&kind=' + encodeURIComponent(activeKind);
      var data = await api(url);
    }
    if (!data.length) {
      results.innerHTML = '<div class="empty">No memories found.</div>';
      return;
    }
    if (!q) results.innerHTML = '<div class="section-title">' + (activeKind ? 'Recent: ' + ((KIND_LABELS && KIND_LABELS[activeKind]) || activeKind) : 'Recent memories') + '</div>' + data.map(renderResult).join('');
    else results.innerHTML = data.map(renderResult).join('');
  } catch(e) { showError(e.message); }
}

function renderResult(r) {
  var color = KIND_COLORS[r.kind] || '#6b7280';
  var label = r.kind || 'unknown';
  var preview = (r.content || '').slice(0, 250);
  var date = r.occurred_at ? new Date(r.occurred_at).toLocaleDateString('en-US', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
  var imp = r.importance != null ? Math.round(r.importance * 100) : 0;
  var conf = r.confidence != null ? Math.round(r.confidence * 100) : 0;
  var scoreColor = r.score > 0.5 ? '#22c55e' : r.score > 0.3 ? '#eab308' : '#ef4444';
  return '<div class="result" onclick="this.classList.toggle(\\'open\\')">' +
    '<div class="result-header">' +
      '<span class="kind-badge" style="background:' + color + '">' + esc(label) + '</span>' +
      '<span class="result-title">' + esc(r.title || 'Untitled') + '</span>' +
      '<span class="result-meta">' + esc(date) + '</span>' +
    '</div>' +
    '<div class="result-preview">' + esc(preview) + ((r.content || '').length > 250 ? '...' : '') + '</div>' +
    '<div class="scores">' +
      '<span>importance <span class="score-bar"><span class="score-bar-fill" style="width:' + imp + '%;background:#22c55e"></span></span> ' + imp + '%</span>' +
      '<span>confidence <span class="score-bar"><span class="score-bar-fill" style="width:' + conf + '%;background:#3b82f6"></span></span> ' + conf + '%</span>' +
      (r.score != null ? '<span>match <span class="score-bar"><span class="score-bar-fill" style="width:' + Math.round(r.score*100) + '%;background:' + scoreColor + '"></span></span> ' + Math.round(r.score*100) + '%</span>' : '') +
    '</div>' +
    '<div class="result-expanded">' +
      '<div class="content-full">' + esc(r.content || '') + '</div>' +
      '<dl class="meta-grid">' +
        '<dt>ID</dt><dd>' + esc(r.memory_id || '') + '</dd>' +
        '<dt>Session</dt><dd>' + esc(r.session_id || '') + '</dd>' +
        '<dt>Project</dt><dd>' + esc(r.project_label || r.project_id || '') + '</dd>' +
      '</dl>' +
      (r.metadata ? '<div style="margin-top:8px"><dt style="color:#58a6ff;font-size:12px">Metadata</dt><dd style="font-size:12px;color:#8b949e;white-space:pre-wrap">' + esc(JSON.stringify(r.metadata, null, 2)) + '</dd></div>' : '') +
    '</div>' +
  '</div>';
}

function showError(msg) {
  var el = document.getElementById('error');
  el.textContent = msg;
  el.style.display = 'block';
  document.getElementById('results').innerHTML = '';
}

document.getElementById('searchInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doSearch();
});

loadStats();
doSearch();
</script>
</body>
</html>"""


def kind_colors_json():
    return json.dumps(KIND_COLORS)


def kind_labels_json():
    return json.dumps(KIND_LABELS)


def render_page():
    return PAGE.replace("{kind_colors_json}", kind_colors_json()).replace("{kind_labels_json}", kind_labels_json())


async def home(request):
    return HTMLResponse(render_page())


async def api_stats(request):
    by_kind = repository.stats()
    return JSONResponse({"by_kind": by_kind, "labels": KIND_LABELS})


async def api_search(request):
    q = request.query_params.get("q", "").strip()
    kind = request.query_params.get("kind")
    project = request.query_params.get("project")
    limit = min(int(request.query_params.get("limit", "20")), 50)
    if not q:
        return JSONResponse([])
    kinds = [kind] if kind else None
    results = repository.search(q, project=project, kinds=kinds, limit=limit)
    return JSONResponse(results)


async def api_recent(request):
    project = request.query_params.get("project")
    kind = request.query_params.get("kind")
    limit = min(int(request.query_params.get("limit", "20")), 50)
    kinds = [kind] if kind else None
    results = repository.recent(project=project, kinds=kinds, limit=limit)
    return JSONResponse(results)


async def api_memory(request):
    memory_id = request.path_params["memory_id"]
    result = repository.get(memory_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


async def api_session(request):
    session_id = request.path_params["session_id"]
    result = repository.session_summary(session_id)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


app = Starlette(
    routes=[
        Route("/", home),
        Route("/api/stats", api_stats),
        Route("/api/search", api_search),
        Route("/api/recent", api_recent),
        Route("/api/memory/{memory_id}", api_memory),
        Route("/api/session/{session_id}", api_session),
    ],
)
