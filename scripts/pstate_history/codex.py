"""Codex native JSONL adapter, with session-wide legacy detection."""
from collections import defaultdict
import json
import re
from .common import finish, message, text_blocks, object_value, parse_ts, clip, with_activity


def parse_all(files, warnings, counts):
    return parse(files, warnings, counts, include_excluded=True)


def parse(files, warnings, counts, include_excluded=False):
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
            if not include_excluded:
                continue
        recognized = any(m.get("source") in ("cli", "vscode", "app", "desktop") for m in metas)
        if not recognized and not bad:
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
        session = finish("codex", sid, messages, warnings, legacy)
        summary, activities = summarize(rows, metas, session["messages"])
        result.append(with_activity(session, summary, activities, rows, recognized and not bad, bad))
    return result


ACTIVITY_ITEMS = {"message", "function_call", "function_call_output", "custom_tool_call",
                  "custom_tool_call_output", "tool_call", "tool_result", "local_shell_call"}
ACTIVITY_EVENTS = {"user_message", "agent_message", "item_completed"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _write_paths(payload):
    name = str(payload.get("name") or "").rsplit(".", 1)[-1]
    arguments = payload.get("arguments", payload.get("input"))
    if name == "apply_patch":
        if isinstance(arguments, dict):
            arguments = arguments.get("patch") or arguments.get("input")
        if isinstance(arguments, str):
            return re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", arguments, re.M), True
    if name in WRITE_TOOLS:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return [], False
        args = object_value(arguments)
        path = args.get("file_path") or args.get("notebook_path")
        if isinstance(path, str) and path:
            return [path], False
    return [], False


def _succeeded(payload, patch):
    output = payload.get("output", payload.get("result"))
    if payload.get("is_error") or payload.get("isError"):
        return False
    if isinstance(output, str):
        try:
            obj = object_value(json.loads(output))
        except ValueError:
            obj = {}
    else:
        obj = object_value(output)
    if obj.get("is_error") or obj.get("isError") or obj.get("error"):
        return False
    metadata = object_value(obj.get("metadata"))
    code = obj.get("exit_code", metadata.get("exit_code"))
    if code is not None and code != 0:
        return False
    if patch:
        return "Success. Updated the following files:" in str(output) or code == 0
    return output is not None


def summarize(rows, metas, messages):
    cwd = branch = entrypoint = None
    for meta in metas:
        if cwd is None and isinstance(meta.get("cwd"), str):
            cwd = meta["cwd"]
        if isinstance(meta.get("source"), str) and entrypoint is None:
            entrypoint = meta["source"]
        info = object_value(meta.get("git") or meta.get("git_info"))
        branch = info.get("branch") or branch
    latest, calls, outputs, activities = None, {}, {}, []
    for _, _, row in rows:
        payload = object_value(row.get("payload"))
        if row.get("type") == "turn_context":
            if isinstance(payload.get("cwd"), str):
                cwd = payload["cwd"]
            info = object_value(payload.get("git") or payload.get("git_info"))
            branch = info.get("branch") or branch
        kind = payload.get("type")
        if not isinstance(kind, str):
            continue
        active = (row.get("type") == "response_item" and kind in ACTIVITY_ITEMS
                  or row.get("type") == "event_msg" and kind in ACTIVITY_EVENTS)
        if not active:
            continue
        stamp, ts = row.get("timestamp"), parse_ts(row.get("timestamp"))
        activities.append([row.get("type"), stamp, payload])
        if ts is not None and (latest is None or ts >= latest[0]):
            latest = (ts, stamp)
        if kind in {"function_call", "custom_tool_call", "tool_call"}:
            paths, patch = _write_paths(payload)
            if paths and payload.get("call_id"):
                calls[payload["call_id"]] = (paths, patch)
        elif kind in {"function_call_output", "custom_tool_call_output", "tool_result"} and payload.get("call_id"):
            outputs[payload["call_id"]] = payload
    written = []
    for call_id, (paths, patch) in calls.items():
        if call_id in outputs and _succeeded(outputs[call_id], patch):
            written.extend(paths)
    humans = [m for m in messages if m["role"] == "user"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    return {
        "cwd": cwd, "branch": branch, "entrypoint": entrypoint,
        "human_count": len(humans), "last_ts": latest[0] if latest else None,
        "version": latest[1] if latest else None,
        "first_human": clip(humans[0]["text"], 200) if humans else "",
        "last_human": clip(humans[-1]["text"]) if humans else "",
        "last_assistant": clip(assistants[-1]["text"]) if assistants else "",
        "recap": None, "written": list(dict.fromkeys(written))[-300:],
        "native_sources": [m.get("source") for m in metas if m.get("source") is not None],
        "thread_sources": list(dict.fromkeys(str(m["thread_source"]) for m in metas if m.get("thread_source"))),
        "originators": list(dict.fromkeys(str(m["originator"]) for m in metas if m.get("originator"))),
    }, activities
