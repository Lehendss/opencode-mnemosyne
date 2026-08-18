import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


MEMORY_NAMESPACE = uuid.UUID("e7cae454-c01f-4479-bf74-3147701d53e2")
PREFERENCE_PATTERN = re.compile(
    r"\b(prefiero|preferimos|me gusta|quiero que|no quiero|"
    r"i prefer|we prefer|i like|i want you to|please always|please never)\b",
    re.IGNORECASE,
)
DECISION_PATTERN = re.compile(
    r"\b(decidimos|decisión|decision|elegimos|usaremos|vamos a usar|la estrategia|"
    r"recomiendo|debe(?:mos)?|se debe|should|must|we will use|the strategy)\b",
    re.IGNORECASE,
)
INCIDENT_PATTERN = re.compile(
    r"\b(error|bug|falla|falló|problema|exception|traceback|failed|failure|incident|"
    r"no funciona|does not work|broken)\b",
    re.IGNORECASE,
)
VERIFICATION_COMMAND_PATTERN = re.compile(
    r"\b(test|check|status|verify|health|quick_check|restore|compile|build|ps)\b",
    re.IGNORECASE,
)
VERIFICATION_RESULT_PATTERN = re.compile(
    r"\b(passed|success|successful|ok|healthy|funciona|verificado|correcto|"
    r"sin errores|no errors|build success)\b",
    re.IGNORECASE,
)
IMPORTANCE_BY_KIND = {
    "user_prompt": 0.35,
    "assistant_response": 0.40,
    "tool_result": 0.45,
    "file_change": 0.70,
    "session_summary": 0.70,
    "preference": 0.90,
    "decision": 0.85,
    "procedure": 0.85,
    "bug_resolution": 0.95,
    "incident": 0.80,
}


def _text(value: Any, maximum: int) -> str:
    if isinstance(value, str):
        return value.replace("\x00", "")[:maximum].strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True).replace("\\u0000", "")[:maximum].strip()


def _memory(
    envelope: Dict[str, Any],
    source_type: str,
    source_id: str,
    kind: str,
    content: str,
    title: Optional[str] = None,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    importance: Optional[float] = None,
    confidence: float = 0.80,
) -> Optional[Dict[str, Any]]:
    content = content.replace("\x00", "").strip()
    title = title.replace("\x00", "") if title else title
    if not content:
        return None
    project_id = envelope["project_id"]
    identity = "%s:%s:%s" % (project_id, source_type, source_id)
    return {
        "memory_id": str(uuid.uuid5(MEMORY_NAMESPACE, identity)),
        "project_id": project_id,
        "project_label": _text(envelope.get("project_label", ""), 500) or None,
        "session_id": envelope.get("session_id"),
        "message_id": message_id or envelope.get("message_id"),
        "source_type": source_type,
        "source_id": source_id,
        "kind": kind,
        "title": title,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "metadata": metadata or {},
        "occurred_at": envelope["occurred_at"],
        "importance": importance if importance is not None else IMPORTANCE_BY_KIND.get(kind, 0.50),
        "confidence": confidence,
        "valid_from": envelope["occurred_at"],
    }


def _part_memory(
    envelope: Dict[str, Any],
    part: Dict[str, Any],
    role: Optional[str],
    maximum: int,
) -> Optional[Dict[str, Any]]:
    part_type = part.get("type")
    part_id = part.get("id")
    if not part_id or part_type == "reasoning":
        return None

    if part_type == "text":
        content = _text(part.get("text", ""), maximum)
        kind = "user_prompt" if role == "user" else "assistant_response"
    elif part_type == "tool":
        state = part.get("state", {})
        if state.get("status") not in ("completed", "error"):
            return None
        output = state.get("output") if state.get("status") == "completed" else state.get("error")
        content = "Tool: %s\nStatus: %s\nInput: %s\nResult: %s" % (
            part.get("tool", "unknown"),
            state.get("status"),
            _text(state.get("input", {}), maximum // 3),
            _text(output or "", maximum * 2 // 3),
        )
        kind = "tool_result"
    elif part_type in ("patch", "file"):
        content = _text(part, maximum)
        kind = "file_change"
    elif part_type == "compaction":
        content = _text(part.get("summary", part), maximum)
        kind = "session_summary"
    else:
        return None

    return _memory(
        envelope,
        "part",
        part_id,
        kind,
        content,
        message_id=part.get("messageID"),
        metadata={"part_type": part_type, "role": role},
    )


def _parts_text(parts: List[Dict[str, Any]], maximum: int) -> str:
    texts = [part.get("text", "").strip() for part in parts if part.get("type") == "text"]
    return _text("\n\n".join(text for text in texts if text), maximum)


def _semantic_memories(
    envelope: Dict[str, Any],
    part: Dict[str, Any],
    role: Optional[str],
    maximum: int,
) -> List[Dict[str, Any]]:
    if part.get("type") != "text" or not part.get("id"):
        return []
    content = _text(part.get("text", ""), maximum)
    if not content:
        return []

    memories = []
    if role == "user" and len(content) <= 1200 and PREFERENCE_PATTERN.search(content):
        item = _memory(
            envelope,
            "semantic",
            "%s:preference" % part["id"],
            "preference",
            content,
            title="User preference",
            message_id=part.get("messageID"),
            metadata={"derived_from": part["id"], "role": role},
            confidence=0.75,
        )
        if item:
            memories.append(item)

    if role == "assistant":
        paragraphs = re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ])", content)
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if len(paragraph) < 40 or not DECISION_PATTERN.search(paragraph):
                continue
            digest = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()[:12]
            item = _memory(
                envelope,
                "semantic",
                "%s:decision:%s" % (part["id"], digest),
                "decision",
                paragraph[:4000],
                title="Technical decision",
                message_id=part.get("messageID"),
                metadata={"derived_from": part["id"], "role": role},
                confidence=0.72,
            )
            if item:
                memories.append(item)
            if len(memories) >= 5:
                break
    return memories


def _procedure_memory(
    envelope: Dict[str, Any],
    aggregate: Dict[str, Any],
    messages_by_id: Dict[str, Dict[str, Any]],
    maximum: int,
) -> Optional[Dict[str, Any]]:
    info = aggregate.get("info", {})
    if info.get("role") != "assistant" or not info.get("id"):
        return None
    parts = aggregate.get("parts", [])
    tools = []
    for part in parts:
        if part.get("type") != "tool":
            continue
        state = part.get("state", {})
        if state.get("status") not in ("completed", "error"):
            continue
        result = state.get("output") if state.get("status") == "completed" else state.get("error")
        tools.append(
            {
                "tool": part.get("tool", "unknown"),
                "status": state.get("status"),
                "input": _text(state.get("input", {}), max(500, maximum // 10)),
                "result": _text(result or "", max(1000, maximum // 8)),
            }
        )
    if not tools:
        return None

    parent = messages_by_id.get(info.get("parentID"), {})
    goal = _parts_text(parent.get("parts", []), max(2000, maximum // 4)) or "Complete the recorded task"
    outcome = _parts_text(parts, max(2000, maximum // 4))
    steps = []
    for index, tool in enumerate(tools, 1):
        steps.append(
            "%d. Tool `%s` (%s)\n   Input: %s\n   Result: %s"
            % (index, tool["tool"], tool["status"], tool["input"], tool["result"])
        )
    verified = any(
        tool["tool"] == "bash"
        and VERIFICATION_COMMAND_PATTERN.search(tool["input"])
        and VERIFICATION_RESULT_PATTERN.search(tool["result"])
        for tool in tools
    ) or bool(outcome and VERIFICATION_RESULT_PATTERN.search(outcome))
    incident_goal = bool(INCIDENT_PATTERN.search(goal))
    if incident_goal and verified:
        kind = "bug_resolution"
    elif incident_goal:
        kind = "incident"
    else:
        kind = "procedure"
    content = "Goal:\n%s\n\nSteps:\n%s" % (goal, "\n".join(steps))
    if outcome:
        content += "\n\nOutcome:\n%s" % outcome
    title = ("Bug resolution: " if kind == "bug_resolution" else "Procedure: ") + goal[:160]
    return _memory(
        envelope,
        "procedure",
        info.get("procedureSourceID", info["id"]),
        kind,
        content[:maximum],
        title=title,
        message_id=info.get("lastMessageID", info["id"]),
        metadata={
            "goal_message_id": info.get("parentID"),
            "tools": [tool["tool"] for tool in tools],
            "step_count": len(tools),
            "has_errors": any(tool["status"] == "error" for tool in tools),
            "verified": verified,
        },
        importance=IMPORTANCE_BY_KIND[kind] if verified else min(0.60, IMPORTANCE_BY_KIND[kind]),
        confidence=0.92 if verified else 0.58,
    )


def _incident_memories(
    envelope: Dict[str, Any], aggregate: Dict[str, Any], maximum: int
) -> List[Dict[str, Any]]:
    info = aggregate.get("info", {})
    memories = []
    for part in aggregate.get("parts", []):
        if part.get("type") != "tool" or not part.get("id"):
            continue
        state = part.get("state", {})
        if state.get("status") != "error":
            continue
        content = "Tool `%s` failed.\nInput: %s\nError: %s" % (
            part.get("tool", "unknown"),
            _text(state.get("input", {}), maximum // 3),
            _text(state.get("error", ""), maximum * 2 // 3),
        )
        item = _memory(
            envelope,
            "incident",
            part["id"],
            "incident",
            content,
            title="Tool failure: %s" % part.get("tool", "unknown"),
            message_id=info.get("id"),
            metadata={"tool": part.get("tool"), "derived_from": part["id"]},
            confidence=0.98,
        )
        if item:
            memories.append(item)
    return memories


def memories_from_envelope(envelope: Dict[str, Any], maximum: int) -> List[Dict[str, Any]]:
    event_type = envelope["event_type"]
    payload = envelope.get("payload", {})
    memories = []

    if event_type == "message.part.updated":
        part = payload.get("properties", {}).get("part", {})
        item = _part_memory(envelope, part, None, maximum)
        if item:
            memories.append(item)

    elif event_type == "memory.session.snapshot":
        session = payload.get("session") or {}
        title = session.get("title")
        if title:
            item = _memory(
                envelope,
                "session",
                session.get("id", envelope.get("session_id", "unknown")),
                "session_summary",
                title,
                title=title,
            )
            if item:
                memories.append(item)
        aggregates = payload.get("messages", [])
        messages_by_id = {
            aggregate.get("info", {}).get("id"): aggregate
            for aggregate in aggregates
            if aggregate.get("info", {}).get("id")
        }
        for aggregate in aggregates:
            info = aggregate.get("info", {})
            role = info.get("role")
            for part in aggregate.get("parts", []):
                item = _part_memory(envelope, part, role, maximum)
                if item:
                    memories.append(item)
                memories.extend(_semantic_memories(envelope, part, role, maximum))
            memories.extend(_incident_memories(envelope, aggregate, maximum))
        procedure_groups = {}
        for aggregate in aggregates:
            info = aggregate.get("info", {})
            if info.get("role") != "assistant" or not info.get("parentID"):
                continue
            procedure_groups.setdefault(info["parentID"], []).append(aggregate)
        for parent_id, group in procedure_groups.items():
            combined_parts = []
            for aggregate in group:
                combined_parts.extend(aggregate.get("parts", []))
            last_message_id = group[-1].get("info", {}).get("id")
            combined = {
                "info": {
                    "id": last_message_id,
                    "lastMessageID": last_message_id,
                    "parentID": parent_id,
                    "procedureSourceID": parent_id,
                    "role": "assistant",
                },
                "parts": combined_parts,
            }
            procedure = _procedure_memory(envelope, combined, messages_by_id, maximum)
            if procedure:
                memories.append(procedure)

    elif event_type in ("session.created", "session.updated"):
        info = payload.get("properties", {}).get("info", {})
        if info.get("title"):
            item = _memory(
                envelope,
                "session",
                info.get("id", envelope.get("session_id", "unknown")),
                "session_summary",
                info["title"],
                title=info["title"],
            )
            if item:
                memories.append(item)

    return memories


def session_from_envelope(envelope: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    payload = envelope.get("payload", {})
    event_type = envelope["event_type"]
    if event_type == "memory.session.snapshot":
        info = payload.get("session") or {}
    else:
        info = payload.get("properties", {}).get("info", {})
    session_id = info.get("id") or envelope.get("session_id")
    if not session_id:
        return None
    time = info.get("time", {})
    deleted_at = envelope["occurred_at"] if event_type == "session.deleted" else None
    return {
        "session_id": session_id,
        "project_id": envelope["project_id"],
        "project_label": _text(envelope.get("project_label", ""), 500) or None,
        "title": _text(info.get("title", ""), 2000) or None,
        "directory_hash": hashlib.sha256(info.get("directory", "").encode("utf-8")).hexdigest()
        if info.get("directory")
        else None,
        "opencode_version": _text(info.get("version", ""), 200) or None,
        "started_at": _millis_to_iso(time.get("created")),
        "updated_at": _millis_to_iso(time.get("updated")) or envelope["occurred_at"],
        "deleted_at": deleted_at,
        "metadata": {"summary": info.get("summary"), "parent_id": info.get("parentID")},
    }


def tombstones_from_envelope(envelope: Dict[str, Any]) -> Iterable[Dict[str, str]]:
    payload = envelope.get("payload", {}).get("properties", {})
    if envelope["event_type"] == "message.part.removed" and payload.get("partID"):
        yield {"source_type": "part", "source_id": payload["partID"]}
    elif envelope["event_type"] == "message.removed" and payload.get("messageID"):
        yield {"message_id": payload["messageID"]}
    elif envelope["event_type"] == "session.deleted" and envelope.get("session_id"):
        yield {"session_id": envelope["session_id"]}


def _millis_to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
