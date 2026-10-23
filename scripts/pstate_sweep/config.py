"""读取并校验 config.toml；必需项缺失或类型不对直接报错，不悄悄回退。"""
import os
import math
import tomllib
from datetime import datetime
from pathlib import Path

REQUIRED = {
    "paths": {"vault_dir": str, "page_name": str, "closed_name": str, "archive_dir": str,
              "installed_plugins": str, "doc_gate": str},
    "scan": {"idle_days": (int, float), "entrypoints": list, "since": str, "window_days": (int, float)},
    "owner": {"personal_desc": str, "work_desc": str, "personal_paths": list},
    "judge": {"claude_cmd": str, "recap_model": str, "resume_model": str, "timeout_s": (int, float),
              "budget_usd_per_call": (int, float), "max_calls": int, "max_failures": int},
    "schedule": {"task_name": str, "interval_hours": (int, float)},
}
PATH_KEYS = {"vault_dir", "projects_dir", "state_dir", "installed_plugins", "doc_gate"}


class ConfigError(Exception):
    pass


def _expand(value):
    return os.path.expanduser(os.path.expandvars(value)) if value else value


def load(path):
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"配置文件读不了：{path}：{e}") from e
    cfg = {}
    for section, keys in REQUIRED.items():
        sec = raw.get(section)
        if not isinstance(sec, dict):
            raise ConfigError(f"配置缺少 [{section}] 段")
        for key, typ in keys.items():
            if key not in sec:
                raise ConfigError(f"配置缺少 {section}.{key}")
            val = sec[key]
            if not isinstance(val, typ) or isinstance(val, bool):
                raise ConfigError(f"配置 {section}.{key} 类型不对：{val!r}")
            cfg[key] = _expand(val) if key in PATH_KEYS else val
    for key in ("claude_cmd", "recap_model", "resume_model"):
        if not cfg[key].strip():
            raise ConfigError(key + " must be nonempty text")
    for key in ("timeout_s", "budget_usd_per_call", "interval_hours", "idle_days", "window_days"):
        if isinstance(cfg[key], float) and not math.isfinite(cfg[key]):
            raise ConfigError(key + " must be finite")
    if cfg["max_calls"] < 0 or cfg["idle_days"] < 0:
        raise ConfigError("max_calls / idle_days 不能为负")
    if cfg["max_failures"] < 1:
        raise ConfigError("max_failures 至少为 1")
    if cfg["window_days"] <= 0:
        raise ConfigError("window_days 必须大于 0")
    if not all(isinstance(p, str) and p for p in cfg["personal_paths"]):
        raise ConfigError("owner.personal_paths 必须是非空字符串列表")
    cfg["personal_paths"] = [_expand(p) for p in cfg["personal_paths"]]
    history = raw.get("history", {}).get("roots")
    if not isinstance(history, dict) or any(not isinstance(k, str) or not isinstance(v, list)
            or any(not isinstance(p, str) or not p for p in v) for k, v in history.items()):
        raise ConfigError("history.roots must map platform names to path lists")
    cfg["history_roots"] = {k: [_expand(p) for p in v] for k, v in history.items()}
    if not all(isinstance(p, str) and p for p in cfg["entrypoints"]):
        raise ConfigError("scan.entrypoints must be a nonempty-string list")
    for key in ("timeout_s", "budget_usd_per_call", "interval_hours"):
        if cfg[key] <= 0:
            raise ConfigError(key + " must be positive")
    for key in ("page_name", "closed_name", "archive_dir"):
        val = cfg[key]
        if not val or Path(val).is_absolute() or len(Path(val).parts) != 1 or val in (".", ".."):
            raise ConfigError(key + " must be one relative filename")
    if cfg["page_name"] == cfg["closed_name"]:
        raise ConfigError("page_name and closed_name must differ")
    cfg["since_ts"] = parse_since(cfg["since"])
    return cfg


def parse_since(text):
    """scan.since：空串 = 不设下限；否则是本地日期 YYYY-MM-DD，最后对话早于这天的会话不看。"""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").timestamp()
    except ValueError as e:
        raise ConfigError(f"配置 scan.since 不是 YYYY-MM-DD 日期：{text!r}") from e