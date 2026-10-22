"""Claude native JSONL adapter."""
from collections import defaultdict
from pathlib import Path
from .common import finish, message, text_blocks, object_value


def parse(files, warnings, counts):
    grouped = defaultdict(list)
    for path, rows in files:
        if "subagents" in {p.lower() for p in Path(path).parts}:
            counts["excluded"] += 1
            continue
        for item in rows:
            sid = item[2].get("sessionId") or item[2].get("session_id") or Path(path).stem
            grouped[str(sid)].append(item)
    result = []
    for sid, rows in grouped.items():
        human_rows = [row for _, _, row in rows if row.get("type") == "user"]
        has_origin = any(row.get("origin") is not None for row in human_rows)
        entrypoints = {str(row.get("entrypoint") or row.get("entryPoint") or object_value(row.get("origin")).get("entrypoint") or "") for _, _, row in rows if not row.get("isSidechain")}
        if entrypoints.intersection({"sdk-cli", "sdk", "headless", "exec"}):
            counts["excluded"] += 1
            continue
        messages = []
        confirmed = False
        for path, line, row in rows:
            if row.get("isMeta") or row.get("isSidechain") or row.get("isCompactSummary") or row.get("isCompact"):
                continue
            role = row.get("type")
            if role not in ("user", "assistant"):
                continue
            msg = row.get("message") or {}
            if not isinstance(msg, dict):
                continue
            if msg.get("role", role) != role:
                continue
            origin = object_value(row.get("origin"))
            entrypoint = row.get("entrypoint") or row.get("entryPoint") or origin.get("entrypoint")
            native = origin.get("kind") == "human" and (entrypoint in ("cli", "claude-vscode") or bool(entrypoints.intersection({"cli", "claude-vscode"})))
            if role == "user":
                if has_origin and not native:
                    continue
                if not has_origin and (row.get("toolUseResult") is not None or row.get("sourceToolAssistantUUID")):
                    continue
            text = text_blocks(msg.get("content"))
            item = message("claude", sid, path, line, row, role, text, row.get("uuid"), native)
            if item:
                messages.append(item)
                confirmed = confirmed or (role == "user" and native)
        legacy = not has_origin
        if not any(m["role"] == "user" for m in messages):
            counts["unknown"] += 1
            warnings.append({"kind": "unknown", "platform": "claude", "session": sid, "reason": "No recognized human conversation messages."})
            continue
        if legacy:
            counts["legacy"] += 1
        elif not confirmed:
            counts["unknown"] += 1
            continue
        result.append(finish("claude", sid, messages, warnings, legacy))
    return result