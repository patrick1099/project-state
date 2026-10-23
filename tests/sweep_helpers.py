"""测试用的小工具：构造 transcript 行、假判断器、假文档闸、配置。"""
import json
from pathlib import Path

DAY = 86400
NOW = 1_800_000_000.0  # 固定"现在"，测试不依赖真实时间


def iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def human(text, ts, cwd="C:/work/proj", entry="cli", branch="main"):
    return {"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": text},
            "timestamp": iso(ts), "cwd": cwd, "entrypoint": entry, "gitBranch": branch}


def assistant(text, ts, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append({"type": "tool_use", "id": tool[0], "name": "Write", "input": {"file_path": tool[1]}})
    return {"type": "assistant", "message": {"role": "assistant", "content": content}, "timestamp": iso(ts)}


def tool_result(tool_id, ts, error=False):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": "ok"}]},
        "timestamp": iso(ts)}


def recap(text, ts):
    return {"type": "system", "subtype": "away_summary", "content": text + " (disable recaps in /config)",
            "timestamp": iso(ts)}


def write_transcript(projects_dir, sid, rows, project="C--work-proj"):
    d = Path(projects_dir) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def sid(n):
    return f"{n:08x}-0000-4000-8000-000000000000"


class FakeJudge:
    """answers：完整判断的返回（没写 owner 的补成"个人"，免得每个测试都多出一轮补判）；
    owners：补判归属（mode=owner）的返回；fail：按 sid 抛的错。"""

    def __init__(self, answers=None, fail=None, owners=None):
        self.answers = answers or {}
        self.fail = fail or {}
        self.owners = owners or {}
        self.requests = []

    def judge(self, request):
        self.requests.append(request)
        if request["sid"] in self.fail:
            raise self.fail[request["sid"]]
        if request["mode"] == "owner":
            return {"owner": self.owners.get(request["sid"], "个人")}
        out = dict(self.answers.get(request["sid"], {"status": "悬置", "title": "默认标题", "stopped_at": "停在某处"}))
        if "status" in out and out["status"] in ("悬置", "完成", "放弃", "不确定"):
            out.setdefault("owner", "个人")
        return out


class FakeGate:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.seen = []

    def changed_since(self, cwd, since):
        return []

    def evaluate(self, paths, stop_at=None):
        self.seen.append(list(paths))
        return self.issues


def make_cfg(tmp):
    tmp = Path(tmp)
    return {
        "vault_dir": str(tmp / "vault"), "page_name": "悬着的事.md", "closed_name": "悬着的事-已处理.md",
        "archive_dir": "归档", "projects_dir": str(tmp / "projects"), "state_dir": str(tmp / "state"),
        "history_roots": {"claude": [str(tmp / "projects")]},
        "installed_plugins": str(tmp / "none.json"), "doc_gate": "",
        "idle_days": 2, "entrypoints": ["cli", "claude-vscode"], "since": "", "since_ts": None, "window_days": 7,
        "personal_desc": "个人工具", "work_desc": "公司固件", "personal_paths": ["my-repos", "treasury-vault"],
        "claude_cmd": "claude", "recap_model": "haiku", "resume_model": "sonnet", "timeout_s": 5,
        "budget_usd_per_call": 1.0, "max_calls": 20, "max_failures": 3, "task_name": "session-sweep", "interval_hours": 4,
    }

def summarize_rows(rows):
    # Test fixtures use the shared native parser; production has no second row parser.
    from pstate_history.claude import summarize
    return summarize([("fixture.jsonl", i + 1, row) for i, row in enumerate(rows)])[0]
