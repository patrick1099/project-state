"""Codex native JSONL adapter, with session-wide legacy detection."""
from collections import defaultdict
from .common import finish, message, text_blocks, object_value


def parse(files, warnings, counts):
    grouped = defaultdict(list)
    metadata = defaultdict(list)
    for path, rows in files:
        metas = [object_value(r.get("payload")) for _, _, r in rows if r.get("type") == "session_meta"]
        meta = next((m for m in metas if isinstance(m, dict) and m.get("id")), None)
        if meta is None or any(m.get("id") != meta["id"] for m in metas):
            counts["unknown"] += 1
            warnings.append({"kind": "unknown", "platform": "codex", "path": path, "reason": "Missing native session metadata."})
            continue
        sid = str(meta["id"])
        grouped[sid].extend(rows)
        metadata[sid].extend(metas)
    result = []
    for sid, rows in grouped.items():
        metas = metadata[sid]
        bad = any(isinstance(m.get("source"), dict) or m.get("source") in ("exec", "headless", "guardian_review", "agent_created_thread") or m.get("thread_source") not in (None, "user") for m in metas)
        if bad:
            counts["excluded"] += 1
            continue
        recognized = any(m.get("source") in ("cli", "vscode", "app", "desktop") for m in metas)
        if not recognized:
            counts["unknown"] += 1
            warnings.append({"kind": "unknown", "platform": "codex", "session": sid, "reason": "Unknown session source."})
            continue
        modern = any(row.get("type") == "event_msg" and isinstance(row.get("payload"), dict) and row["payload"].get("type") == "item_completed" and isinstance(row["payload"].get("item"), dict) and row["payload"]["item"].get("type") == "UserMessage" for _, _, row in rows)
        old_events = any(row.get("type") == "event_msg" and object_value(row.get("payload")).get("type") == "user_message" for _, _, row in rows)
        legacy = not modern
        messages = []
        for path, line, row in rows:
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            role, text, anchor, native = None, "", None, False
            if row.get("type") == "event_msg":
                item = payload.get("item") or {}
                if modern and payload.get("type") == "item_completed" and isinstance(item, dict) and item.get("type") == "UserMessage":
                    role, text, anchor, native = "user", text_blocks(item.get("content")), item.get("id"), True
                elif legacy and payload.get("type") == "user_message":
                    role, text, anchor = "user", text_blocks(payload.get("message")), payload.get("id")
            elif row.get("type") == "response_item" and payload.get("type") == "message":
                if payload.get("role") == "assistant" and payload.get("channel") not in ("analysis", "justify", "confidence", "summary"):
                    role, text, anchor = "assistant", text_blocks(payload.get("content")), payload.get("id")
                elif legacy and not old_events and payload.get("role") == "user":
                    role, text, anchor = "user", text_blocks(payload.get("content")), payload.get("id")
            if role:
                messages.append(message("codex", sid, path, line, row, role, text, anchor, native))
        messages = [m for m in messages if m]
        if not any(m["role"] == "user" for m in messages):
            counts["unknown"] += 1
            warnings.append({"kind": "unknown", "platform": "codex", "session": sid, "reason": "No recognized human conversation messages."})
            continue
        if legacy:
            counts["legacy"] += 1
        result.append(finish("codex", sid, messages, warnings, legacy))
    return result