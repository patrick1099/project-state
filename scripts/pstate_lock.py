# 结构: vibe-scripts/standard（pstate 的锁模块，由 pstate.py 导入；本文件只有 1 配置 / 3 Core / 4 Adapter 三区）
# 用途: 会话互斥锁——按文件相对路径的显式占用表，占用者挂在 Claude Code 会话进程上，进程没了占用就失效
# 用法: 不直接运行；pstate.py 的 hook-pre / hook-post / hook-fail / hook-end 钩子与 claim / release / take / lock 子命令调用这里
#       文件占用按仓库存 locks.json；任务认领按 Obsidian 任务目录存全局 task-claims.json，与 git 仓无关
# 原始需求: "缺少项目记忆，或者说todo。有时候很容易冲突，和handoff"——几个会话同时干同一个个人项目时，
#           a 同时改同一批文件（含不同 worktree 里的同一相对路径）、b 同一件事各做一遍；撞上就拦下，由 AI 停下问用户等对方还是接手。

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path

# ===== 1 配置/常量 =====
LOCK_FILE = "locks.json"
CLAIMS_FILE = "task-claims.json"
MUTEX_FILE = "locks.mutex"
MUTEX_WAIT_SECONDS = 5.0
GIT_TIMEOUT_SECONDS = 5
INTEGRATION_BRANCHES = ("main", "master")
CLAUDE_PROCESS_NAMES = ("claude.exe",)
BLUEPRINT_MAIN = Path("docs") / "BLUEPRINT.md"
BLUEPRINT_SIDECAR_GLOB = "*.BLUEPRINT.md"
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHORT_ID = 8


# ===== 3 Core：纯逻辑（state 是普通 dict，进出都在这里算） =====
def empty_state():
    return {"version": 1, "enabled": None, "sessions": {}, "claims": {}, "taken": {}}


def normalize_state(st):
    base = empty_state()
    if isinstance(st, dict):
        base.update({k: st[k] for k in base if k in st})
    for k in ("sessions", "claims", "taken"):
        if not isinstance(base[k], dict):
            base[k] = {}
    return base


def rel_key(top, abs_path):
    rel = os.path.relpath(abs_path, top)
    return os.path.normcase(rel).replace("\\", "/")


def is_personal(enabled, has_blueprint):
    if enabled is False:
        return False
    return bool(enabled) or has_blueprint


def short(sid):
    return (sid or "?")[:SHORT_ID]


def in_family(holder, chain):
    """holder 是本进程链上的某个 claude 进程（同进程的子代理、本会话起的 headless 子会话）→ 视为同一家。"""
    return (holder.get("pid"), holder.get("ctime")) in chain


def upsert_session(st, me, now):
    s = st["sessions"].setdefault(me["sid"], {"task": None, "since": now})
    s.update(pid=me["pid"], ctime=me["ctime"], transcript=me.get("transcript") or s.get("transcript", ""),
             seen=now)
    return s


def drop_session(st, sid):
    st["sessions"].pop(sid, None)
    st["taken"].pop(sid, None)
    for rel in [r for r, c in st["claims"].items() if c.get("holder") == sid]:
        del st["claims"][rel]


def prune(st, alive):
    """alive(pid, ctime) → True / False / None（确认不了）。只清掉确认死亡的会话；无主的占用一并清掉。"""
    dead = [sid for sid, s in st["sessions"].items() if alive(s.get("pid"), s.get("ctime")) is False]
    for sid in dead:
        drop_session(st, sid)
    for rel in [r for r, c in st["claims"].items() if c.get("holder") not in st["sessions"]]:
        del st["claims"][rel]
    return dead


def evaluate_write(st, rel, me, chain, tool_id, worktree, now, released):
    """一次写入前的裁决。released(claim) → 该占用能否证明已交付。返回 (allow|deny, info)，并就地登记占用。"""
    taken = st["taken"].pop(me["sid"], None)
    upsert_session(st, me, now)
    if taken:
        return "deny", {"kind": "taken", **taken}
    claim = st["claims"].get(rel)
    if claim and claim.get("holder") != me["sid"]:
        holder = st["sessions"].get(claim.get("holder"))
        if holder is None:
            claim = None
        elif in_family(holder, chain):
            return "allow", {"kind": "delegated", "holder": claim["holder"]}
        elif not claim.get("inflight") and released(claim):
            claim = None
        else:
            return "deny", {"kind": "claim", "rel": rel, "holder": claim["holder"], "claim": claim}
    if claim is None:
        claim = st["claims"][rel] = {"holder": me["sid"], "worktree": worktree, "since": now,
                                     "inflight": {}, "written": False}
        new = True
    else:
        new = False
        claim["worktree"] = worktree
    claim.setdefault("inflight", {})[tool_id] = new
    return "allow", {"kind": "claimed", "new": new}


def finish_tool(st, tool_id, failed):
    """工具结束：成功 → 记为已写入；失败 → 只撤销这次新建、且从没写成过的占用。"""
    for rel, claim in list(st["claims"].items()):
        inflight = claim.get("inflight") or {}
        if tool_id not in inflight:
            continue
        created = inflight.pop(tool_id)
        if not failed:
            claim["written"] = True
        elif created and not inflight and not claim.get("written"):
            del st["claims"][rel]
        return rel
    return None


def claim_global(claims, key, me, now, path=None):
    """claims: {认领键: {sid,pid,ctime,transcript,since,path}}。别的会话占着 → (False, 它的会话号)；本会话重复认领幂等。"""
    held = claims.get(key)
    if held and held.get("sid") != me["sid"]:
        return False, held.get("sid")
    claims[key] = {"sid": me["sid"], "pid": me["pid"], "ctime": me["ctime"],
                   "transcript": me.get("transcript", ""), "since": (held or {}).get("since", now),
                   "path": path or (held or {}).get("path") or key}
    return True, me["sid"]


def prune_claims(claims, alive):
    dead = [k for k, c in claims.items() if alive(c.get("pid"), c.get("ctime")) is False]
    for k in dead:
        del claims[k]
    return dead


def release_claims(claims, sid):
    keys = [k for k, c in claims.items() if c.get("sid") == sid]
    for k in keys:
        del claims[k]
    return keys


def take_claim(claims, key, me, now):
    """只转这一项任务认领；返回原认领者会话号，没有可接手的返回 None。"""
    held = claims.get(key)
    if not held or held.get("sid") == me["sid"]:
        return None
    claims[key] = {"sid": me["sid"], "pid": me["pid"], "ctime": me["ctime"],
                   "transcript": me.get("transcript", ""), "since": now, "path": held.get("path") or key}
    return held.get("sid")


def tasks_by_sid(claims):
    out = {}
    for key, c in claims.items():
        out.setdefault(c.get("sid"), []).append(c.get("path") or key)
    return out


def task_label(path):
    """任务目录的短显示：<项目>/task/<Tnn-名称>。"""
    p = Path(path)
    return "/".join(p.parts[-3:]) if len(p.parts) >= 3 else str(p)


def release_mine(st, sid):
    """放掉本会话的任务认领和所有没有在途写入的文件占用；返回放掉的文件数。"""
    s = st["sessions"].get(sid)
    if s:
        s["task"] = None
    rels = [r for r, c in st["claims"].items() if c.get("holder") == sid and not c.get("inflight")]
    for rel in rels:
        del st["claims"][rel]
    return len(rels)


def resolve_target(st, target, me_sid):
    """target 是会话号前缀或任务短名 → 会话号；找不到或有歧义返回 None。"""
    by_task = [sid for sid, s in st["sessions"].items() if s.get("task") == target and sid != me_sid]
    if len(by_task) == 1:
        return by_task[0]
    by_id = [sid for sid in st["sessions"] if sid.startswith(target) and sid != me_sid]
    return by_id[0] if len(by_id) == 1 else None


def take_over(st, target_sid, me, now):
    """把对方的占用和任务认领转到本会话名下；对方下一次写入会被拦一次并被告知。"""
    upsert_session(st, me, now)
    other = st["sessions"].get(target_sid, {})
    moved, inflight = 0, 0
    for claim in st["claims"].values():
        if claim.get("holder") == target_sid:
            claim["holder"] = me["sid"]
            inflight += len(claim.get("inflight") or {})
            moved += 1
    task = other.get("task")
    if task:
        st["sessions"][me["sid"]]["task"] = task
        other["task"] = None
    if target_sid in st["sessions"]:
        st["taken"][target_sid] = {"by": me["sid"], "task": task, "at": now}
    return {"moved": moved, "inflight": inflight, "task": task}


def session_lines(st, me_sid, now, log_age, tasks=None):
    """log_age(session) → 对话日志距今秒数或 None；tasks: {会话号: [任务目录]}（全局认领快照）。"""
    tasks = tasks or {}
    lines = []
    counts = {}
    for c in st["claims"].values():
        counts[c.get("holder")] = counts.get(c.get("holder"), 0) + 1
    for sid, s in sorted(st["sessions"].items(), key=lambda kv: kv[1].get("since", 0)):
        mark = "（本会话）" if sid == me_sid else ""
        age = log_age(s)
        age_txt = "日志时间未知" if age is None else f"对话日志最后更新 {_age(age)}"
        lines.append(f"  - 会话 {short(sid)}{mark}：任务 {claimed_text(tasks.get(sid))}，"
                     f"占用本仓 {counts.get(sid, 0)} 个文件，{age_txt}")
    return lines


def claimed_text(paths):
    return "、".join(task_label(p) for p in paths) if paths else "（未认领）"


def _age(seconds):
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def deny_text(info, st, now, log_age, cmd, tasks=None):
    """cmd 已带 --cwd <目标文件所在仓库>；tasks 是全局认领快照，读不到时为 None。"""
    if info["kind"] == "taken":
        return (f"[pstate 锁] 用户已让会话 {short(info.get('by'))} 接手你在这个仓库的活"
                f"（任务 {info.get('task') or '无'}），你原来占着的文件都转给了它。这次写入已拦下。"
                "请停下，把这件事告诉用户；用户要你继续做别的事，再重试即可（这条提醒只出现一次）。")
    holder = st["sessions"].get(info["holder"], {})
    claim = info["claim"]
    age = log_age(holder)
    parts = [f"[pstate 锁] 这个文件正被另一个会话占用，本次写入已拦下。",
             f"- 文件：{info['rel']}",
             f"- 占用者：会话 {short(info['holder'])}，在 worktree {claim.get('worktree')}，"
             f"认领的任务 {'（未知）' if tasks is None else claimed_text(tasks.get(info['holder']))}，"
             + ("对话日志时间未知" if age is None else f"对话日志最后更新 {_age(age)}")]
    if claim.get("inflight"):
        parts.append(f"- 它还有 {len(claim['inflight'])} 个对这个文件的写入在执行中或等待批准")
    parts += ["请停下，把上面的情况告诉用户，问他：等对方做完，还是由本会话接手。",
              f"用户说接手后运行：{cmd} take {short(info['holder'])}，再重试这次写入。",
              "不要绕过：改用 shell、python 或别的工具写同一个文件也算绕过。"]
    return "\n".join(parts)


# ===== 4 Adapter：进程、文件锁、状态文件、git =====
class _WinProcess:
    """Windows：Toolhelp32 快照走父进程链，GetProcessTimes 取创建时间防 PID 复用。"""

    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.ct, self.wt = ctypes, wt
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k.CreateToolhelp32Snapshot.argtypes = (wt.DWORD, wt.DWORD)
        k.OpenProcess.restype = wt.HANDLE
        k.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
        k.CloseHandle.argtypes = (wt.HANDLE,)
        k.GetProcessTimes.argtypes = (wt.HANDLE,) + (ctypes.POINTER(wt.FILETIME),) * 4
        k.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
        self.k = k

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]
        self.Entry = Entry
        k.Process32FirstW.argtypes = (wt.HANDLE, ctypes.POINTER(Entry))
        k.Process32NextW.argtypes = (wt.HANDLE, ctypes.POINTER(Entry))

    def _table(self):
        snap = self.k.CreateToolhelp32Snapshot(0x2, 0)
        if not snap or snap == self.wt.HANDLE(-1).value:
            return {}
        e = self.Entry()
        e.dwSize = self.ct.sizeof(e)
        out = {}
        try:
            ok = self.k.Process32FirstW(snap, self.ct.byref(e))
            while ok:
                out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
                ok = self.k.Process32NextW(snap, self.ct.byref(e))
        finally:
            self.k.CloseHandle(snap)
        return out

    def ctime(self, pid):
        """返回 (创建时间, 是否仍在运行)；打不开返回 (None, 原因)。"""
        h = self.k.OpenProcess(0x1000, False, pid)
        if not h:
            return None, self.ct.get_last_error()
        try:
            c, x, kk, u = (self.wt.FILETIME() for _ in range(4))
            if not self.k.GetProcessTimes(h, self.ct.byref(c), self.ct.byref(x), self.ct.byref(kk), self.ct.byref(u)):
                return None, self.ct.get_last_error()
            code = self.wt.DWORD()
            running = bool(self.k.GetExitCodeProcess(h, self.ct.byref(code))) and code.value == 259
            return (c.dwHighDateTime << 32) | c.dwLowDateTime, running
        finally:
            self.k.CloseHandle(h)

    def ancestry(self):
        """从本进程往上的 [(pid, 创建时间, 进程名)]；父进程链断开（父已退出、PID 被复用）时就此截断。"""
        table = self._table()
        out, pid, child_ctime = [], os.getpid(), None
        while pid in table and len(out) < 64:
            ct, _ = self.ctime(pid)
            if ct is None or (child_ctime is not None and ct > child_ctime):
                break
            ppid, name = table[pid]
            out.append((pid, ct, name))
            if ppid in (0, pid):
                break
            pid, child_ctime = ppid, ct
        return out

    def alive(self, pid, ctime):
        if not pid:
            return False
        ct, state = self.ctime(int(pid))
        if ct is None:
            return False if state == 87 else None
        return state is True and ct == ctime


PROCESS_BACKENDS = {"nt": _WinProcess}


def process_backend():
    cls = PROCESS_BACKENDS.get(os.name)
    return cls() if cls else None


def identify(backend, sid, transcript=""):
    """本会话 = 父进程链上最近的 claude 进程。认不出返回 (None, [])，调用方据此放行、不加锁。"""
    if backend is None or not sid:
        return None, []
    chain = backend.ancestry()
    for i, (pid, ct, name) in enumerate(chain):
        if name.lower() in CLAUDE_PROCESS_NAMES:
            me = {"sid": sid, "pid": pid, "ctime": ct, "transcript": transcript}
            return me, [(p, c) for p, c, _ in chain[i:]]
    return None, []


def _lock_fd(fd):
    import msvcrt
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _unlock_fd(fd):
    import msvcrt
    os.lseek(fd, 0, 0)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _lock_fd_posix(fd):
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd_posix(fd):
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)


FILE_LOCKS = {"nt": (_lock_fd, _unlock_fd), "posix": (_lock_fd_posix, _unlock_fd_posix)}


@contextlib.contextmanager
def mutex(state_dir):
    """仓库级短锁：只保护一次"读状态—裁决—写回"。等不到就抛 TimeoutError，调用方按出错放行处理。"""
    lock, unlock = FILE_LOCKS[os.name]
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    fd = os.open(str(Path(state_dir) / MUTEX_FILE), os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + MUTEX_WAIT_SECONDS
        while True:
            try:
                os.lseek(fd, 0, 0)
                lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("pstate 锁文件忙")
                time.sleep(0.05)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)


def load_state(state_dir):
    p = Path(state_dir) / LOCK_FILE
    try:
        return normalize_state(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return empty_state()


def save_state(state_dir, st):
    p = Path(state_dir) / LOCK_FILE
    tmp = p.with_name(f"{LOCK_FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


class ClaimsError(RuntimeError):
    pass


def load_claims(home):
    """全局认领表。文件缺失 = 空；读不了或结构不对就抛 ClaimsError，绝不当空表覆盖。"""
    p = Path(home) / CLAIMS_FILE
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ClaimsError(f"任务认领表读不了：{p}（{e}）") from e
    if not isinstance(data, dict) or any(not isinstance(v, dict) or not v.get("sid") for v in data.values()):
        raise ClaimsError(f"任务认领表结构不对：{p}")
    return data


def save_claims(home, claims):
    p = Path(home) / CLAIMS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{CLAIMS_FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def claims_snapshot(home):
    """只读快照：不加锁、不清理、不写回；读不了返回 None，调用方据此显示“未知”。"""
    try:
        return tasks_by_sid(load_claims(home))
    except ClaimsError:
        return None


def task_dir_key(path):
    """Obsidian 任务目录 → (认领键, 显示用真实路径)；键是规范大小写的真实路径。
    不是已存在的 .../task/Tnn-名称 目录就抛 ValueError。"""
    p = Path(path).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise ValueError(f"任务目录必须是已存在的绝对路径：{path}")
    real = Path(os.path.realpath(p))
    if real.parent.name.lower() != "task" or real.name[:1] not in ("T", "t") or not real.name[1:2].isdigit():
        raise ValueError(f"不是 Obsidian 任务目录（应为 .../task/Tnn-名称）：{real}")
    return os.path.normcase(str(real)), str(real)


def git(args, cwd):
    r = subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SECONDS)
    return r.returncode, r.stdout


def locate(abs_path):
    """目标文件 → (worktree 根, git common dir)；不在 git 里返回 None。"""
    d = Path(abs_path).parent
    while not d.is_dir():
        if d.parent == d:
            return None
        d = d.parent
    rc, out = git(["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"], str(d))
    lines = out.splitlines()
    if rc != 0 or len(lines) < 2:
        return None
    return os.path.realpath(lines[0]), os.path.realpath(lines[1])


def has_blueprint(top, abs_path):
    if (Path(top) / BLUEPRINT_MAIN).is_file():
        return True
    d = Path(abs_path).parent
    return d.is_dir() and any(d.glob(BLUEPRINT_SIDECAR_GLOB))


def released(claim, rel):
    """能证明"持有者那边已交付"才算释放：worktree 没了；或文件干净、不是被忽略文件、且分支已并进集成分支。"""
    wt = claim.get("worktree") or ""
    if not os.path.isdir(wt):
        return True
    rc, out = git(["status", "--porcelain", "-z", "--untracked-files=all", "--", rel], wt)
    if rc != 0 or out.strip("\0"):
        return False
    rc, _ = git(["check-ignore", "-q", "--", rel], wt)
    if rc == 0:
        return False
    for branch in INTEGRATION_BRANCHES:
        rc, _ = git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], wt)
        if rc == 0:
            rc, _ = git(["merge-base", "--is-ancestor", "HEAD", f"refs/heads/{branch}"], wt)
            return rc == 0
    return False


def log_age(session, now=None):
    t = session.get("transcript")
    try:
        return (now or time.time()) - os.path.getmtime(t) if t else None
    except OSError:
        return None
