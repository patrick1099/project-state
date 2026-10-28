"""Bounded conversation paging and native human source verification."""
import base64
import json
import os
from pathlib import Path
from . import claude, codex, sweep
from .common import digest, read_rows, redact

DEFAULT_ROOTS = {
    "claude": (Path.home() / ".claude" / "projects",),
    "codex": (Path.home() / ".codex" / "sessions", Path.home() / ".codex" / "archived_sessions"),
}
PLATFORMS = {
    "claude": {"parse": claude.parse, "parse_all": claude.parse_all, "imports": True,
               "sweep_hints": True, "background": True},
    "codex": {"parse": codex.parse, "parse_all": codex.parse_all, "background": False},
}


def roots_for(options, platform):
    configured = options.get("roots")
    values = configured.get(platform, ()) if configured is not None else DEFAULT_ROOTS.get(platform, ())
    if isinstance(values, (str, Path)):
        values = [values]
    return [Path(p).expanduser().resolve() for p in values]


def source_for(session, item, text=None):
    excerpt = (redact(item["text"] if text is None else text).strip() or redact(item["text"]).strip())[:400]
    return {"platform": session["platform"], "session": session["session"], "anchor": item["anchor"], "time": item["time"], "role": item["role"], "excerpt": excerpt, "excerpt_hash": digest(excerpt), "path": item["path"]}


def enumerate_paths(roots, warnings, counts):
    paths = set()
    for root in roots:
        if not root.exists():
            counts["missing_roots"] += 1
            warnings.append({"kind": "missing_root", "path": str(root)})
            continue
        if not root.is_dir():
            counts["unreadable"] += 1
            warnings.append({"kind": "unreadable", "path": str(root), "reason": "History root is not a directory."})
            continue
        def onerror(exc):
            counts["unreadable"] += 1
            warnings.append({"kind": "unreadable", "path": str(exc.filename), "reason": str(exc)})
        for directory, subdirs, names in os.walk(root, onerror=onerror, followlinks=False):
            subdirs[:] = sorted(s for s in subdirs if s != "subagents" and not Path(directory, s).is_symlink())
            for name in sorted(names):
                path = Path(directory, name)
                if name.endswith((".jsonl", ".jsonl.zst")) and not path.is_symlink():
                    paths.add(path.resolve())
    return sorted(paths, key=str)


def import_hints(roots, warnings):
    # Only inspect this specifically named mapping, never neighboring settings.
    checked = set()
    for root in roots:
        for path in (root / "external_agent_session_imports.json", root.parent / "external_agent_session_imports.json"):
            if path in checked or not path.is_file():
                continue
            checked.add(path)
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                warnings.append({"kind": "external_import_mapping", "path": str(path), "entries": len(value) if isinstance(value, (dict, list)) else 0, "reason": "Cross-platform imports may duplicate native sessions; compare sources before merging."})
            except (OSError, ValueError, UnicodeError) as exc:
                warnings.append({"kind": "import_mapping_unreadable", "path": str(path), "reason": str(exc)})


def _cursor(snapshot, position):
    return base64.urlsafe_b64encode(json.dumps({"snapshot": snapshot, "position": position}, separators=(",", ":")).encode()).decode()


def _position(cursor, snapshot):
    if not cursor:
        return [0, 0, 0]
    try:
        state = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        pos = state["position"]
        if state["snapshot"] != snapshot:
            raise ValueError("History changed; restart discovery without the stale cursor.")
        if not isinstance(pos, list) or len(pos) != 3 or any(type(n) is not int or n < 0 for n in pos):
            raise ValueError("Invalid cursor position.")
        return pos
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid or stale history cursor: %s" % exc) from exc


def load_sessions(options=None, include_excluded=False):
    """Read selected native logs once; consumers share summaries and identities."""
    options = options or {}
    warnings = []
    counts = {key: 0 for key in ("files", "unreadable", "missing_roots", "malformed_lines", "unknown", "legacy", "excluded", "seen", "unreviewed", "truncated", "gone")}
    sessions = []
    for platform, adapter in PLATFORMS.items():
        roots = roots_for(options, platform)
        if "files" in options:
            named = options["files"].get(platform, ())
            if isinstance(named, (str, Path)):
                named = [named]
            paths = sorted({Path(p).expanduser().resolve() for p in named}, key=str)
        else:
            paths = enumerate_paths(roots, warnings, counts)
        counts["files"] += len(paths)
        files = [(str(path), read_rows(path, warnings, counts)) for path in paths]
        parser = adapter.get("parse_all", adapter["parse"]) if include_excluded else adapter["parse"]
        sessions.extend(parser(files, warnings, counts))
        if adapter.get("imports") and "files" not in options:
            import_hints(roots, warnings)
    return {"sessions": sessions, "coverage": counts, "warnings": warnings}


def discover(options=None):
    options = options or {}
    limit = options.get("limit", 20)
    max_chars = options.get("max_chars", 24000)
    if type(limit) is not int or limit < 1 or type(max_chars) is not int or max_chars < 1:
        raise ValueError("limit and max_chars must be positive integers")
    loaded = load_sessions(options)
    sessions, counts, warnings = loaded["sessions"], loaded["coverage"], loaded["warnings"]
    hints = sweep.hints(options.get("sweep_state"), warnings)
    counts["gone"] = sum(bool(h.get("gone")) for h in hints.values())
    excluded = set(options.get("exclude_sessions") or ())
    seen = options.get("seen") or {}
    selected = []
    for session in sessions:
        key = "%s:%s" % (session["platform"], session["session"])
        if session["session"] in excluded or key in excluded:
            counts["excluded"] += 1
            continue
        if options.get("since") and (session["latest_time"] or "") < str(options["since"]):
            continue
        value = seen.get(key)
        version = value.get("version") if isinstance(value, dict) else value
        session["reviewed"] = version == session["version"]
        if session["reviewed"]:
            counts["seen"] += 1
        if PLATFORMS[session["platform"]].get("sweep_hints") and session["session"] in hints:
            session["sweep_hint"] = hints[session["session"]]
        selected.append(session)
    selected.sort(key=lambda s: (s["latest_time"] or "", s["platform"], s["session"]), reverse=True)
    counts["unreviewed"] = sum(not s["reviewed"] for s in selected)
    snapshot = digest(json.dumps([[s["platform"], s["session"], s["version"]] for s in selected], separators=(",", ":")))
    si, mi, offset = _position(options.get("cursor"), snapshot)
    if si > len(selected) or (si == len(selected) and (mi or offset)):
        raise ValueError("Cursor position outside available sessions")
    result, remaining = [], max_chars
    while si < len(selected) and len(result) < limit and remaining:
        session = selected[si]
        if session["reviewed"]:
            si += 1
            mi = offset = 0
            continue
        messages = session["messages"]
        if mi >= len(messages):
            raise ValueError("Cursor message outside available session")
        start = [mi, offset]
        package = {key: value for key, value in session.items() if key not in {"messages", "reviewed", "summary"}}
        package["messages"] = []
        package["sources"] = []
        while mi < len(messages) and remaining:
            item = messages[mi]
            text = redact(item["text"])
            if offset >= len(text):
                raise ValueError("Cursor offset outside message")
            fragment = text[offset:offset + remaining]
            next_offset = offset + len(fragment)
            partial = next_offset < len(text)
            source = source_for(session, item, fragment)
            package["messages"].append({"role": item["role"], "text": fragment, "source": source, "offset": offset, "next_offset": next_offset if partial else None, "truncated": partial, "total_chars": len(text)})
            package["sources"].append(source)
            remaining -= len(fragment)
            if partial:
                offset = next_offset
                break
            mi += 1
            offset = 0
        package["start"] = {"message": start[0], "offset": start[1]}
        package["complete"] = start == [0, 0] and mi == len(messages)
        package["has_more"] = mi < len(messages)
        if not package["complete"]:
            counts["truncated"] += 1
        result.append(package)
        if mi == len(messages):
            si += 1
            mi = offset = 0
    next_cursor = _cursor(snapshot, [si, mi, offset]) if si < len(selected) else None
    counts["returned_sessions"] = len(result)
    counts["remaining_sessions"] = sum(not s["reviewed"] for s in selected[si:])
    counts["earliest_readable_time"] = min((s["earliest_time"] for s in sessions if s["earliest_time"]), default=None)
    counts["history_complete"] = False
    counts["note"] = "Coverage describes readable local files only; deleted or expired history cannot be recovered. Discovery does not mark sessions reviewed."
    return {"sessions": result, "next_cursor": next_cursor, "coverage": counts, "warnings": warnings}


def verify_source(source, options=None):
    options = options or {}
    try:
        if not isinstance(source, dict):
            return {"verified": False, "reason": "Source must be an object."}
        for key in ("platform", "session", "anchor", "time", "role", "excerpt", "excerpt_hash", "path"):
            if not isinstance(source.get(key), str) or (not source[key] and key != "time"):
                return {"verified": False, "reason": "Missing or invalid source field: " + key}
        if source["role"] != "user":
            return {"verified": False, "reason": "Only native human user messages can verify a decision."}
        platform = source["platform"]
        if platform not in PLATFORMS:
            return {"verified": False, "reason": "Unsupported source platform."}
        if digest(source["excerpt"]) != source["excerpt_hash"]:
            return {"verified": False, "reason": "Excerpt hash mismatch."}
        path = Path(source["path"]).expanduser().resolve()
        roots = roots_for(options, platform)
        explicit = (options.get("files") or {}).get(platform, ())
        if isinstance(explicit, (str, Path)):
            explicit = [explicit]
        permitted = any(path.is_relative_to(root) for root in roots) or path in {Path(p).expanduser().resolve() for p in explicit}
        if not permitted or "subagents" in {p.lower() for p in path.parts}:
            return {"verified": False, "reason": "Source path is outside the platform history roots."}
        counts = {key: 0 for key in ("unreadable", "malformed_lines", "excluded", "unknown", "legacy")}
        warnings = []
        rows = read_rows(path, warnings, counts)
        if counts["unreadable"] or counts["malformed_lines"]:
            return {"verified": False, "reason": "Source is missing, unreadable, incomplete, or not yet durably written."}
        sessions = PLATFORMS[platform]["parse"]([(str(path), rows)], warnings, counts)
        for session in sessions:
            if session["session"] != source["session"] or session["platform"] != platform:
                continue
            for item in session["messages"]:
                if item["anchor"] != source["anchor"] or item["role"] != "user":
                    continue
                if not item["native_human"]:
                    return {"verified": False, "reason": "Legacy or fallback message lacks confirmed native human provenance."}
                if item["time"] != source["time"]:
                    return {"verified": False, "reason": "Native message timestamp mismatch."}
                if source["excerpt"] not in redact(item["text"]):
                    return {"verified": False, "reason": "Excerpt does not match the native human message."}
                return {"verified": True, "reason": "Matched native human session, message anchor, time, and excerpt."}
        return {"verified": False, "reason": "No matching native human message in the declared session."}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {"verified": False, "reason": "Source verification failed: " + str(exc)}