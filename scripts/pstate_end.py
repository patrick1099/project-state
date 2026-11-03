"""Small SessionEnd receipt: durable first, no process discovery or repository scan."""
import hashlib
import json
import os
from pathlib import Path
import sys


def receipt_path(home, sid):
    return Path(home) / "ended" / hashlib.sha256(sid.encode("utf-8")).hexdigest()


def is_ended(home, sid):
    return isinstance(sid, str) and receipt_path(home, sid).is_file()


def mark(home, sid):
    if not isinstance(sid, str) or not sid:
        raise ValueError("missing session_id")
    target = receipt_path(home, sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + "." + str(os.getpid()) + ".tmp")
    try:
        temp.write_text(json.dumps({"session_id": sid}), encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def main():
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    sid = data.get("session_id")
    if not isinstance(sid, str) or not sid:
        raise ValueError("missing session_id")
    home = os.environ.get("PSTATE_HOME") or str(Path(os.environ.get("LOCALAPPDATA") or
           Path.home() / ".local" / "state") / "ai-project-state")
    mark(home, "codex:" + sid)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write(f"[pstate] SessionEnd receipt failed: {exc!r}\n")
        sys.exit(1)
