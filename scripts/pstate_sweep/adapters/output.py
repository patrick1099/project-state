"""Output ownership and a kernel-held lease shared across every state home."""
import contextvars
from contextlib import contextmanager
import hashlib
import json
import os
import tempfile
from pathlib import Path

from . import store

OWNER_FILE = ".pstate-sweep-owner.json"
ACTIVATION_FILE = "activation.json"
_CURRENT = contextvars.ContextVar("pstate_sweep_output_lease", default=None)
_PREVIEW = contextvars.ContextVar("pstate_sweep_preview", default=None)


def canonical(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


PATHS_FILE = "paths.json"
RELOCATION_FILE = "relocation.json"


def local_output(state_dir):
    data = read_json(Path(state_dir) / PATHS_FILE)
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("output_root"), str) or not Path(data["output_root"]).is_absolute()):
        raise ValueError("missing or invalid local output mapping; use sweep relocate --out")
    return canonical(data["output_root"])


def portable_record(token, phase=None, **extra):
    record = {"version": 2, "state": ".", "out": ".", "token": token, **extra}
    if phase is not None:
        record["phase"] = phase
    return record


def expand_record(data, state_dir):
    if data is not None and data.get("version") == 2:
        if data.get("state") != "." or data.get("out") != ".":
            raise ValueError("invalid relative writer reference")
        return {**data, "state": canonical(state_dir), "out": local_output(state_dir)}
    return data


def owner_record(state_dir, out_dir):
    raw = read_json(Path(out_dir) / OWNER_FILE)
    check_owner_record(raw)
    return expand_record(raw, state_dir)


def write_registration(path, text, dry_run=False):
    lease = _CURRENT.get()
    path = Path(path)
    allowed = ({lease.state_dir / name for name in (ACTIVATION_FILE, PATHS_FILE, RELOCATION_FILE)}
               | {lease.root / OWNER_FILE}) if lease else set()
    if (lease is None or not lease.switching
            or (path not in allowed and path.parent != lease.state_dir / "relocations")):
        raise RuntimeError("registration writes require the explicit ownership lease")
    if not dry_run:
        store.write_atomic(path, json.dumps(text, ensure_ascii=False))


def activation(state_dir):
    data = read_json(Path(state_dir) / ACTIVATION_FILE)
    if data is not None and (not isinstance(data, dict) or type(data.get("version")) is not int
            or data.get("version") not in (1, 2) or data.get("phase") not in ("switching", "relocating", "enabled", "paused")
            or any(not isinstance(data.get(k), str) or not data[k] for k in ("state", "out", "token"))):
        raise ValueError("invalid writer activation; explicit reconciliation is required")
    return expand_record(data, state_dir)


def check_owner_record(owner):
    if owner is not None and (not isinstance(owner, dict) or type(owner.get("version")) is not int
            or owner.get("version") not in (1, 2)
            or any(not isinstance(owner.get(k), str) or not owner[k] for k in ("state", "out", "token"))):
        raise ValueError("invalid output owner record")


def validate_owner(state_dir, out_dir, enabled=True):
    state_key, out_key = canonical(state_dir), canonical(out_dir)
    active = activation(state_dir)
    if not active or (enabled and active.get("phase") != "enabled"):
        raise RuntimeError("sweep is disabled; explicit ownership switch is required before run")
    owner = owner_record(state_dir, out_key)
    if (not owner or owner.get("version") not in (1, 2) or active.get("out") != out_key
            or owner.get("out") != out_key or owner.get("state") != state_key
            or active.get("state") != state_key or not active.get("token")
            or owner.get("token") != active["token"]):
        raise RuntimeError("output owner does not match this state home; explicit switch is required")
    return active


class OutputLease:
    """The lock location depends on physical output path, never PSTATE_HOME."""

    def __init__(self, out_dir, state_dir, switching=False, from_out=None, from_state=None):
        self.root = Path(canonical(out_dir))
        self.state_dir = Path(canonical(state_dir))
        self.switching = switching
        self.from_out = from_out
        self.from_state = from_state
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()
        self.lock = store.RunLock(Path(tempfile.gettempdir()) / "pstate-sweep-output-locks" / digest)
        self.context_token = None

    def __enter__(self):
        if _CURRENT.get() is not None:
            raise RuntimeError("nested output lease is not allowed")
        self.lock.__enter__()
        try:
            if self.from_out is not None:
                from ..relocation import inspect_move
                inspect_move(self.state_dir, self.root, self.from_out, self.from_state)
            elif self.switching:
                owner = owner_record(self.state_dir, self.root)
                active = activation(self.state_dir)
                if active and active.get("phase") == "relocating":
                    raise RuntimeError("relocation is incomplete; retry sweep relocate")
                if owner and (owner.get("state") != str(self.state_dir) or owner.get("out") != str(self.root)
                              or (owner.get("version") == 2 and (not active or owner["token"] != active["token"]))):
                    raise RuntimeError("output belongs to another state home")
            else:
                validate_owner(self.state_dir, self.root)
            self.context_token = _CURRENT.set(self)
            return self
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise

    def __exit__(self, *exc):
        if self.context_token is not None:
            _CURRENT.reset(self.context_token)
        self.lock.__exit__(*exc)


@contextmanager
def preview_activation(pending):
    lease = _CURRENT.get()
    if (lease is None or not lease.switching or pending.get("out") != str(lease.root)
            or pending.get("state") != str(lease.state_dir)):
        raise RuntimeError("preview requires the explicit switch lease")
    token = _PREVIEW.set(pending)
    try:
        yield
    finally:
        _PREVIEW.reset(token)


def check_path(path):
    lease = _CURRENT.get()
    if lease is None:
        raise RuntimeError("page writes require the active output lease")
    resolved = Path(canonical(path))
    try:
        relative = resolved.relative_to(lease.root)
    except ValueError as exc:
        raise ValueError("page path escapes the owned output") from exc
    if not relative.parts or relative.parts[0] == OWNER_FILE:
        raise ValueError("invalid generated page target")
    # Root aliases resolve before locking; children may not redirect a generated write.
    lexical = Path(os.path.abspath(path))
    try:
        child = lexical.relative_to(lease.root)
    except ValueError:
        # A user-selected alias of the root is allowed.
        child = relative
    current = lease.root
    for part in child.parts:
        current = current / part
        try:
            stat = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise ValueError("generated page child is a symlink or junction")
    return resolved


def guard(path, dry_run=False):
    resolved = check_path(path)
    lease = _CURRENT.get()
    preview = _PREVIEW.get()
    if not (dry_run and lease.switching and preview):
        active = validate_owner(lease.state_dir, lease.root, enabled=not lease.switching)
        if lease.switching and active.get("phase") != "switching":
            raise RuntimeError("switching writes require an explicit in-progress switch")
    return resolved
