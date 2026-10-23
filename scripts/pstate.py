# 结构: vibe-scripts/standard（锁的 Core/Adapter 拆在同目录 pstate_lock.py）
# 用途: 项目看板——同一仓库的所有 worktree、所有 AI 会话共用：会话互斥锁（谁在干哪件事、占着哪些文件）＋ 待办条目
# 用法: py -3 pstate.py status | new <slug> --title <标题> | claim <slug> | release | take <会话号|slug> | lock on|off|auto
#       | done <slug> | path | hook / hook-pre / hook-post / hook-fail / hook-end（Claude Code 钩子，读 stdin）
# 原始需求: "缺少项目记忆，或者说todo。有时候很容易冲突，和handoff"。几个会话同时干同一个个人项目时互斥：
#           同时改同一批文件、同一件事做两遍，撞上就拦下由 AI 问用户；外加待办看板。进度正文写项目的 HANDOFF，不写这里。
#           只管个人项目（公司仓不加锁、不新建条目）；状态放仓库外本机目录（按 git common dir 识别同一仓库）。

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pstate_lock as L

# ===== 1 配置/常量 =====
STATE_HOME_ENV = "PSTATE_HOME"
STATE_HOME_DEFAULT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state") / "ai-project-state"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
MAX_INJECT = 4000
STALE_DAYS = 7
FIELD_WORKTREE = "worktree"
FIELD_BRANCH = "分支"
FIELD_STATUS = "状态"
FIELD_HANDOFF = "交接"
STATUSES = ("进行中", "阻塞", "待办")
SLUG_RE = re.compile(r"^[\w.\-]+$")
HOOK_COMMANDS = ("hook", "hook-pre", "hook-post", "hook-fail", "hook-end")

TASK_TEMPLATE = """# {title}

- {f_wt}: {worktree}
- {f_br}: {branch}
- {f_st}: {status}
- {f_ho}: {handoff}

## 目标
"""


# ===== 3 Core：纯逻辑 =====
def norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def repo_key(common_dir):
    """同一仓库（不管从哪个 worktree 进来）得到同一个目录名：主仓目录名 + common dir 路径哈希。"""
    p = Path(common_dir)
    root_name = p.parent.name if p.name == ".git" else p.name
    digest = hashlib.sha1(norm(p).encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", root_name).strip("._") or "repo"
    return f"{safe}-{digest}"


def parse_worktrees(porcelain):
    """解析 `git worktree list --porcelain`，返回 [{path, head, branch, prunable}]。"""
    out, cur = [], None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[9:], "head": "", "branch": "", "prunable": False}
            out.append(cur)
        elif cur is None:
            continue
        elif line.startswith("HEAD "):
            cur["head"] = line[5:12]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "detached":
            cur["branch"] = "(detached)"
        elif line.startswith("prunable"):
            cur["prunable"] = True
    return out


def count_changes(porcelain):
    """`git status --porcelain` → (已跟踪文件改动数, 未跟踪数)。"""
    tracked = untracked = 0
    for line in porcelain.splitlines():
        if line.startswith("??"):
            untracked += 1
        elif line.strip():
            tracked += 1
    return tracked, untracked


def parse_task(text):
    """任务文件 → {title, fields, sections}。字段是标题下 `- 键: 值` 行，节是 `## ` 标题。"""
    title, fields, sections, cur = "", {}, {}, None
    for line in text.splitlines():
        if cur is None and not title and line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur is None:
            m = re.match(r"^[-*]\s*([^:：]+)[:：]\s*(.*)$", line)
            if m:
                fields[m.group(1).strip()] = m.group(2).strip()
        else:
            sections[cur].append(line)
    return {"title": title, "fields": fields,
            "sections": {k: "\n".join(v).strip() for k, v in sections.items()}}


def next_step(task):
    for name, body in task["sections"].items():
        if name.startswith("停在哪") or "下一步" in name:
            first = next((l.strip(" -*") for l in body.splitlines() if l.strip()), "")
            return first
    return ""


def set_field(text, field, value):
    pat = re.compile(rf"^([-*]\s*{re.escape(field)}\s*[:：]\s*).*$", re.M)
    return pat.sub(lambda m: m.group(1) + value, text, count=1)


def age_text(seconds):
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def task_line(slug, task, mtime, now, known_wts, current_wt, holder=""):
    f = task["fields"]
    wt = f.get(FIELD_WORKTREE, "")
    status = f.get(FIELD_STATUS, "?")
    where = Path(wt).name if wt else "?"
    if wt and norm(wt) == norm(current_wt):
        where += "（当前）"
    notes = [f"{age_text(now - mtime)}更新"]
    if holder:
        notes.insert(0, f"由会话 {holder} 认领")
    if now - mtime > STALE_DAYS * 86400:
        notes.append("久未更新，内容可能过期")
    if wt and norm(wt) not in known_wts:
        notes.append("工作区已不在 git 登记里，基线待核实")
    line = f"  - {slug}：{task['title'] or '(无标题)'}  [{status}] @{where}  {'，'.join(notes)}"
    nxt = next_step(task)
    return line + (f"\n      下一步：{nxt}" if nxt else "")


def render_status(repo_name, state_dir, worktrees, tasks, current_wt, now, session_lines=(), holders=None):
    """worktrees: [{path, branch, head, prunable, changes:(t,u)|None}]；tasks: [(slug, task, mtime)]。"""
    holders = holders or {}
    lines = [f"[pstate] {repo_name} 的工作现场（任务目录 {state_dir}）", "工作区："]
    for w in worktrees:
        mark = "*" if norm(w["path"]) == norm(current_wt) else " "
        if w["prunable"] or w["changes"] is None:
            state = "目录不存在或读不了"
        else:
            t, u = w["changes"]
            state = "干净" if not (t or u) else f"改动 {t}，未跟踪 {u}"
        lines.append(f"  {mark} {w['path']}  [{w['branch']}] {w['head']}  {state}")
    if session_lines:
        lines.append(f"活会话（{len(session_lines)}）：")
        lines += list(session_lines)
    known = {norm(w["path"]) for w in worktrees if not w["prunable"]}
    if tasks:
        lines.append(f"看板条目（{len(tasks)}）：")
        lines += [task_line(s, t, m, now, known, current_wt, holders.get(s, "")) for s, t, m in tasks]
    else:
        lines.append("看板条目：无")
    return "\n".join(lines)


def render_hook(status_text, current_tasks, cmd, personal):
    """current_tasks: [(path, text)]，当前 worktree 的条目全文。"""
    lines = [status_text, ""]
    total = sum(len(t) for _, t in current_tasks)
    if current_tasks and total <= MAX_INJECT:
        lines.append("当前工作区的条目全文：")
        for path, text in current_tasks:
            lines += ["", f"--- {path} ---", text.rstrip()]
        lines.append("")
    elif current_tasks:
        lines.append(f"当前工作区的条目合计 {total} 字，没有注入全文，接手前完整读完：")
        lines += [f"- {p}" for p, _ in current_tasks]
        lines.append("")
    if personal:
        lines += [
            "这是本仓库的 pstate 看板：会话互斥（哪个会话在干哪件事、占着哪些文件）＋ 待办条目。用法：",
            f"- 开工前对一下上面的活会话和条目。要做的事在看板上，先 {cmd} claim <短名>；被拒说明别的活会话正在干，停下问用户等对方还是接手。",
            f"- 看板上没有、又会跨会话的事，{cmd} new <英文短名> --title <标题> 建一条再 claim；一问一答当场做完的不建。",
            "- 改文件时钩子自动占用；撞上别的活会话占着的文件会被拦下，照拦截提示问用户，别换工具绕过。",
            "- 进度和交接写项目的 HANDOFF.md，不写进看板条目，也不写进记忆；条目只放标题、状态、一句目标。",
            f"- 做完：{cmd} done <短名>。用 squash / cherry-pick 合并、或放弃改动的，收尾跑 {cmd} release 放掉占用。",
        ]
    else:
        lines += [
            "这是本仓库遗留的 pstate 任务（公司仓：用户定了不加锁、不新建任务，这里只保留已有任务直到做完）。",
            f"- 用户要做的事对应上面某个任务，就接着干，先读它全文；有实质进展时更新该任务 .md；做完 {cmd} done <短名>。",
            "- 进度不写进记忆；记忆只放长期有效的结论和教训。",
        ]
    return "\n".join(lines)


def new_task_text(title, worktree, branch, status, handoff=""):
    return TASK_TEMPLATE.format(title=title, worktree=worktree, branch=branch, status=status, handoff=handoff,
                                f_wt=FIELD_WORKTREE, f_br=FIELD_BRANCH, f_st=FIELD_STATUS, f_ho=FIELD_HANDOFF)


# ===== 4 Adapter：git 与文件 =====
def git(args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} 失败")
    return r.stdout


def repo_context(cwd):
    """返回 (common_dir, 当前 worktree 根, 当前分支)；不在 git 仓库里抛 RuntimeError。"""
    common = git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd).strip()
    top = git(["rev-parse", "--show-toplevel"], cwd).strip()
    branch = git(["branch", "--show-current"], cwd).strip() or "(detached)"
    return os.path.realpath(common), os.path.realpath(top), branch


def collect_worktrees(cwd):
    wts = parse_worktrees(git(["worktree", "list", "--porcelain"], cwd))
    for w in wts:
        w["changes"] = None
        if not w["prunable"] and Path(w["path"]).is_dir():
            try:
                w["changes"] = count_changes(git(["status", "--porcelain"], w["path"]))
            except RuntimeError:
                pass
    return wts


def state_home():
    return Path(os.environ.get(STATE_HOME_ENV) or STATE_HOME_DEFAULT)


def state_dir_for(common_dir):
    return state_home() / repo_key(common_dir)


def ensure_state_dir(common_dir):
    d = state_dir_for(common_dir)
    (d / "tasks").mkdir(parents=True, exist_ok=True)
    info = d / "repo.txt"
    if not info.exists():
        info.write_text(f"git common dir: {common_dir}\n", encoding="utf-8")
    return d


def load_tasks(state_dir):
    tasks = []
    for p in sorted((state_dir / "tasks").glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tasks.append((p.stem, p, text, p.stat().st_mtime))
    return tasks


def self_cmd():
    return f'py -3 "{Path(__file__).resolve()}"'


def inflight_marker(tool_id):
    safe = re.sub(r"[^\w.\-]", "_", tool_id)[:120]
    return state_home() / "inflight" / safe


def live_state(sdir, backend):
    """读锁状态并清掉确认已死的会话（持短锁写回）。"""
    if not (Path(sdir) / L.LOCK_FILE).exists():
        return L.empty_state()
    with L.mutex(sdir):
        st = L.load_state(sdir)
        if backend and L.prune(st, backend.alive):
            L.save_state(sdir, st)
    return st


def personal_repo(top, sdir):
    return L.is_personal(L.load_state(sdir)["enabled"], L.has_blueprint(top, Path(top) / "_"))


def me_from_env(backend):
    me, chain = L.identify(backend, os.environ.get(SESSION_ENV, ""))
    if not me:
        raise RuntimeError(f"认不出当前会话（需要在 Claude Code 会话里运行，且有 {SESSION_ENV}）")
    return me, chain


# ===== 5 App：命令表 + CLI 入口 =====
def build_status(cwd, me_sid=""):
    common, top, _ = repo_context(cwd)
    sdir = state_dir_for(common)
    raw = load_tasks(sdir) if (sdir / "tasks").is_dir() else []
    parsed = [(s, parse_task(t), m) for s, _, t, m in raw]
    st = live_state(sdir, L.process_backend())
    now = time.time()
    holders = {s.get("task"): L.short(sid) for sid, s in st["sessions"].items() if s.get("task")}
    text = render_status(Path(common).parent.name if Path(common).name == ".git" else Path(common).name,
                         sdir, collect_worktrees(cwd), parsed, top, now,
                         L.session_lines(st, me_sid, now, L.log_age), holders)
    return text, raw, parsed, top, sdir, st


def cmd_status(args):
    text, *_ = build_status(args.cwd, os.environ.get(SESSION_ENV, ""))
    print(text)
    return 0


def cmd_new(args):
    if not SLUG_RE.match(args.slug):
        print(f"短名只能用字母、数字、下划线、点、横线：{args.slug}", file=sys.stderr)
        return 2
    common, top, branch = repo_context(args.cwd)
    if not args.force and not personal_repo(top, state_dir_for(common)):
        print("这不是个人项目（没有 docs/BLUEPRINT.md，也没用 lock on 打开）：用户定了公司仓不新建条目。"
              "确实要建，加 --force。", file=sys.stderr)
        return 1
    p = ensure_state_dir(common) / "tasks" / f"{args.slug}.md"
    if p.exists():
        print(f"条目已存在：{p}", file=sys.stderr)
        return 1
    handoff = Path(top) / "docs" / "HANDOFF.md"
    p.write_text(new_task_text(args.title, top, branch, args.status, f"{handoff}（进度正文写那里）"),
                 encoding="utf-8")
    print(p)
    return 0


def cmd_claim(args):
    common, _, _ = repo_context(args.cwd)
    sdir = state_dir_for(common)
    task_file = sdir / "tasks" / f"{args.slug}.md"
    if not task_file.exists():
        print(f"看板上没有这一条：{args.slug}。先 new 再 claim。", file=sys.stderr)
        return 1
    backend = L.process_backend()
    me, _ = me_from_env(backend)
    with L.mutex(sdir):
        st = L.load_state(sdir)
        L.prune(st, backend.alive)
        ok, holder = L.claim_task(st, args.slug, me, time.time())
        L.save_state(sdir, st)
    if not ok:
        print(f"「{args.slug}」已被活会话 {L.short(holder)} 认领。停下，告诉用户这件事别的会话正在干，"
              f"问他等对方还是接手；用户说接手再跑：take {L.short(holder)}", file=sys.stderr)
        return 1
    text = task_file.read_text(encoding="utf-8", errors="replace")
    task_file.write_text(set_field(text, FIELD_STATUS, "进行中"), encoding="utf-8")
    print(f"已认领 {args.slug}（会话 {L.short(me['sid'])}）")
    return 0


def cmd_release(args):
    common, _, _ = repo_context(args.cwd)
    sdir = state_dir_for(common)
    me, _ = me_from_env(L.process_backend())
    with L.mutex(sdir):
        st = L.load_state(sdir)
        n = L.release_mine(st, me["sid"])
        L.save_state(sdir, st)
    print(f"已放掉任务认领和 {n} 个文件占用")
    return 0


def cmd_take(args):
    common, _, _ = repo_context(args.cwd)
    sdir = state_dir_for(common)
    backend = L.process_backend()
    me, _ = me_from_env(backend)
    with L.mutex(sdir):
        st = L.load_state(sdir)
        L.prune(st, backend.alive)
        target = L.resolve_target(st, args.target, me["sid"])
        if target is None:
            print(f"找不到唯一的活会话或认领：{args.target}（会话可能已经结束，那就不用接手，直接重试）", file=sys.stderr)
            return 1
        info = L.take_over(st, target, me, time.time())
        L.save_state(sdir, st)
    print(f"已接手会话 {L.short(target)}：{info['moved']} 个文件占用，任务 {info['task'] or '无'}")
    if info["inflight"]:
        print(f"注意：对方还有 {info['inflight']} 个写入在执行中或等待批准，已经放行的写入撤不回，先看一眼对方窗口。")
    return 0


def cmd_lock(args):
    common, _, _ = repo_context(args.cwd)
    sdir = ensure_state_dir(common)
    with L.mutex(sdir):
        st = L.load_state(sdir)
        st["enabled"] = {"on": True, "off": False, "auto": None}[args.mode]
        L.save_state(sdir, st)
    print({"on": "本仓库强制开锁", "off": "本仓库关锁", "auto": "本仓库按有没有蓝图自动判断"}[args.mode])
    return 0


def cmd_done(args):
    common, _, _ = repo_context(args.cwd)
    sdir = state_dir_for(common)
    src = sdir / "tasks" / f"{args.slug}.md"
    if not src.exists():
        print(f"没有这个条目：{src}", file=sys.stderr)
        return 1
    dst = sdir / "done" / f"{time.strftime('%Y%m%d')}-{args.slug}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dst)
    if (sdir / L.LOCK_FILE).exists():
        with L.mutex(sdir):
            st = L.load_state(sdir)
            for s in st["sessions"].values():
                if s.get("task") == args.slug:
                    s["task"] = None
            L.save_state(sdir, st)
    print(dst)
    return 0


def cmd_path(args):
    common, _, _ = repo_context(args.cwd)
    print(ensure_state_dir(common))
    return 0


def emit(event, **fields):
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}, ensure_ascii=False))


def read_hook_input():
    return json.loads(sys.stdin.read() or "{}")


def global_thread_hint():
    from pstate_threads.store import hook_hint
    try:
        return hook_hint(state_home(), f'"{sys.executable}" "{Path(__file__).resolve()}"')
    except Exception:
        return ""


def cmd_hook(args):
    data = read_hook_input()
    cwd = data.get("cwd") or os.getcwd()
    hint = global_thread_hint()
    event = data.get("hook_event_name") or "SessionStart"
    if not os.path.isdir(cwd):
        if hint:
            emit(event, additionalContext=hint)
        return 0
    try:
        text, raw, parsed, top, sdir, st = build_status(cwd, data.get("session_id", ""))
    except RuntimeError:
        if hint:
            emit(event, additionalContext=hint)
        return 0
    others = [sid for sid in st["sessions"] if sid != data.get("session_id")]
    if not raw and not others:
        if hint:
            emit(event, additionalContext=hint)
        return 0
    current = [(p, t) for (_, p, t, _), (_, task, _) in zip(raw, parsed)
               if norm(task["fields"].get(FIELD_WORKTREE, "")) == norm(top)]
    ctx = render_hook(text, current, self_cmd(), personal_repo(top, sdir))
    emit(event, additionalContext="\n".join(part for part in (ctx, hint) if part))
    return 0


def cmd_hook_pre(args):
    data = read_hook_input()
    if data.get("tool_name") not in L.WRITE_TOOLS:
        return 0
    ti = data.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if not path:
        return 0
    abs_path = os.path.realpath(os.path.join(data.get("cwd") or os.getcwd(), path))
    loc = L.locate(abs_path)
    if not loc:
        return 0
    top, common = loc
    sdir = state_dir_for(common)
    if not L.is_personal(L.load_state(sdir)["enabled"], L.has_blueprint(top, abs_path)):
        return 0
    backend = L.process_backend()
    me, chain = L.identify(backend, data.get("session_id"), data.get("transcript_path") or "")
    if not me:
        return 0
    rel = L.rel_key(top, abs_path)
    tool_id = data.get("tool_use_id") or f"anon-{os.getpid()}-{time.time()}"
    now = time.time()
    with L.mutex(sdir):
        st = L.load_state(sdir)
        L.prune(st, backend.alive)
        decision, info = L.evaluate_write(st, rel, me, chain, tool_id, top, now, lambda c: L.released(c, rel))
        L.save_state(sdir, st)
    if decision == "deny":
        emit("PreToolUse", permissionDecision="deny",
             permissionDecisionReason=L.deny_text(info, st, now, L.log_age, self_cmd()))
    elif info["kind"] == "claimed":
        marker = inflight_marker(tool_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{sdir}\n{me['sid']}\n", encoding="utf-8")
    return 0


def finish_hook(failed):
    data = read_hook_input()
    tool_id = data.get("tool_use_id")
    if not tool_id:
        return 0
    marker = inflight_marker(tool_id)
    if not marker.exists():
        return 0
    sdir = marker.read_text(encoding="utf-8").splitlines()[0]
    with L.mutex(sdir):
        st = L.load_state(sdir)
        L.finish_tool(st, tool_id, failed)
        L.save_state(sdir, st)
    marker.unlink(missing_ok=True)
    return 0


def cmd_hook_post(args):
    return finish_hook(failed=False)


def cmd_hook_fail(args):
    return finish_hook(failed=True)


def cmd_hook_end(args):
    sid = read_hook_input().get("session_id")
    if not sid:
        return 0
    for lock_file in state_home().glob(f"*/{L.LOCK_FILE}"):
        sdir = lock_file.parent
        if sid not in L.load_state(sdir)["sessions"]:
            continue
        with L.mutex(sdir):
            st = L.load_state(sdir)
            L.drop_session(st, sid)
            L.save_state(sdir, st)
    for marker in (state_home() / "inflight").glob("*"):
        try:
            if marker.read_text(encoding="utf-8").splitlines()[1:2] == [sid]:
                marker.unlink()
        except (OSError, IndexError):
            pass
    return 0


COMMANDS = {"status": cmd_status, "new": cmd_new, "claim": cmd_claim, "release": cmd_release, "take": cmd_take,
            "lock": cmd_lock, "done": cmd_done, "path": cmd_path, "hook": cmd_hook, "hook-pre": cmd_hook_pre,
            "hook-post": cmd_hook_post, "hook-fail": cmd_hook_fail, "hook-end": cmd_hook_end}


def build_parser():
    ap = argparse.ArgumentParser(prog="pstate", description="项目看板：会话互斥锁 + 待办条目",
                                 epilog="LLMs/agents: run 'pstate --ai-help' for usage guidance.")
    ap.add_argument("--cwd", default=os.getcwd(), help="从哪个目录识别仓库（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("thread", help="全局待续事项；thread --ai-help 查看结构化契约")
    sub.add_parser("sweep", help="内部后台与联合回顾页；sweep --ai-help 查看契约")
    sub.add_parser("status", help="工作区现场 + 活会话 + 看板条目")
    p = sub.add_parser("new", help="新建看板条目，打印路径（公司仓默认拒绝）")
    p.add_argument("slug")
    p.add_argument("--title", required=True)
    p.add_argument("--status", choices=STATUSES, default="待办")
    p.add_argument("--force", action="store_true", help="不是个人项目也建")
    p = sub.add_parser("claim", help="本会话认领一条；已被别的活会话认领则退出 1")
    p.add_argument("slug")
    sub.add_parser("release", help="放掉本会话在本仓库的认领和文件占用")
    p = sub.add_parser("take", help="用户同意后接手另一个会话的占用和认领")
    p.add_argument("target", help="会话号前缀或条目短名")
    p = sub.add_parser("lock", help="本仓库的锁开关：on 强制开 / off 关 / auto 按蓝图判断")
    p.add_argument("mode", choices=("on", "off", "auto"))
    p = sub.add_parser("done", help="条目移出活跃视图（移到 done/）")
    p.add_argument("slug")
    sub.add_parser("path", help="打印本仓库的任务目录")
    for name, desc in (("hook", "SessionStart 钩子"), ("hook-pre", "PreToolUse 钩子：写入前裁决"),
                       ("hook-post", "PostToolUse 钩子"), ("hook-fail", "PostToolUseFailure 钩子"),
                       ("hook-end", "SessionEnd 钩子")):
        sub.add_parser(name, help=f"{desc}（读 stdin JSON）")
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    routed = argv[2:] if argv[:1] == ["--cwd"] and len(argv) > 2 else argv
    if routed[:1] == ["sweep"]:
        from pstate_sweep.cli import main as sweep_main
        return sweep_main(routed[1:], state_home())
    if routed[:1] == ["thread"]:
        from pstate_threads.cli import main as thread_main
        return thread_main(routed[1:], state_home())
    before = argv[:argv.index("--")] if "--" in argv else argv
    if "--ai-help" in before:
        from pstate_threads.cli import main as thread_main
        return thread_main(["--ai-help"] + (["--json"] if "--json" in before or "json" in before else []), state_home())
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except RuntimeError as e:
        if args.cmd in HOOK_COMMANDS:
            return 0
        print(f"不在 git 仓库里，或 git 调用失败：{e}", file=sys.stderr)
        return 1


def run_as_hook(argv):
    """钩子入口：任何异常（含参数错误、超时、锁文件忙）都静默放行，退出 0。"""
    real_err = sys.stderr
    try:
        with open(os.devnull, "w", encoding="utf-8") as sys.stderr:
            main(argv)
    except BaseException:
        pass
    finally:
        sys.stderr = real_err
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    if "thread" not in sys.argv[1:] and any(a in HOOK_COMMANDS for a in sys.argv[1:]):
        sys.exit(run_as_hook(sys.argv[1:]))
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f"pstate 内部错误：{e!r}\n")
        sys.exit(1)
