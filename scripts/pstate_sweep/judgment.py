"""Shared judgment schemas and pure prompt rendering."""
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

