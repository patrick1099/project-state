"""Rebind a physically moved output without copying or rewriting its contents."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .adapters import output, store


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def inspect_move(state_dir, out, from_out, from_state=None):
    state_dir, out = Path(output.canonical(state_dir)), Path(output.canonical(out))
    old_out = os.path.normcase(os.path.abspath(Path(from_out).expanduser()))
    old_state = os.path.normcase(os.path.abspath(Path(from_state or state_dir).expanduser()))
    if Path(old_out).exists() and output.canonical(old_out) != str(out):
        raise RuntimeError("old output still exists; move it explicitly before relocating")
    if not out.is_dir():
        raise FileNotFoundError("moved output directory does not exist: " + str(out))
    active = output.read_json(state_dir / output.ACTIVATION_FILE)
    owner = output.read_json(out / output.OWNER_FILE)
    paths = output.read_json(state_dir / output.PATHS_FILE)
    journal = output.read_json(state_dir / output.RELOCATION_FILE)
    keys = {"state": str(state_dir), "from_state": old_state, "from_out": old_out, "out": str(out)}
    if journal and journal.get("target") == keys:
        if journal.get("fingerprint") != fingerprint(journal["plan"]):
            raise ValueError("relocation journal fingerprint mismatch")
        plan = journal["plan"]
        before, after = plan["before"], plan["after"]
        pending = {**after["active"], "phase": "relocating"}
        if (active not in (before["active"], pending, after["active"])
                or owner not in (before["owner"], after["owner"])
                or paths not in (before["paths"], after["paths"])):
            raise RuntimeError("registration changed since relocation; inspect before retrying")
        return journal
    if journal and not journal.get("complete"):
        raise RuntimeError("another relocation is incomplete; retry its original parameters")
    output.check_owner_record(owner)
    if (not isinstance(active, dict) or not owner or active.get("token") != owner.get("token")
            or active.get("phase") not in ("enabled", "paused")
            or any(active.get(k) != owner.get(k) for k in ("version", "state", "out"))):
        raise RuntimeError("moved owner and activation are not a matching registered pair")
    if type(active.get("version")) is not int:
        raise ValueError("invalid registration version")
    version = active.get("version")
    if version == 1:
        if active.get("out") != old_out or active.get("state") != old_state:
            raise RuntimeError("old location does not match the registered paths")
    elif version == 2:
        if active.get("state") != "." or active.get("out") != "." or os.path.normcase(os.path.abspath(paths["output_root"])) != old_out:
            raise RuntimeError("old location does not match the local output mapping")
    else:
        raise ValueError("unsupported registration version")
    extras = {k: v for k, v in active.items() if k not in ("version", "state", "out", "token", "phase")}
    after_active = output.portable_record(active["token"], phase=active["phase"], **extras)
    after_owner = output.portable_record(active["token"])
    plan = {"before": {"active": active, "owner": owner, "paths": paths},
            "after": {"active": after_active, "owner": after_owner,
                      "paths": {"version": 1, "output_root": str(out)}}}
    return {"version": 1, "id": uuid.uuid4().hex, "at": time.time(), "target": keys,
            "plan": plan, "fingerprint": fingerprint(plan), "complete": False}


def relocate(home, out, from_out, from_state=None, dry_run=False):
    state_dir = Path(output.canonical(Path(home) / "sweep"))
    out = Path(output.canonical(out))
    with output.OutputLease(out, state_dir, switching=True, from_out=from_out, from_state=from_state), store.RunLock(state_dir):
        journal = inspect_move(state_dir, out, from_out, from_state)
        after = journal["plan"]["after"]
        write = lambda path, data: output.write_registration(path, data, dry_run=dry_run)
        archive = state_dir / "relocations" / (journal["id"] + ".json")
        write(archive, journal)
        write(state_dir / output.RELOCATION_FILE, journal)
        write(state_dir / output.ACTIVATION_FILE, {**after["active"], "phase": "relocating"})
        write(state_dir / output.PATHS_FILE, after["paths"])
        write(out / output.OWNER_FILE, after["owner"])
        write(state_dir / output.ACTIVATION_FILE, after["active"])
        receipt = {**journal, "complete": True}
        write(state_dir / output.RELOCATION_FILE, receipt)
        write(archive, receipt)
        if not dry_run:
            output.validate_owner(state_dir, out, enabled=after["active"]["phase"] == "enabled")
        return {"out": str(out), "registration_version": 2, "phase": after["active"]["phase"],
                "dry_run": bool(dry_run), "snapshot": str(archive), "contents_rewritten": False}
