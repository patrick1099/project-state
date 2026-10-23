# 结构: vibe-scripts/toolkit; frozen review -> thread writer and native history.
# 用途: Persist an explicit multi-matter review with independent readback and seen last.
# 用法: pstate sweep review --input-file review.json --dry-run --json
# 原始需求: Partial captures remain unreviewed; replay the identical frozen input without duplicates.
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pstate_history
from pstate_history.common import redact
from pstate_threads import core, store
from pstate_threads.cli import verify


def input_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _plan(value):
    plan = copy.deepcopy(value)
    required = {"platform", "session", "review_version", "files", "items", "complete"}
    if not isinstance(plan, dict) or set(plan) != required:
        raise core.ThreadError("review requires platform/session/review_version/files/items/complete")
    for key in ("platform", "session", "review_version"):
        core.required_text(plan[key], key)
    if plan["platform"] not in pstate_history.PLATFORMS:
        raise core.ThreadError("unsupported review platform")
    if type(plan["complete"]) is not bool or not isinstance(plan["items"], list):
        raise core.ThreadError("complete must be boolean; items must be a list")
    files = plan["files"]
    if not isinstance(files, dict) or not files or set(files) - set(pstate_history.PLATFORMS):
        raise core.ThreadError("files must map registered platforms to explicit native log paths")
    for paths in files.values():
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise core.ThreadError("files values must be nonempty lists of log paths")
    if plan["platform"] not in files:
        raise core.ThreadError("files must include the reviewed platform")
    for entry in plan["items"]:
        if not isinstance(entry, dict) or "payload" not in entry or set(entry) - {"payload", "into", "rev", "distinct"}:
            raise core.ThreadError("each review item requires payload; optional into/rev/distinct")
        if type(entry.get("distinct", False)) is not bool:
            raise core.ThreadError("distinct must be an explicit boolean")
        if "into" in entry:
            core.required_text(entry["into"], "into")
            if type(entry.get("rev")) is not int or entry["rev"] < 1 or entry.get("distinct"):
                raise core.ThreadError("into requires positive rev and cannot combine distinct")
        elif "rev" in entry:
            raise core.ThreadError("rev requires into")
        if not isinstance(entry["payload"], dict):
            raise core.ThreadError("payload must be an add JSON object")
    return plan


def _load(plan):
    loaded = pstate_history.load_sessions({"files": plan["files"], "roots": {}})
    counts = loaded["coverage"]
    if counts.get("unreadable") or counts.get("malformed_lines"):
        raise core.ThreadError("review native logs are unavailable or incomplete", "E_IO", 1)
    selected = [s for s in loaded["sessions"]
                if s["platform"] == plan["platform"] and s["session"] == plan["session"]]
    if not selected or any(s["review_version"] != plan["review_version"] for s in selected):
        raise core.ThreadError("source review_version changed or native session is unavailable; rediscover before a new review",
                               "E_VALIDATION", 1)
    return loaded["sessions"]


def _source_matches(src, sessions):
    src = core.source(src)
    if "path" not in src:
        raise core.ThreadError("review sources require an explicit native path")
    path = Path(src["path"]).expanduser().resolve()
    for session in sessions:
        if (session["platform"], session["session"]) != (src["platform"], src["session"]):
            continue
        for message in session["messages"]:
            if (message["anchor"], message["time"], message["role"]) != (src["anchor"], src["time"], src["role"]):
                continue
            if Path(message["path"]).resolve() == path and src["excerpt"] in redact(message["text"]):
                return
    raise core.ThreadError("source excerpt/hash/role/anchor/time does not match the declared native log")


def _prepare(plan, sessions, state):
    shadow = copy.deepcopy(state)
    prepared = []
    for entry in plan["items"]:
        payload = entry["payload"]
        sources = payload.get("sources", [])
        if not isinstance(sources, list):
            raise core.ThreadError("sources must be a list")
        for src in sources:
            _source_matches(src, sessions)
        if not any(s["platform"] == plan["platform"] and s["session"] == plan["session"] for s in sources):
            raise core.ThreadError("every captured item must include a source from the reviewed session")
        quotations = core.fields({"confirmed": payload["confirmed"]})["confirmed"] if "confirmed" in payload else []
        for quotation in quotations:
            _source_matches(quotation["source"], sessions)
        # Explicit files establish read scope only. Native human confirmation uses the existing trust boundary.
        verified = {"confirmed_entries": [verify(q) for q in quotations]}
        core.evolve(shadow, "add", payload, datetime.now(timezone.utc).isoformat(),
                    uuid.uuid4().hex, entry.get("rev"), entry.get("into"), entry.get("distinct", False), verified)
        core.validate_state(shadow)
        prepared.append((entry, verified))
    return prepared


def _readback(state, entry, ident):
    item = core.get_item(state, ident)
    requested = entry["payload"]["sources"]
    actual = {(core.source_key(s), s["excerpt_hash"]) for s in item["sources"]}
    if any((core.source_key(s), s["excerpt_hash"]) not in actual for s in requested):
        raise core.ThreadError("capture readback lacks a requested source", "E_VERIFICATION_FAILED", 1)
    if entry.get("into"):
        matched = ident == entry["into"]
    else:
        title, sources = core.fingerprint(entry["payload"]["title"], requested)
        identity = item["capture_identity"]
        matched = identity["title"] == title and set(sources) <= {tuple(p) for p in identity["sources"]}
    if not matched:
        raise core.ThreadError("capture readback identity mismatch", "E_VERIFICATION_FAILED", 1)
    return {"id": ident, "rev": item["rev"], "state": item["state"]}


def review(home, value, dry_run=False):
    digest = input_hash(value)
    saved = []
    pending = None
    phase = "preflight"
    try:
        plan = _plan(value)
        sessions = _load(plan)
        directory = Path(home) / "threads"
        current = store.read(directory)
        prepared = _prepare(plan, sessions, current)
        preview = copy.deepcopy(current) if dry_run else None
        for index, (entry, verified) in enumerate(prepared):
            phase = "capture"
            pending = {"index": index, "into": entry.get("into")}
            result = store.mutate(directory, "add", entry["payload"], into=entry.get("into"),
                                  rev=entry.get("rev"), distinct=entry.get("distinct", False),
                                  verified=verified, dry_run=dry_run, preview_state=preview)
            pending["id"] = result["id"]
            phase = "capture_readback"
            actual = preview if dry_run else store.read(directory)
            receipt = _readback(actual, entry, result["id"])
            receipt.update(index=index, replayed=result.get("replayed", False), persisted=not dry_run)
            saved.append(receipt)
            pending = None
        if plan["complete"]:
            phase = "final_version"
            _load(plan)
            actual = preview if dry_run else store.read(directory)
            for (entry, _), receipt in zip(prepared, saved):
                _readback(actual, entry, receipt["id"])
            seen = {"platform": plan["platform"], "session": plan["session"], "version": plan["review_version"]}
            phase = "seen"
            store.mutate(directory, "seen", seen, dry_run=dry_run, preview_state=preview)
            actual = preview if dry_run else store.read(directory)
            if actual["seen"].get(plan["platform"] + ":" + plan["session"], {}).get("version") != plan["review_version"]:
                raise core.ThreadError("seen readback mismatch", "E_VERIFICATION_FAILED", 1)
        return {"input_hash": digest, "saved": saved, "complete": plan["complete"],
                "seen": plan["complete"] and not dry_run, "dry_run": bool(dry_run)}
    except Exception as exc:
        details = {"input_hash": digest, "saved": saved, "pending": pending, "phase": phase,
                   "recovery": "Inspect persisted threads, then retry the identical frozen input with this hash. Do not rename, re-split or add distinct automatically."}
        if isinstance(exc, core.ThreadError):
            details["cause"] = {"code": exc.code, "details": exc.details}
            code, status = ("E_PARTIAL_FAILURE", 1) if saved else (exc.code, exc.status)
        elif isinstance(exc, PermissionError):
            code, status = ("E_PARTIAL_FAILURE" if saved else "E_PERMISSION"), 1
        elif isinstance(exc, (OSError, TimeoutError)):
            code, status = ("E_PARTIAL_FAILURE" if saved else "E_IO"), 1
        else:
            code, status = ("E_PARTIAL_FAILURE" if saved else "E_INTERNAL"), 1
        raise core.ThreadError(str(exc), code, status, details) from exc
