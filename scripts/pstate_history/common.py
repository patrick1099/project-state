"""Shared parsing primitives; these functions never persist history state."""
import hashlib
import io
from compression import zstd
import json
import re
from pathlib import Path
from datetime import datetime


def object_value(value):
    return value if isinstance(value, dict) else {}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean(text):
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def redact(text):
    text = re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+)\b", "[REDACTED]", text)
    return re.sub(r"(?im)((?:api[_ -]?key|access[_ -]?token|password|secret|authorization)\s*[:=]\s*)(?:Bearer\s+)?[^\s,;]+", r"\1[REDACTED]", text)


def text_blocks(value):
    if isinstance(value, str):
        return clean(value)
    if not isinstance(value, list):
        return ""
    return "\n".join(clean(x["text"]) for x in value if isinstance(x, dict) and x.get("type", "text") in ("text", "input_text", "output_text") and isinstance(x.get("text"), str))


def injected(text):
    stripped = text.lstrip()
    prefixes = ("<local-command-caveat>", "<command-name>", "<command-message>", "<local-command-stdout>", "Message Type: NEW_TASK", "Message Type: MESSAGE", "Message Type: FINAL_ANSWER", "<environment_context>", "<permissions instructions>", "<system-reminder>", "<task-notification>", "<subagent_notification>", "<agent_notification>", "<INSTRUCTIONS>", "# AGENTS.md instructions", "# AGENTS.md", "This session is being continued from a previous conversation", "This conversation was compacted", "[Automated continuation", "[Auto continuation", "[Request interrupted by user")
    return not stripped or stripped.startswith(prefixes)


def read_rows(path, warnings, counts):
    rows = []
    try:
        if str(path).endswith(".jsonl.zst"):
            # Validate the entire frame before exposing any rows to discovery/verification.
            stream = io.StringIO(zstd.decompress(Path(path).read_bytes()).decode("utf-8-sig"))
        else:
            stream = Path(path).open(encoding="utf-8-sig")
        with stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("row is not an object")
                except (ValueError, TypeError):
                    counts["malformed_lines"] += 1
                    warnings.append({"kind": "malformed_line", "path": str(path), "line": number})
                    continue
                rows.append((str(Path(path).resolve()), number, row))
    except (OSError, UnicodeError, zstd.ZstdError) as exc:
        rows.clear()
        counts["unreadable"] += 1
        warnings.append({"kind": "unreadable", "path": str(path), "reason": str(exc)})
    return rows


def message(platform, sid, path, line, row, role, text, anchor, native_human):
    text = clean(text)
    if role == "user":
        text = re.sub(r"<(ide_opened_file|ide_selection|system-reminder)(?:\s[^>]*)?>.*?</\1\s*>", "", text, flags=re.DOTALL).strip()
    if injected(text):
        return None
    native_anchor = bool(anchor)
    anchor = str(anchor) if anchor else "line:%s:%s" % (line, digest(text)[:16])
    return {"role": role, "text": text, "time": str(row.get("timestamp") or row.get("time") or ""), "anchor": anchor, "path": path, "line": line, "native_human": bool(native_human and native_anchor), "native_anchor": native_anchor}


def finish(platform, sid, messages, warnings, legacy=False):
    unique = {}
    for item in messages:
        if item is None:
            continue
        # IDs deduplicate archive copies without merging two genuine equal utterances.
        key = (item["role"], item["anchor"]) if item["native_anchor"] else (item["role"], item["time"], digest(item["text"]))
        prior = unique.get(key)
        if prior is None or len(item["text"]) > len(prior["text"]):
            unique[key] = item
    ordered = sorted(unique.values(), key=lambda m: (m["time"], m["line"], m["path"]))
    version = digest(json.dumps([[m["role"], m["anchor"], m["time"], m["text"]] for m in ordered], ensure_ascii=False, separators=(",", ":")))
    if legacy:
        warnings.append({"kind": "legacy", "platform": platform, "session": sid, "reason": "Human provenance is incomplete; source verification remains unavailable."})
    if any(not m["native_anchor"] for m in ordered):
        warnings.append({"kind": "fallback_anchor", "platform": platform, "session": sid})
    return {"platform": platform, "session": sid, "version": version, "review_version": version, "messages": ordered, "legacy": legacy, "paths": sorted({m["path"] for m in ordered}), "earliest_time": min((m["time"] for m in ordered if m["time"]), default=None), "latest_time": max((m["time"] for m in ordered if m["time"]), default=None)}


def parse_ts(stamp):
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        return None


def clip(text, limit=1500):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\u2026"


def with_activity(session, summary, activities, rows, interactive, headless=False):
    """Keep body review and native activity independent, including archive copies."""
    events = {json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for event in activities}
    activity_version = digest(json.dumps(sorted(events), ensure_ascii=False, separators=(",", ":")))
    identity = session["platform"] + ":" + session["session"]
    recap, last = summary.get("recap"), summary.get("last_ts")
    summary.update(
        platform=session["platform"], native_session=session["session"], identity=identity,
        review_version=session["review_version"], activity_version=activity_version,
        interactive=bool(interactive), headless=bool(headless),
        recap_fresh=bool(recap) and last is not None and recap["ts"] >= last,
    )
    summary["owner_hints"] = {"cwd": summary.get("cwd") or "", "written": list(summary.get("written") or [])}
    session.update(identity=identity, activity_version=activity_version,
                   activity_time=summary.get("version"), interactive=bool(interactive),
                   headless=bool(headless), summary=summary)
    session["paths"] = sorted({path for path, _, _ in rows})
    return session
