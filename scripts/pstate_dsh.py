# 用途: DSH（DeepSeek Harness）原生钩子薄壳——把 DSH 事件载荷翻译成共享判定使用的 data，
#       再交给 pstate_hooks.begin/finish/end；本文件不做锁判定，只做输入与输出翻译。
# 用法: 由 hooks/dsh.json 调用：py -3 scripts/pstate_dsh.py start|prompt|pre|post|fail|turn|end（读 stdin JSON）
# 事件: agent/created→start、agent/inbox/inserted→prompt、tools/pre-execute→pre、
#       tools/post-execute→post（失败由 tool_response 判定，也可显式 fail）、
#       agent/turn-stopping→turn（Stop 不是 SessionEnd，不做事）、agent/disposed→end。
# 退出码: 0 放行；2 阻断（写前拒绝或写前检查失败）；1 其它非阻断错误。stdout 用 Claude 兼容决策 JSON。
import json
import os
import sys

import pstate
import pstate_hooks as H
import pstate_tools as T

HOST = "dsh"
EVENT_NAMES = {"agent/created": "SessionStart", "agent/inbox/inserted": "UserPromptSubmit",
               "tools/pre-execute": "PreToolUse", "tools/post-execute": "PostToolUse",
               "agent/turn-stopping": "Stop", "agent/disposed": "SessionEnd"}
COMMANDS = ("start", "prompt", "pre", "post", "fail", "turn", "end")
USAGE = "usage: pstate_dsh.py " + "|".join(COMMANDS) + "  (reads one DSH hook payload from stdin)\n"


def text(value):
    return value if isinstance(value, str) else ""


def read_payload():
    raw = sys.stdin.buffer.read()
    value = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("DSH hook payload must be a JSON object")
    return value


def normalize(payload):
    """DSH payload → shared hook data; tool names are mapped by this host adapter."""
    dsh = payload.get("dsh") if isinstance(payload.get("dsh"), dict) else {}
    native = text(payload.get("hook_event_name"))
    cwd = text(payload.get("cwd")) or os.getcwd()
    inputs = payload.get("tool_input")
    inputs = dict(inputs) if isinstance(inputs, dict) else {}
    for key in ("workdir", "cwd"):
        if text(inputs.get(key)) and not os.path.isabs(str(inputs[key])):
            inputs[key] = os.path.join(cwd, str(inputs[key]))
    turn = dsh.get("turn")
    return {"session_id": text(payload.get("session_id")),
            "tool_use_id": text(payload.get("tool_use_id")),
            "tool_name": T.normalize_tool(payload.get("tool_name"), HOST),
            "tool_input": inputs, "cwd": cwd,
            "transcript_path": text(dsh.get("session_log")),
            "tool_response": payload.get("tool_response"),
            "prompt": text(payload.get("prompt")), "source": text(payload.get("source")),
            "turn_id": None if turn is None else str(turn),
            "hook_event_name": EVENT_NAMES.get(native) or native,
            "native_event_name": native,
            "plugin_root": text(payload.get("plugin_root")),
            "stop_hook_active": payload.get("stop_hook_active") is True,
            "host": HOST}


def start(data):
    return pstate.hook_start(data, HOST)


def prompt(data):
    return pstate.hook_prompt(data)


def pre(data):
    """Pre-write decision. Identity still comes from the process chain, never from the env alone."""
    allowed = H.begin(data, pstate.state_home(), pstate.state_dir_for, pstate.self_cmd(), host=HOST)
    return 0 if allowed else 2


def settle(data, failed=False):
    H.finish(data, pstate.state_home(), failed, HOST)
    return 0


def turn(data):
    """agent/turn-stopping: no post-hoc scan and no release; Stop is not SessionEnd."""
    return 0


def end(data):
    H.end(data, pstate.state_home(), HOST)
    return 0


DISPATCH = {"start": start, "prompt": prompt, "pre": pre, "post": settle,
            "fail": lambda data: settle(data, True), "turn": turn, "end": end}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ""
    if command in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0
    try:
        if command not in DISPATCH:
            raise ValueError(f"unknown DSH hook command: {command!r}")
        return DISPATCH[command](normalize(read_payload()))
    except Exception as exc:
        event = "hook-pre" if command == "pre" else "dsh:" + (command or "?")
        message = H.diagnostic(pstate.state_home(), event, exc)
        sys.stderr.write(message + "\n")
        if command == "pre":
            H.emit_deny(message + "；写入未放行，先修复诊断，不要绕到其他工具。")
            return 2
        return 1


if __name__ == "__main__":
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
