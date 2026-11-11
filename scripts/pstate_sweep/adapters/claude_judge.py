"""判断器的 Claude Code 实现：无头 claude，隔离用户的钩子/插件/skill，不留会话记录。"""
import json
import os
import subprocess

from ..ports import Judge, JudgeError

from ..judgment import SCHEMA, OWNER_SCHEMA, RESUME_PROMPT, RECAP_PROMPT, OWNER_PROMPT, _fill


def build_command(cfg, request):
    """拼出一次调用的 argv 和 stdin 文本（纯函数，便于测试核对隔离参数）。"""
    mode = request["mode"]
    model = cfg["resume_model"] if mode == "resume" else cfg["recap_model"]
    schema = OWNER_SCHEMA if mode == "owner" else SCHEMA
    argv = [cfg["claude_cmd"], "-p", "--safe-mode", "--tools", "", "--no-session-persistence",
            "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False),
            "--model", model, "--max-budget-usd", str(cfg["budget_usd_per_call"])]
    if mode == "resume":
        argv += ["--resume", request["sid"], "--fork-session"]
        prompt = _fill(RESUME_PROMPT, cfg, request)
    elif mode == "owner":
        prompt = _fill(OWNER_PROMPT, cfg, request)
    else:
        prompt = _fill(RECAP_PROMPT, cfg, request)
    return argv, prompt


def parse_output(stdout):
    """claude --output-format json 的结果 → 结构化输出字典。"""
    try:
        data = json.loads(stdout)
    except ValueError as e:
        raise JudgeError(f"输出不是 JSON：{stdout[:200]!r}") from e
    if isinstance(data, list):  # 部分版本输出事件数组，取最后的 result 事件
        data = next((x for x in reversed(data) if isinstance(x, dict) and x.get("type") == "result"), {})
    if data.get("is_error"):
        raise JudgeError(f"claude 报错：{str(data.get('result') or data.get('subtype'))[:200]}")
    out = data.get("structured_output")
    if isinstance(out, dict):
        return out
    result = data.get("result")
    if isinstance(result, str):
        try:
            return json.loads(result)
        except ValueError:
            pass
    raise JudgeError(f"没有结构化输出：{str(result)[:200]!r}")


class ClaudeJudge(Judge):
    def __init__(self, cfg, scratch_dir, log=None):
        self.cfg = cfg
        self.scratch_dir = scratch_dir   # recap 分类不需要项目目录，在这个空目录里跑
        self.log = log or (lambda argv: None)

    def judge(self, request):
        argv, prompt = build_command(self.cfg, request)
        if request["mode"] == "resume":
            cwd = request.get("cwd")
            if not cwd or not os.path.isdir(cwd):
                raise JudgeError(f"原工作目录已不存在，无法让会话自答：{cwd}", retry=False)
        else:   # recap 分类和归属补判都不需要项目目录
            os.makedirs(self.scratch_dir, exist_ok=True)
            cwd = self.scratch_dir
        self.log(argv)
        try:
            proc = subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", cwd=cwd, timeout=self.cfg["timeout_s"],
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as e:
            raise JudgeError(f"超时（{self.cfg['timeout_s']} 秒）") from e
        except OSError as e:
            raise JudgeError(f"启动 claude 失败：{e}") from e
        if proc.returncode != 0 and not proc.stdout.strip():
            raise JudgeError(f"claude 退出码 {proc.returncode}：{proc.stderr.strip()[:200]}")
        return parse_output(proc.stdout)