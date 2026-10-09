import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pstate  # noqa: E402
import pstate_lock as L  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


class CoreTest(unittest.TestCase):
    def test_parse_worktrees(self):
        text = ("worktree C:/a\nHEAD 1234567890abc\nbranch refs/heads/main\n\n"
                "worktree C:/b\nHEAD abcdef0123456\ndetached\n\n"
                "worktree C:/gone\nHEAD 0000000000000\nbranch refs/heads/x\nprunable gitdir file points to non-existent location\n")
        wts = pstate.parse_worktrees(text)
        self.assertEqual([w["path"] for w in wts], ["C:/a", "C:/b", "C:/gone"])
        self.assertEqual(wts[0]["branch"], "main")
        self.assertEqual(wts[1]["branch"], "(detached)")
        self.assertTrue(wts[2]["prunable"])

    def test_count_changes(self):
        self.assertEqual(pstate.count_changes(" M a.c\nM  b.c\n?? c.txt\n"), (2, 1))
        self.assertEqual(pstate.count_changes(""), (0, 0))

    def test_parse_task_and_next_step(self):
        text = pstate.new_task_text("标题", "C:/wt", "dev", "阻塞", "C:/wt/docs/HANDOFF.md")
        self.assertNotIn("停在哪", text, "新模板不再有进度节")
        text += "\n## 停在哪 / 下一步\n\n- 改 comm_err 表\n- 然后编译\n"
        t = pstate.parse_task(text)
        self.assertEqual(t["title"], "标题")
        self.assertEqual(t["fields"][pstate.FIELD_WORKTREE], "C:/wt")
        self.assertEqual(t["fields"][pstate.FIELD_STATUS], "阻塞")
        self.assertEqual(t["fields"][pstate.FIELD_HANDOFF], "C:/wt/docs/HANDOFF.md")
        self.assertEqual(pstate.next_step(t), "改 comm_err 表")
        self.assertIn("- 状态: 进行中", pstate.set_field(text, pstate.FIELD_STATUS, "进行中"))

    def test_repo_key_is_stable_and_distinct(self):
        a = pstate.repo_key("C:/x/proj/.git")
        self.assertEqual(a, pstate.repo_key("C:/x/proj/.git/"))
        self.assertTrue(a.startswith("proj-"))
        self.assertNotEqual(a, pstate.repo_key("D:/y/proj/.git"))

    def test_render_status_flags_stale_and_missing_worktree(self):
        wts = [{"path": "C:/a", "branch": "main", "head": "1234567", "prunable": False, "changes": (1, 0)}]
        now = 100 * 86400
        task = pstate.parse_task(pstate.new_task_text("旧活", "C:/gone", "x", "进行中"))
        out = pstate.render_status("proj", "D", wts, [("old", task, now - 30 * 86400)], "C:/a", now,
                                   ["  - 会话 abc"], {"old": "abc"})
        self.assertIn("久未更新", out)
        self.assertIn("工作区已不在", out)
        self.assertIn("改动 1，未跟踪 0", out)
        self.assertIn("活会话（1）", out)
        self.assertIn("由会话 abc 认领", out)


A = {"sid": "aaaa1111-x", "pid": 100, "ctime": 1}
B = {"sid": "bbbb2222-y", "pid": 200, "ctime": 2}
CHAIN_A = [(100, 1), (9, 0)]
CHAIN_B = [(200, 2), (9, 0)]
NEVER = lambda c: False  # noqa: E731
ALWAYS = lambda c: True  # noqa: E731


class LockCoreTest(unittest.TestCase):
    def setUp(self):
        self.st = L.empty_state()

    def write(self, me, chain, rel="a.py", tid="t", released=NEVER):
        return L.evaluate_write(self.st, rel, me, chain, tid, "W", 0, released)

    def test_second_session_is_denied_and_owner_passes(self):
        self.assertEqual(self.write(A, CHAIN_A, tid="t1")[0], "allow")
        d, info = self.write(B, CHAIN_B, tid="t2")
        self.assertEqual((d, info["holder"]), ("deny", A["sid"]))
        self.assertEqual(self.write(A, CHAIN_A, tid="t3")[1], {"kind": "claimed", "new": False})
        self.assertEqual(self.write(B, CHAIN_B, rel="b.py", tid="t4")[0], "allow", "别的文件不受影响")

    def test_family_is_allowed_without_taking_ownership(self):
        self.write(A, CHAIN_A)
        child = {"sid": "child", "pid": 300, "ctime": 3}
        d, info = self.write(child, [(300, 3), (100, 1), (9, 0)])
        self.assertEqual((d, info["kind"]), ("allow", "delegated"))
        self.assertEqual(self.st["claims"]["a.py"]["holder"], A["sid"])

    def test_inflight_blocks_even_when_delivered(self):
        self.write(A, CHAIN_A, tid="t1")
        self.assertEqual(self.write(B, CHAIN_B, tid="t2", released=ALWAYS)[0], "deny", "在途写入必须互斥")
        L.finish_tool(self.st, "t1", failed=False)
        self.assertEqual(self.write(B, CHAIN_B, tid="t3", released=ALWAYS)[0], "allow")
        self.assertEqual(self.st["claims"]["a.py"]["holder"], B["sid"])

    def test_failure_only_rolls_back_fresh_claim(self):
        self.write(A, CHAIN_A, tid="t1")
        L.finish_tool(self.st, "t1", failed=True)
        self.assertNotIn("a.py", self.st["claims"])
        self.write(A, CHAIN_A, tid="t2")
        L.finish_tool(self.st, "t2", failed=False)
        self.write(A, CHAIN_A, tid="t3")
        L.finish_tool(self.st, "t3", failed=True)
        self.assertIn("a.py", self.st["claims"], "之前成功写过的占用不能被一次失败撤掉")

    def test_prune_only_drops_confirmed_dead(self):
        self.write(A, CHAIN_A)
        self.write(B, CHAIN_B, rel="b.py")
        L.prune(self.st, lambda pid, ct: False if pid == 100 else None)
        self.assertNotIn(A["sid"], self.st["sessions"])
        self.assertNotIn("a.py", self.st["claims"])
        self.assertIn("b.py", self.st["claims"], "确认不了死活的不能当死亡处理")

    def test_take_transfers_and_warns_old_holder_once(self):
        self.write(A, CHAIN_A, tid="t1")
        L.finish_tool(self.st, "t1", failed=False)
        self.assertTrue(L.claim_task(self.st, "fix", A, 0)[0])
        self.assertEqual(L.claim_task(self.st, "fix", B, 0), (False, A["sid"]))
        self.assertEqual(L.resolve_target(self.st, "fix", B["sid"]), A["sid"])
        info = L.take_over(self.st, A["sid"], B, 0)
        self.assertEqual((info["moved"], info["task"]), (1, "fix"))
        self.assertEqual(self.st["sessions"][B["sid"]]["task"], "fix")
        d, info = self.write(A, CHAIN_A, rel="other.py", tid="t5")
        self.assertEqual((d, info["kind"]), ("deny", "taken"))
        self.assertEqual(self.write(A, CHAIN_A, rel="other.py", tid="t6")[0], "allow", "被接手的提醒只拦一次")
        self.assertEqual(self.write(A, CHAIN_A, tid="t7")[0], "deny", "转走的文件现在归接手方")

    def test_release_mine(self):
        self.write(A, CHAIN_A, tid="t1")
        self.write(A, CHAIN_A, rel="b.py", tid="t2")
        L.finish_tool(self.st, "t1", failed=False)
        self.assertEqual(L.release_mine(self.st, A["sid"]), 1, "在途的占用不放")
        self.assertEqual(list(self.st["claims"]), ["b.py"])

    def test_is_personal(self):
        self.assertTrue(L.is_personal(None, True))
        self.assertFalse(L.is_personal(None, False))
        self.assertTrue(L.is_personal(True, False))
        self.assertFalse(L.is_personal(False, True))


class FakeProc:
    def __init__(self):
        self.chain = []
        self.dead = set()

    def ancestry(self):
        return self.chain

    def alive(self, pid, ctime):
        return pid not in self.dead


class EndToEndTest(unittest.TestCase):
    """真 git 仓库（带蓝图 = 个人项目）+ 第二个 worktree；进程身份用假后端模拟两个会话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.main = base / "main"
        (self.main / "docs").mkdir(parents=True)
        (self.main / "docs" / "BLUEPRINT.md").write_text("# bp\n", encoding="utf-8")
        (self.main / "a.txt").write_text("0\n", encoding="utf-8")
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "add", ".")
        git(self.main, "commit", "-q", "-m", "i")
        self.wt2 = base / "wt2"
        git(self.main, "worktree", "add", "-q", str(self.wt2), "-b", "feat")
        self.plain = base / "plain"
        self.plain.mkdir()
        git(self.plain, "init", "-q", "-b", "main")
        git(self.plain, "commit", "-q", "--allow-empty", "-m", "i")
        self.state = base / "state"
        self.proc = FakeProc()
        self.patches = [mock.patch.dict(os.environ, {pstate.STATE_HOME_ENV: str(self.state)}),
                        mock.patch.object(L, "process_backend", lambda: self.proc)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, stdin=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin or "")), \
                mock.patch.dict(os.environ, env or {}):
            rc = pstate.main(list(argv))
        return rc, out.getvalue() + err.getvalue()

    def as_session(self, s):
        self.proc.chain = [(s["pid"], s["ctime"], "claude.exe"), (9, 0, "Code.exe")]

    def pre(self, s, path, tid):
        self.as_session(s)
        data = {"session_id": s["sid"], "tool_name": "Edit", "tool_use_id": tid, "cwd": str(path.parent),
                "tool_input": {"file_path": str(path)}, "hook_event_name": "PreToolUse"}
        rc, out = self.run_cli("hook-pre", stdin=json.dumps(data))
        self.assertEqual(rc, 0)
        return json.loads(out)["hookSpecificOutput"] if out else None

    def post(self, tid, failed=False):
        return self.run_cli("hook-fail" if failed else "hook-post", stdin=json.dumps({"tool_use_id": tid}))

    def session_start(self, s, cwd):
        data = {"cwd": str(cwd), "hook_event_name": "SessionStart", "session_id": s["sid"]}
        rc, out = self.run_cli("hook", stdin=json.dumps(data))
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def test_task_board_flow(self):
        self.assertEqual(self.session_start(A, self.main), "", "没有条目也没有别的会话时必须静默")
        rc, path = self.run_cli("--cwd", str(self.wt2), "new", "t1", "--title", "第一件")
        self.assertEqual(rc, 0)
        text = Path(path.strip()).read_text(encoding="utf-8")
        self.assertIn("wt2", text)
        self.assertIn("- 状态: 待办", text)
        rc, out = self.run_cli("--cwd", str(self.main), "status")
        self.assertIn("t1：第一件", out, "从另一个 worktree 也要看到同一份条目")
        self.assertIn("当前工作区的条目全文", self.session_start(A, self.wt2))
        self.assertNotIn("当前工作区的条目全文", self.session_start(A, self.main))
        self.assertEqual(self.run_cli("--cwd", str(self.wt2), "new", "t1", "--title", "重复")[0], 1)

        self.as_session(A)
        rc, _ = self.run_cli("--cwd", str(self.main), "claim", "t1", env={pstate.SESSION_ENV: A["sid"]})
        self.assertEqual(rc, 0)
        self.assertIn("- 状态: 进行中", Path(path.strip()).read_text(encoding="utf-8"))
        self.as_session(B)
        rc, out = self.run_cli("--cwd", str(self.main), "claim", "t1", env={pstate.SESSION_ENV: B["sid"]})
        self.assertEqual(rc, 1)
        self.assertIn("aaaa1111", out)
        self.assertIn("由会话 aaaa1111 认领", self.session_start(B, self.main))
        rc, out = self.run_cli("--cwd", str(self.main), "take", "t1", env={pstate.SESSION_ENV: B["sid"]})
        self.assertEqual(rc, 0, out)
        self.assertIn("由会话 bbbb2222 认领", self.session_start(A, self.main))

        self.assertEqual(self.run_cli("--cwd", str(self.main), "done", "t1")[0], 0)
        self.assertNotIn("t1：", self.session_start(A, self.main))

    def test_new_refused_in_company_repo(self):
        rc, out = self.run_cli("--cwd", str(self.plain), "new", "x", "--title", "公司活")
        self.assertEqual(rc, 1)
        self.assertIn("--force", out)
        self.assertEqual(self.run_cli("--cwd", str(self.plain), "new", "x", "--title", "公司活", "--force")[0], 0)

    def test_cross_worktree_conflict_until_merged(self):
        f_main, f_wt2 = self.main / "a.txt", self.wt2 / "a.txt"
        self.assertIsNone(self.pre(A, f_wt2, "t1"))
        f_wt2.write_text("A\n", encoding="utf-8")
        self.post("t1")
        deny = self.pre(B, f_main, "t2")
        self.assertEqual(deny["permissionDecision"], "deny", "不同 worktree 里的同一相对路径也要互斥")
        self.assertIn("aaaa1111", deny["permissionDecisionReason"])
        self.assertIn("take aaaa1111", deny["permissionDecisionReason"])
        ctx = self.session_start(B, self.main)
        self.assertIn("活会话（2）", ctx, "没有条目但有别的活会话也要注入")
        self.assertIn("bbbb2222（本会话）", ctx)

        git(self.wt2, "commit", "-q", "-am", "A")
        self.assertIsNotNone(self.pre(B, f_main, "t3"), "提交了但没并进 main 仍算没交付")
        git(self.main, "merge", "-q", "feat")
        self.assertIsNone(self.pre(B, f_main, "t4"), "并进 main 且干净后自动释放")
        self.post("t4")
        f_main.write_text("B\n", encoding="utf-8")
        self.assertIsNotNone(self.pre(A, f_wt2, "t5"), "现在归 B")

    def test_failure_rollback_and_session_end(self):
        f = self.main / "a.txt"
        self.assertIsNone(self.pre(A, f, "t1"))
        self.post("t1", failed=True)
        self.assertIsNone(self.pre(B, f, "t2"), "A 失败的新占用撤销后 B 能写")
        self.post("t2")
        f.write_text("B\n", encoding="utf-8")
        self.assertIsNotNone(self.pre(A, f, "t3"))
        self.run_cli("hook-end", stdin=json.dumps({"session_id": B["sid"]}))
        self.assertIsNone(self.pre(A, f, "t4"), "B 会话结束后占用随之释放")

    def test_dead_process_releases(self):
        f = self.main / "a.txt"
        self.pre(A, f, "t1")
        self.post("t1")
        f.write_text("A\n", encoding="utf-8")
        self.assertIsNotNone(self.pre(B, f, "t2"))
        self.proc.dead.add(A["pid"])
        self.assertIsNone(self.pre(B, f, "t3"), "A 窗口关了（进程没了）锁自动失效")

    def test_company_repo_and_lock_switch(self):
        f = self.plain / "x.c"
        self.assertIsNone(self.pre(A, f, "t1"))
        self.assertIsNone(self.pre(B, f, "t2"), "公司仓不加锁")
        self.assertFalse(self.state.exists() and any(self.state.glob("*/locks.json")))
        self.run_cli("--cwd", str(self.plain), "lock", "on")
        self.assertIsNone(self.pre(A, f, "t3"))
        self.assertIsNotNone(self.pre(B, f, "t4"), "lock on 之后加锁")
        self.run_cli("--cwd", str(self.main), "lock", "off")
        self.pre(A, self.main / "a.txt", "t5")
        self.assertIsNone(self.pre(B, self.main / "a.txt", "t6"), "lock off 优先于蓝图")

    def test_hook_entry_never_fails(self):
        self.assertEqual(pstate.run_as_hook(["hook-pre", "--bogus"]), 0)
        with mock.patch("sys.stdin", io.StringIO("not json")):
            self.assertEqual(pstate.run_as_hook(["hook-pre"]), 0)

    def test_hook_outside_repo_is_silent(self):
        self.assertEqual(self.session_start(A, self.tmp.name), "")


@unittest.skipUnless(os.name == "nt", "进程身份只实现了 Windows")
class WinProcessTest(unittest.TestCase):
    def test_ancestry_starts_with_self_and_alive(self):
        proc = L._WinProcess()
        chain = proc.ancestry()
        self.assertEqual(chain[0][0], os.getpid())
        self.assertTrue(proc.alive(chain[0][0], chain[0][1]))
        self.assertFalse(proc.alive(chain[0][0], chain[0][1] + 1), "创建时间对不上 = PID 被复用")
        self.assertFalse(proc.alive(0, 0))


if __name__ == "__main__":
    unittest.main()
