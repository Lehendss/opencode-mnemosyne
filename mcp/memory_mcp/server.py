from typing import List, Optional

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .repository import MemoryRepository


settings = Settings.from_env()
repository = MemoryRepository(
    settings.database_url,
    settings.ollama_url,
    settings.embedding_model,
    settings.embedding_dimension,
)
mcp = FastMCP("OpenCode Memory", host="0.0.0.0", port=settings.port)


@mcp.tool()
def memory_search(
    query: str,
    project: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    limit: int = 8,
) -> list:
    """Search technical memory using semantic, lexical, and metadata signals."""
    return repository.search(query, project, kinds, session_id, limit)


@mcp.tool()
def memory_get(memory_id: str) -> dict:
    """Get one complete memory record and its source references."""
    result = repository.get(memory_id)
    return result or {"error": "memory not found"}


@mcp.tool()
def memory_recent(project: Optional[str] = None, kinds: Optional[List[str]] = None, limit: int = 10) -> list:
    """Return the most recent memories, optionally scoped to a project."""
    return repository.recent(project, kinds, limit)


@mcp.tool()
def memory_get_session_summary(session_id: str) -> dict:
    """Return session metadata and its ordered active memories."""
    result = repository.session_summary(session_id)
    return result or {"error": "session not found"}


@mcp.tool()
def memory_find_procedures(
    query: str,
    project: Optional[str] = None,
    limit: int = 8,
    verified_only: bool = True,
) -> list:
    """Find proven workflows, commands, tool sequences, and bug-resolution procedures."""
    return repository.search(
        query, project, ["procedure", "bug_resolution"], None, limit, verified_only
    )


@mcp.tool()
def memory_find_decisions(
    query: str,
    project: Optional[str] = None,
    limit: int = 8,
) -> list:
    """Find prior technical decisions and their source sessions."""
    return repository.search(query, project, ["decision"], None, limit)


@mcp.tool()
def memory_find_preferences(
    query: str,
    project: Optional[str] = None,
    limit: int = 8,
) -> list:
    """Find durable user or project preferences relevant to a task."""
    return repository.search(query, project, ["preference"], None, limit)


@mcp.tool()
def memory_find_similar_errors(
    query: str,
    project: Optional[str] = None,
    limit: int = 8,
) -> list:
    """Find similar incidents, tool failures, and verified bug resolutions."""
    return repository.search(query, project, ["incident", "bug_resolution"], None, limit)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
