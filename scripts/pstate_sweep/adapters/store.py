"""Permanent source material, reminder closures, run lock and output IO.

sources.json is durable source evidence, not a disposable scan cache.
It never stores matter lifecycle; that remains exclusively in threads.json.
"""
import json
import math
import os
import time
import tempfile
from pathlib import Path

STATE_FILE = "sources.json"
LOCK_FILE = "run.lock"



class SourceStateError(OSError):
    pass


def _number(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def validate_summary(summary):
    if not isinstance(summary, dict) or "version" not in summary:
        raise SourceStateError("invalid source summary; legacy version field is required")
    for key in ("version", "cwd", "branch", "entrypoint"):
        if key in summary and summary[key] is not None and not isinstance(summary[key], str):
            raise SourceStateError("invalid source summary text: " + key)
    for key in ("first_human", "last_human", "last_assistant", "platform", "native_session",
                "identity", "review_version", "activity_version"):
        if key in summary and not isinstance(summary[key], str):
            raise SourceStateError("invalid source summary text: " + key)
    if "human_count" in summary and (type(summary["human_count"]) is not int or summary["human_count"] < 0):
        raise SourceStateError("invalid source summary human_count")
    if "last_ts" in summary and summary["last_ts"] is not None and not _number(summary["last_ts"]):
        raise SourceStateError("invalid source summary last_ts")
    for key in ("interactive", "headless", "recap_fresh"):
        if key in summary and type(summary[key]) is not bool:
            raise SourceStateError("invalid source summary flag: " + key)
    if "written" in summary and (not isinstance(summary["written"], list)
            or any(not isinstance(path, str) for path in summary["written"])):
        raise SourceStateError("invalid source summary written paths")
    recap = summary.get("recap")
    if recap is not None and (not isinstance(recap, dict) or not _number(recap.get("ts"))
            or not isinstance(recap.get("text"), str)):
        raise SourceStateError("invalid source summary recap")


def validate_state(data):
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != 1 or not isinstance(data.get("sessions"), dict)):
        raise SourceStateError("invalid or unsupported source material; left untouched")
    for key in ("closures", "thread_closures", "legacy_marks"):
        if not isinstance(data.setdefault(key, {}), dict):
            raise SourceStateError("invalid source closure/pointer material")
    for identity, entry in data["sessions"].items():
        if not isinstance(identity, str) or not isinstance(entry, dict) or not isinstance(entry.get("summary"), dict):
            raise SourceStateError("invalid source summary material")
        validate_summary(entry["summary"])
    for key in ("closures", "thread_closures"):
        for ident, closure in data[key].items():
            if not isinstance(ident, str) or not isinstance(closure, dict) or not isinstance(closure.get("at"), (int, float)):
                raise SourceStateError("invalid source reminder closure")
    for ident, alias in data["legacy_marks"].items():
        if not isinstance(ident, str) or not isinstance(alias, dict) or any(
                not isinstance(alias.get(key), str) or not alias[key] for key in ("identity", "activity_version")):
            raise SourceStateError("invalid legacy source pointer")
    return data


def load_state(state_dir):
    path = Path(state_dir) / STATE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": 1, "sessions": {}, "closures": {}, "thread_closures": {}, "legacy_marks": {}}
    except UnicodeError as exc:
        raise SourceStateError("cannot decode permanent source material; left untouched") from exc
    try:
        return validate_state(json.loads(text))
    except (ValueError, UnicodeError) as exc:
        raise SourceStateError("cannot decode permanent source material; left untouched: " + str(exc)) from exc


def save_state(state_dir, state, dry_run=False):
    validate_state(state)
    if dry_run:
        return
    write_atomic(Path(state_dir) / STATE_FILE, json.dumps(state, ensure_ascii=False, indent=1))


def write_atomic(path, text):
    """先写同目录临时文件再替换，避免留下半份文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_launch_log(state_dir, status, stdout, stderr):
    write_atomic(Path(state_dir) / "last_launch.log",
                 f"exit {status}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}")


def write_page_if_changed(path, text, dry_run=False):
    """页面唯一的写入口：内容不变就不写，返回是否写了。正式运行与 --out 冒烟共用。"""
    from .output import guard
    path = guard(path, dry_run=dry_run)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    if not dry_run:
        write_atomic(path, text)
    return True


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read_generated(dir_path, is_generated):
    """目录下脚本生成的页面 → {路径: 内容}；不是脚本生成的（is_generated 为假）一律不读进来。"""
    out = {}
    d = Path(dir_path)
    try:
        with os.scandir(d) as entries:
            paths = sorted((d / entry.name for entry in entries if entry.name.endswith(".md") and entry.is_file()), key=str)
    except FileNotFoundError:
        return out
    for p in paths:
        text = read_text(p)
        if is_generated(text):
            out[str(p)] = text
    return out


def remove_generated(path, is_generated, dry_run=False):
    """删掉一个不再需要的生成页；删前再确认一次是脚本生成的，用户的文件绝不删。返回是否删了。"""
    from .output import guard
    path = guard(path, dry_run=dry_run)
    if not is_generated(read_text(path)):
        return False
    if not dry_run:
        path.unlink(missing_ok=True)
    return True


class RunLock:
    """Kernel-held whole-run lock; exit releases it without age-based takeover."""

    def __init__(self, state_dir):
        self.path = Path(state_dir) / LOCK_FILE
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise RuntimeError("another sweep run is active: " + str(self.path)) from exc
        return self

    def __exit__(self, *exc):
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
