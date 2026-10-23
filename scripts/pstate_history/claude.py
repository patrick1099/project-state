"""Claude native JSONL adapter."""
from collections import defaultdict
from pathlib import Path
import json
import re
from .common import finish, message, text_blocks, object_value, parse_ts, clip, with_activity


def parse_all(files, warnings, counts):
    return parse(files, warnings, counts, include_excluded=True)


def parse(files, warnings, counts, include_excluded=False):
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
        headless = bool(entrypoints.intersection({"sdk-cli", "sdk", "headless", "exec"}))
        if headless:
            counts["excluded"] += 1
            if not include_excluded:
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
        summary, activities = summarize(rows)
        if not any(m["role"] == "user" for m in messages) and not (include_excluded and (headless or summary["human_count"])):
            counts["unknown"] += 1
            warnings.append({"kind": "unknown", "platform": "claude", "session": sid, "reason": "No recognized human conversation messages."})
            continue
        if legacy:
            counts["legacy"] += 1
        elif not confirmed and not headless and not (include_excluded and summary["human_count"]):
            counts["unknown"] += 1
            continue
        session = finish("claude", sid, messages, warnings, legacy)
        result.append(with_activity(session, summary, activities, rows, not headless, headless))
    return result


WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
RECAP_SUFFIX = re.compile(r"\s*\(disable recaps in /config\)\s*$")


def _human(row):
    if row.get("type") != "user" or row.get("isMeta") or row.get("isSidechain"):
        return False
    origin = row.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    content = object_value(row.get("message")).get("content")
    if isinstance(content, str):
        return not content.lstrip().startswith("<")
    if isinstance(content, list):
        kinds = {item.get("type") for item in content if isinstance(item, dict)}
        return "tool_result" not in kinds and bool(kinds & {"text", "image"})
    return False


def summarize(rows):
    """Native activity summary consumed by the background without parsing again."""
    cwd = branch = entrypoint = None
    human_count, first_human, last_human, last_assistant = 0, "", "", ""
    latest, recap = None, None
    calls, ok_ids, activities, humans = [], set(), [], set()
    for _, _, row in sorted(rows, key=lambda item: (parse_ts(item[2].get("timestamp")) or 0, item[1], item[0])):
        if cwd is None and isinstance(row.get("cwd"), str):
            cwd = row["cwd"]
        if isinstance(row.get("gitBranch"), str) and row["gitBranch"]:
            branch = row["gitBranch"]
        ep = row.get("entrypoint") or row.get("entryPoint") or object_value(row.get("origin")).get("entrypoint")
        if entrypoint is None and isinstance(ep, str):
            entrypoint = ep
        stamp, ts = row.get("timestamp"), parse_ts(row.get("timestamp"))
        if row.get("type") == "system" and row.get("subtype") == "away_summary":
            text = RECAP_SUFFIX.sub("", str(row.get("content") or "")).strip()
            if ts is not None and text and (recap is None or ts >= recap["ts"]):
                recap = {"ts": ts, "text": text}
            continue
        if row.get("type") not in ("user", "assistant") or row.get("isSidechain"):
            continue
        content = object_value(row.get("message")).get("content")
        activities.append([row.get("type"), stamp, row.get("uuid"), content])
        if ts is not None and (latest is None or ts >= latest[0]):
            latest = (ts, stamp)
        if _human(row):
            key = row.get("uuid") or json.dumps([stamp, content], ensure_ascii=False, sort_keys=True)
            if key not in humans:
                humans.add(key)
                human_count += 1
            last_human = text_blocks(content)
            if not first_human:
                first_human = last_human
        elif row.get("type") == "assistant":
            text = text_blocks(content)
            if text.strip():
                last_assistant = text
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if row.get("type") == "assistant" and item.get("type") == "tool_use" and item.get("name") in WRITE_TOOLS:
                inp = object_value(item.get("input"))
                path = inp.get("file_path") or inp.get("notebook_path")
                if item.get("id") and isinstance(path, str) and path:
                    calls.append((item["id"], path))
            elif item.get("type") == "tool_result" and not item.get("is_error") and item.get("tool_use_id"):
                ok_ids.add(item["tool_use_id"])
    written = list(dict.fromkeys(path for call_id, path in calls if call_id in ok_ids))[-300:]
    return {
        "cwd": cwd, "branch": branch, "entrypoint": entrypoint,
        "human_count": human_count, "last_ts": latest[0] if latest else None,
        "version": latest[1] if latest else None, "first_human": clip(first_human, 200),
        "last_human": clip(last_human), "last_assistant": clip(last_assistant),
        "recap": recap, "written": written,
        "origin_kinds": sorted({str(object_value(row.get("origin")).get("kind") or "") for _, _, row in rows}),
        "entrypoints": sorted({str(row.get("entrypoint") or row.get("entryPoint") or object_value(row.get("origin")).get("entrypoint") or "") for _, _, row in rows}),
    }, activities
