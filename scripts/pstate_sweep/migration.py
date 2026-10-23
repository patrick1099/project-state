"""Explicit, loss-preserving legacy import and forward-only writer switching."""
import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from pstate_threads import store as thread_store
from . import core, runtime
from .adapters import output, store


class LegacyLock:
    """Reserve the old O_EXCL lock; never guess process liveness or steal by age."""

    def __init__(self, path, reservation=None):
        self.path = Path(path)
        self.reservation = reservation
        self.created = False
        self.keep = False

    def signature(self):
        stat = self.path.stat()
        return {"path": output.canonical(self.path), "content": self.path.read_text(encoding="ascii"),
                "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            if self.reservation and self.signature() == self.reservation:
                return self
            raise RuntimeError("legacy sweep is busy or its run.lock needs explicit operator recovery: "
                               + str(self.path)) from exc
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        self.created = True
        return self

    def __exit__(self, *exc):
        if self.created and not self.keep:
            self.path.unlink(missing_ok=True)


def page_snapshot(cfg, out):
    out = Path(out)
    pages = {}
    output.check_path(out / cfg["page_name"])
    output.check_path(out / cfg["closed_name"])
    output.check_path(out / cfg["archive_dir"] / "_preflight.md")
    for name in (cfg["page_name"], cfg["closed_name"]):
        path = out / name
        try:
            pages[name] = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
    for path, text in store.read_generated(out / cfg["archive_dir"], core.is_generated_archive).items():
        pages[str(Path(path).relative_to(out)).replace("\\", "/")] = text
    return pages


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def merge_legacy(state, legacy, pages, legacy_path, out, now):
    if (not isinstance(legacy, dict) or legacy.get("version") != 1
            or not isinstance(legacy.get("sessions"), dict)):
        raise ValueError("invalid or unsupported legacy state")
    # Keep the complete original record, including fields unknown to this version.
    material = {"path": output.canonical(legacy_path), "out": output.canonical(out),
                "state": legacy, "pages": pages}
    digest = _digest(material)
    imports = state.setdefault("legacy_imports", {})
    repeated = digest in imports
    imports.setdefault(digest, material)
    conflicts = []
    imported = 0
    for sid, original in legacy["sessions"].items():
        if not isinstance(sid, str) or not isinstance(original, dict) or not isinstance(original.get("summary"), dict):
            raise ValueError("invalid legacy session entry")
        identity = sid if sid.startswith("claude:") else "claude:" + sid
        entry = copy.deepcopy(original)
        summary = entry["summary"]
        summary.setdefault("platform", "claude")
        summary.setdefault("native_session", identity.partition(":")[2])
        summary.setdefault("identity", identity)
        if not entry.get("paths") and entry.get("path"):
            entry["paths"] = [entry["path"]]
        # No transcript access is needed to preserve existing material.
        entry.setdefault("source_unavailable", "Imported source summary; original availability has not been verified.")
        existing = state["sessions"].get(identity)
        if existing is None:
            state["sessions"][identity] = entry
            imported += 1
        elif existing != entry:
            conflicts.append({"identity": identity, "resolution": "current retained; legacy original in legacy_imports",
                              "legacy_import": digest})
    # Legacy imports never overwrite a closure produced by the current writer.
    for key, closure in legacy.get("closures", {}).items():
        if not isinstance(closure, dict) or not isinstance(closure.get("at"), (int, float)):
            raise ValueError("invalid legacy closure")
        normalized = next(iter(runtime.map_source_marks({key: closure}, state)))
        if not repeated and normalized not in state.get("closure_history", {}):
            state.setdefault("closures", {}).setdefault(normalized, copy.deepcopy(closure))
    for key, alias in legacy.get("legacy_marks", {}).items():
        state.setdefault("legacy_marks", {}).setdefault(key, copy.deepcopy(alias))
    # Full original generated archives remain available at the original relative paths.
    for name, text in pages.items():
        state.setdefault("legacy_pages", {}).setdefault(name, text)
    state.setdefault("legacy_closures", {}).update(copy.deepcopy(legacy.get("closures", {})))
    state.setdefault("legacy_last_runs", {}).setdefault(digest, copy.deepcopy(legacy.get("last_run")))
    return {"imported": imported, "repeated": repeated, "conflicts": conflicts, "legacy_import": digest}


def _load_legacy(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _reconcile(cfg, state, out, now, home=None):
    snapshot = thread_store.read(Path(home) / "threads") if home is not None else {"items": {}, "seen": {}}
    runtime.reconcile_marks(cfg, state, out, now, snapshot)
    return snapshot


def import_legacy(cfg, home, legacy_state, out, dry_run=False):
    home, out = Path(home), Path(out)
    state_dir = home / "sweep"
    active = output.activation(state_dir)
    if active and active["out"] != output.canonical(out):
        raise RuntimeError("this state home is bound to another output")
    reservation = (active or {}).get("legacy_lock")
    lock_path = Path(legacy_state).parent / "run.lock"
    with output.OutputLease(out, state_dir, switching=True), store.RunLock(state_dir), LegacyLock(lock_path, reservation):
        state = store.load_state(state_dir)
        pages = page_snapshot(cfg, out)
        report = merge_legacy(state, _load_legacy(legacy_state), pages, legacy_state, out, time.time())
        _reconcile(cfg, state, out, time.time(), home)
        store.save_state(state_dir, state, dry_run=dry_run)
        return {**report, "pages_preserved": len(pages), "dry_run": bool(dry_run)}


def switch(cfg, home, out, legacy_state=None, dry_run=False):
    home, out = Path(home), Path(out)
    state_dir = home / "sweep"
    with output.OutputLease(out, state_dir, switching=True), store.RunLock(state_dir):
        active = output.activation(state_dir) or {}
        if active.get("out") and active["out"] != output.canonical(out):
            raise RuntimeError("this state home is bound to another output")
        legacy_state = legacy_state or active.get("legacy_state")
        pages = page_snapshot(cfg, out)
        if pages and not legacy_state and not active:
            raise ValueError("existing output requires an explicit --legacy-state import")
        lock = LegacyLock(Path(legacy_state).parent / "run.lock", active.get("legacy_lock")) if legacy_state else None
        from contextlib import nullcontext
        with lock or nullcontext():
            state = store.load_state(state_dir)
            report = {"imported": 0, "conflicts": []}
            if legacy_state:
                report = merge_legacy(state, _load_legacy(legacy_state), pages, legacy_state, out, time.time())
            # Snapshot and final checkbox read happen while both writers are excluded.
            current = {"sources": copy.deepcopy({k: v for k, v in state.items()
                       if k not in ("switch_snapshots", "rollback_snapshots")}), "pages": page_snapshot(cfg, out),
                       "activation": copy.deepcopy(active)}
            snapshot = _reconcile(cfg, state, out, time.time(), home)
            current["threads"] = copy.deepcopy(snapshot)
            snapshots = state.setdefault("switch_snapshots", {})
            snapshots.setdefault(_digest(current), current)
            token = active.get("token") or uuid.uuid4().hex
            pending = {"version": 1, "state": output.canonical(state_dir), "out": output.canonical(out),
                       "token": token, "phase": "switching", "legacy_state": str(legacy_state) if legacy_state else None}
            if lock:
                pending["legacy_lock"] = lock.signature()
            store.save_state(state_dir, state, dry_run=dry_run)
            if dry_run:
                # The same ownership and low-level write gates run against a virtual activation.
                with output.preview_activation(pending):
                    result = runtime.write_pages(cfg, state, out, time.time(), cfg.get("since_ts"), [], None,
                        lambda: store.save_state(state_dir, state, dry_run=True), snapshot=snapshot, dry_run=True)
                return {**report, **result, "phase": "preview", "dry_run": True}
            store.write_atomic(state_dir / output.ACTIVATION_FILE, json.dumps(pending))
            if lock:
                lock.keep = True
            store.write_atomic(Path(output.canonical(out)) / output.OWNER_FILE,
                               json.dumps({key: pending[key] for key in ("version", "state", "out", "token")}))
            result = runtime.write_pages(cfg, state, out, time.time(), cfg.get("since_ts"), [], None,
                lambda: store.save_state(state_dir, state), snapshot=snapshot)
            pending["phase"] = "enabled"
            store.write_atomic(state_dir / output.ACTIVATION_FILE, json.dumps(pending))
            return {**report, **result, "phase": "enabled", "dry_run": False}


def rollback(cfg, home, out, dry_run=False):
    state_dir = Path(home) / "sweep"
    with output.OutputLease(out, state_dir, switching=True), store.RunLock(state_dir):
        active = output.validate_owner(state_dir, out, enabled=False)
        state = store.load_state(state_dir)
        # Current decisions and source materials survive; the old snapshot is never restored.
        _reconcile(cfg, state, out, time.time(), home)
        pages = page_snapshot(cfg, out)
        state.setdefault("rollback_snapshots", {}).setdefault(_digest(pages), pages)
        store.save_state(state_dir, state, dry_run=dry_run)
        active["phase"] = "paused"
        if not dry_run:
            store.write_atomic(state_dir / output.ACTIVATION_FILE, json.dumps(active))
        return {"phase": "paused", "pages_preserved": len(pages), "legacy_writer_restored": False,
                "recovery": "switch forward after reconciliation; no old snapshot is restored", "dry_run": bool(dry_run)}
