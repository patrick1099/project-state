"""判断器的 Claude Code 实现：无头 claude，隔离用户的钩子/插件/skill，不留会话记录。"""
import json
import os
import subprocess

from ..ports import Judge, JudgeError

OWNER_ENUM = ["个人", "公司", "说不清"]

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["悬置", "完成", "放弃", "不确定"]},
        "title": {"type": "string"},
        "stopped_at": {"type": "string"},
        "owner": {"type": "string", "enum": OWNER_ENUM},
    },
    "required": ["status", "title", "stopped_at", "owner"],
    "additionalProperties": False,
}

OWNER_SCHEMA = {
    "type": "object",
    "properties": {"owner": {"type": "string", "enum": OWNER_ENUM}},
    "required": ["owner"],
    "additionalProperties": False,
}

RULES = """判断这件事现在处于哪种状态，只按给定的 JSON schema 输出：
- 悬置：用户要求过、还没做完的事；或者 AI 停在等用户决定、点头、提供信息才能继续。
- 完成：用户要的东西已经交付，没有在等用户拍板的事。AI 随口提的「以后可以做」不算悬置。
- 放弃：用户明确说不做了、换方向了。
- 不确定：材料不够判断。
title：15 个字以内的中文短标题，说清是什么事。
stopped_at：一两句中文，说目标、停在哪、下一步是什么。"""

OWNER_RULES = """owner：这件悬着的事本身属于哪一类（看事情的内容，不看会话开在哪个目录——用户常在公司仓目录里聊个人工具）：
- 个人：{personal_desc}
- 公司：{work_desc}
- 说不清：两边都沾、或材料不够判断。
线索（仅供参考）：
- 会话目录：{cwd}
- 会话里写过的文件（节选）：{written}
- 其中命中「个人路径」的：{personal_hits}"""

RESUME_PROMPT = """[后台检查，不是用户在继续对话] 不要执行任何操作，也不要接着干活。
请根据上面这整段对话回答。
""" + RULES + "\n" + OWNER_RULES

RECAP_PROMPT = """下面是一个编程会话的材料：会话自动生成的离开摘要，以及最后一轮的用户消息和 AI 回复。
""" + RULES + "\n" + OWNER_RULES + """

【离开摘要】
{recap}

【最后一条用户消息】
{last_human}

【最后一条 AI 回复】
{last_assistant}
"""

OWNER_PROMPT = """下面是一件"没做完的事"的已有结论。只判断它的归属，按给定的 JSON schema 输出。
""" + OWNER_RULES + """

【标题】
{title}

【停在哪】
{stopped_at}
"""


def _fill(template, cfg, request):
    hints = request.get("hints") or {}
    fields = dict(request)
    fields.update(personal_desc=cfg["personal_desc"], work_desc=cfg["work_desc"],
                  cwd=hints.get("cwd") or request.get("cwd") or "（未知）",
                  written="；".join(hints.get("written") or []) or "（无）",
                  personal_hits="；".join(hints.get("personal_hits") or []) or "（无）")
    fields.setdefault("title", "")
    fields.setdefault("stopped_at", "")
    return template.format(**fields)


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