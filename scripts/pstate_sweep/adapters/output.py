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


def activation(state_dir):
    data = read_json(Path(state_dir) / ACTIVATION_FILE)
    if data is not None and (not isinstance(data, dict) or type(data.get("version")) is not int
            or data.get("version") != 1 or data.get("phase") not in ("switching", "enabled", "paused")
            or any(not isinstance(data.get(k), str) or not data[k] for k in ("state", "out", "token"))):
        raise ValueError("invalid writer activation; explicit reconciliation is required")
    return data


def check_owner_record(owner):
    if owner is not None and (not isinstance(owner, dict) or type(owner.get("version")) is not int
            or owner.get("version") != 1
            or any(not isinstance(owner.get(k), str) or not owner[k] for k in ("state", "out", "token"))):
        raise ValueError("invalid output owner record")


def validate_owner(state_dir, out_dir, enabled=True):
    state_key, out_key = canonical(state_dir), canonical(out_dir)
    active = activation(state_dir)
    owner = read_json(Path(out_key) / OWNER_FILE)
    check_owner_record(owner)
    if not active or (enabled and active.get("phase") != "enabled"):
        raise RuntimeError("sweep is disabled; explicit ownership switch is required before run")
    if (not owner or owner.get("version") != 1 or active.get("out") != out_key
            or owner.get("out") != out_key or owner.get("state") != state_key
            or active.get("state") != state_key or not active.get("token")
            or owner.get("token") != active["token"]):
        raise RuntimeError("output owner does not match this state home; explicit switch is required")
    return active


class OutputLease:
    """The lock location depends on physical output path, never PSTATE_HOME."""

    def __init__(self, out_dir, state_dir, switching=False):
        self.root = Path(canonical(out_dir))
        self.state_dir = Path(canonical(state_dir))
        self.switching = switching
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()
        self.lock = store.RunLock(Path(tempfile.gettempdir()) / "pstate-sweep-output-locks" / digest)
        self.context_token = None

    def __enter__(self):
        if _CURRENT.get() is not None:
            raise RuntimeError("nested output lease is not allowed")
        self.lock.__enter__()
        try:
            owner = read_json(self.root / OWNER_FILE)
            check_owner_record(owner)
            if self.switching:
                if owner and (owner.get("version") != 1 or owner.get("state") != str(self.state_dir)
                              or owner.get("out") != str(self.root)):
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
