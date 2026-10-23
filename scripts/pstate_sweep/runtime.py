# 结构: vibe-scripts/toolkit; runtime orchestrates shared history, model and output adapters.
# 用途: 内部后台扫描与联合回顾页，不改变事项生命周期。
# 用法: run_sweep with explicit adapters; CLI is pstate sweep.
# 原始需求: 合并后台发现和事项回顾，保留旧来源与全部勾选往返。
import copy
import time
from pathlib import Path

from pstate_history import PLATFORMS, load_sessions
from pstate_threads import store as thread_store
from . import core
from .adapters import docgate, store, output
from .ports import JudgeError


def require_enabled(home, state_dir, out_dir):
    return output.validate_owner(state_dir, out_dir)


def history_options(cfg):
    return {"roots": {name: cfg["history_roots"].get(name, []) if adapter.get("background") else []
                      for name, adapter in PLATFORMS.items()}}


def remember_legacy(state, identity, summary):
    token = summary.get("activity_version")
    native = summary.get("native_session")
    stamp = summary.get("version")
    adapter = PLATFORMS.get(summary.get("platform"), {})
    if token and native and stamp and adapter.get("sweep_hints"):
        state.setdefault("legacy_marks", {}).setdefault(core.closure_key(native, stamp),
            {"identity": identity, "activity_version": token})


def map_source_marks(marks, state):
    mapped = {}
    aliases = state.get("legacy_marks", {})
    for key, value in marks.items():
        alias = aliases.get(key)
        target = (core.closure_key(alias["identity"], alias["activity_version"]) if alias else key)
        if isinstance(value, bool):
            mapped[target] = mapped.get(target, True) and value
        else:
            mapped[target] = value
    return mapped


def refresh_sessions(state, loaded, now):
    sessions = state["sessions"]
    present = set()
    for session in loaded["sessions"]:
        adapter = PLATFORMS.get(session["platform"], {})
        if not adapter.get("background"):
            continue
        identity = session["identity"]
        present.add(identity)
        if not session.get("interactive"):
            if identity in sessions:
                sessions[identity]["summary"]["interactive"] = False
            continue
        summary = copy.deepcopy(session["summary"])
        old = sessions.get(identity)
        native = session["session"]
        if old is None and native in sessions and adapter.get("sweep_hints"):
            old = sessions.pop(native)
        entry = old or {}
        # Bind imported timestamp verdicts once, without inventing new source activity.
        previous = entry.get("summary", {})
        remember_legacy(state, identity, previous)
        remember_legacy(state, identity, summary)
        for field in ("verdict", "error", "owner_error"):
            record = entry.get(field)
            if (record and "activity_version" not in record
                    and record.get("version") == previous.get("version") == summary.get("version")):
                record["activity_version"] = previous.get("activity_version", summary["activity_version"])
        entry.update(summary=summary, paths=list(session.get("paths", [])), gone=False)
        entry.pop("gone_at", None)
        entry.pop("source_unavailable", None)
        sessions[identity] = entry
    for identity, entry in sessions.items():
        if identity in present:
            continue
        paths = entry.get("paths") or ([entry["path"]] if entry.get("path") else [])
        if not paths:
            entry["source_unavailable"] = "No source paths are available; original summary retained."
            continue
        try:
            missing = True
            for path in paths:
                try:
                    Path(path).stat()
                    missing = False
                except FileNotFoundError:
                    pass
        except OSError as exc:
            entry["source_unavailable"] = str(exc)
            continue
        if missing:
            entry.update(gone=True, gone_at=entry.get("gone_at", now))
        else:
            entry["source_unavailable"] = "Source was not returned by the shared adapter; summary retained."


def _changed_during(entry, loader, expected):
    if entry.get("gone"):
        return False
    summary = entry["summary"]
    platform = summary.get("platform")
    paths = entry.get("paths", [])
    if not platform or not paths:
        return False
    loaded = loader({"files": {platform: paths}}, include_excluded=True)
    fresh = next((s for s in loaded["sessions"] if s["identity"] == summary["identity"]), None)
    if fresh is None or not fresh.get("interactive"):
        entry["source_unavailable"] = "Source changed or became unavailable during judgment."
        return True
    entry.update(summary=copy.deepcopy(fresh["summary"]), paths=list(fresh.get("paths", [])), gone=False)
    return fresh["activity_version"] != expected


def judge_sessions(state, plan, judge, max_calls, max_failures, save, log, personal_paths=(), loader=load_sessions):
    calls = failures = discarded = 0
    for identity, mode in plan:
        if calls >= max_calls:
            break
        entry = state["sessions"][identity]
        summary = entry["summary"]
        token = summary.get("activity_version", summary["version"])
        hints = core.owner_hints(summary, personal_paths)
        request = core.make_request(identity, summary, mode, verdict=entry.get("verdict"), hints=hints)
        field = "owner_error" if mode == "owner" else "error"
        if mode == "resume" and not (summary.get("cwd") and Path(summary["cwd"]).is_dir()):
            entry[field] = core.record_failure(entry.get(field), "Original working directory is unavailable.",
                                               token, False, max_failures, time.time())
            failures += 1
            save()
            continue
        calls += 1
        try:
            raw = judge.judge(request)
            verdict = core.clean_owner(raw) if mode == "owner" else core.clean_verdict(raw)
        except (JudgeError, ValueError) as exc:
            entry[field] = core.record_failure(entry.get(field), str(exc), token,
                                               getattr(exc, "retry", True), max_failures, time.time())
            failures += 1
            log("Judgment failed: " + identity + ": " + str(exc))
            save()
            continue
        if _changed_during(entry, loader, token):
            discarded += 1
            save()
            continue
        if mode == "owner":
            entry["verdict"]["owner"] = verdict
        else:
            verdict.update(version=summary["version"], activity_version=token, source=mode, at=time.time())
            entry["verdict"] = verdict
            entry.pop("error", None)
        entry.pop("owner_error", None)
        save()
    return calls, failures, discarded


def doc_workspaces(state, now, idle_days, entrypoints, since_ts=None):
    ws = {}
    for entry in state["sessions"].values():
        summary = entry["summary"]
        if entry.get("gone") or not core.is_candidate(summary, now, idle_days, entrypoints, since_ts):
            continue
        if summary.get("cwd"):
            ws.setdefault(summary["cwd"], []).extend(summary.get("written") or [])
    return ws


def _combined_marks(texts, parser):
    result = {}
    for text in texts:
        for key, checked in parser(text).items():
            result[key] = result.get(key, True) and checked
    return result


def reconcile_marks(cfg, state, out, now, snapshot=None):
    out = Path(out)
    snapshot = snapshot or {"items": {}, "seen": {}}
    page_path, closed_path = out / cfg["page_name"], out / cfg["closed_name"]
    archive_dir = out / cfg["archive_dir"]
    # Read every checkbox only after all potentially slow judgments have finished.
    pending_text, closed_text = store.read_text(page_path), store.read_text(closed_path)
    archive_texts = store.read_generated(archive_dir, core.is_generated_archive)
    for identity, entry in state["sessions"].items():
        remember_legacy(state, identity, entry["summary"])
    archive_marks = map_source_marks(_combined_marks(archive_texts.values(), core.parse_marks), state)
    closures = core.merge_closures(map_source_marks(state.get("closures", {}), state),
        map_source_marks(core.parse_marks(pending_text), state),
        map_source_marks(core.parse_marks(closed_text), state),
        set(map_source_marks({key: True for key in core.parse_legacy_closed(closed_text)}, state)), now, archive_marks)
    state.setdefault("closure_history", {}).update(closures)
    state["closures"], _ = core.effective_closures(closures, state["sessions"])
    thread_closures = core.merge_closures(state.get("thread_closures"), core.parse_thread_marks(pending_text),
        core.parse_thread_marks(closed_text), set(), now,
        _combined_marks(archive_texts.values(), core.parse_thread_marks))
    valid_thread_keys = {t["id"] + "@" + str(t["rev"]) for t in snapshot["items"].values()}
    state.setdefault("thread_closure_history", {}).update(thread_closures)
    state["thread_closures"] = {key: value for key, value in thread_closures.items() if key in valid_thread_keys}
    return archive_texts


def write_pages(cfg, state, out, now, since_ts, doc_issues, doc_error, save,
                snapshot=None, dry_run=False):
    out = Path(out)
    snapshot = snapshot or {"items": {}, "seen": {}}
    page_path, closed_path = out / cfg["page_name"], out / cfg["closed_name"]
    archive_dir = out / cfg["archive_dir"]
    archive_texts = reconcile_marks(cfg, state, out, now, snapshot)
    save()
    items, queued, hidden = core.build_items(state["sessions"], state["closures"], now,
        cfg["idle_days"], cfg["entrypoints"], since_ts)
    core.annotate_sources(items, snapshot)
    reviewed = [i for i in items if i["reviewed"]]
    items = [i for i in items if not i["reviewed"]]
    items += core.thread_items(snapshot, state["thread_closures"], now)
    pending, closed, archives = core.layout(items, now, cfg["window_days"])
    reviewed_pending, reviewed_closed, reviewed_archives = core.layout(reviewed, now, cfg["window_days"])
    names = {"closed": cfg["closed_name"], "archive_dir": cfg["archive_dir"]}
    all_archives = {m: archives.get(m, []) + reviewed_archives.get(m, [])
                    for m in set(archives) | set(reviewed_archives)}
    text = core.render_page(pending, queued, len(closed), all_archives, doc_issues, doc_error, names, cfg["window_days"])
    written = store.write_page_if_changed(page_path, core.append_reviewed(text, reviewed_pending), dry_run=dry_run)
    text = core.render_closed_page(closed, cfg["page_name"], cfg["window_days"])
    closed_written = store.write_page_if_changed(closed_path, core.append_reviewed(text, reviewed_closed), dry_run=dry_run)
    keep = set()
    for monday in all_archives:
        path = archive_dir / core.archive_name(monday)
        keep.add(str(path))
        text = core.render_archive_page(monday, archives.get(monday, []), cfg["page_name"])
        written |= store.write_page_if_changed(path, core.append_reviewed(text, reviewed_archives.get(monday, [])), dry_run=dry_run)
    removed = [p for p in archive_texts if p not in keep
               and str(Path(p).relative_to(out)).replace("\\", "/") not in state.get("legacy_pages", {})
               and store.remove_generated(p, core.is_generated_archive, dry_run=dry_run)]
    return {"listed": len(pending), "closed_listed": len(closed), "archived": sum(map(len, all_archives.values())),
            "archive_files": len(all_archives), "archive_removed": len(removed), "hidden_work": hidden,
            "reviewed_sources": len(reviewed), "queued": queued, "page_written": written,
            "closed_written": closed_written, "page": str(page_path), "dry_run": bool(dry_run)}


def run_sweep(cfg, judge, gate_loader, out_dir, state_dir, now=None, max_calls=None, only_mode=None,
              no_judge=False, log=lambda *args: None, *, threads_dir=None, history_loader=load_sessions,
              history_input=None, dry_run=False):
    now = time.time() if now is None else now
    max_calls = cfg["max_calls"] if max_calls is None else max_calls
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError("max_calls must be a nonnegative integer")
    with output.OutputLease(out_dir, state_dir), store.RunLock(state_dir):
        # Validate every generated target before scanning sources or calling a model.
        output.guard(Path(out_dir) / cfg["page_name"], dry_run=dry_run)
        output.guard(Path(out_dir) / cfg["closed_name"], dry_run=dry_run)
        output.guard(Path(out_dir) / cfg["archive_dir"] / "_preflight.md", dry_run=dry_run)
        state = store.load_state(state_dir)
        loaded = history_loader(history_input if history_input is not None else history_options(cfg), include_excluded=True)
        refresh_sessions(state, loaded, now)
        save = lambda: store.save_state(state_dir, state, dry_run=dry_run)
        save()
        since_ts = cfg.get("since_ts")
        plan = [] if no_judge else core.plan_judgments(state["sessions"], now, cfg["idle_days"],
            cfg["entrypoints"], only_mode, since_ts)
        calls, failures, discarded = (0, 0, 0) if dry_run else judge_sessions(state, plan, judge, max_calls,
            cfg["max_failures"], save, log, cfg["personal_paths"], history_loader)
        doc_issues, doc_error = [], None
        try:
            gate = gate_loader()
            doc_issues = docgate.probe(gate, doc_workspaces(state, now, cfg["idle_days"], cfg["entrypoints"], since_ts))
        except Exception as exc:
            doc_error = type(exc).__name__ + ": " + str(exc)
        # The matter lock is short and is never held during any model call.
        snapshot = thread_store.read(threads_dir) if threads_dir is not None else {"items": {}, "seen": {}}
        pages = write_pages(cfg, state, Path(out_dir), now, since_ts, doc_issues, doc_error, save,
                            snapshot=snapshot, dry_run=dry_run)
        report = {"at": now, "candidates": sum(not e.get("gone") and core.is_candidate(e["summary"], now,
                   cfg["idle_days"], cfg["entrypoints"], since_ts) for e in state["sessions"].values()),
                  "planned": len(plan), "recap_planned": sum(m == "recap" for _, m in plan),
                  "owner_planned": sum(m == "owner" for _, m in plan), "calls": calls,
                  "failures": failures, "discarded": discarded, "doc_issues": len(doc_issues),
                  "doc_error": doc_error, "closed": len(state["closures"]),
                  "coverage": loaded.get("coverage", {}), "warnings": loaded.get("warnings", []), **pages}
        state["last_run"] = report
        save()
    return report
