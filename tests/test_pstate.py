import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pstate  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


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
        text = pstate.new_task_text("标题", "C:/wt", "dev", "阻塞")
        text = text.replace("## 停在哪 / 下一步\n", "## 停在哪 / 下一步\n\n- 改 comm_err 表\n- 然后编译\n")
        t = pstate.parse_task(text)
        self.assertEqual(t["title"], "标题")
        self.assertEqual(t["fields"][pstate.FIELD_WORKTREE], "C:/wt")
        self.assertEqual(t["fields"][pstate.FIELD_STATUS], "阻塞")
        self.assertEqual(pstate.next_step(t), "改 comm_err 表")

    def test_repo_key_is_stable_and_distinct(self):
        a = pstate.repo_key("C:/x/proj/.git")
        self.assertEqual(a, pstate.repo_key("C:/x/proj/.git/"))
        self.assertTrue(a.startswith("proj-"))
        self.assertNotEqual(a, pstate.repo_key("D:/y/proj/.git"))

    def test_render_status_flags_stale_and_missing_worktree(self):
        wts = [{"path": "C:/a", "branch": "main", "head": "1234567", "prunable": False, "changes": (1, 0)}]
        now = 100 * 86400
        task = pstate.parse_task(pstate.new_task_text("旧活", "C:/gone", "x", "进行中"))
        out = pstate.render_status("proj", "D", wts, [("old", task, now - 30 * 86400)], "C:/a", now)
        self.assertIn("久未更新", out)
        self.assertIn("工作区已不在", out)
        self.assertIn("改动 1，未跟踪 0", out)


class EndToEndTest(unittest.TestCase):
    """真 git 仓库 + 第二个 worktree：两边看到同一份任务，hook 只在有任务时输出。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.main = base / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
        self.wt2 = base / "wt2"
        git(self.main, "worktree", "add", "-q", str(self.wt2), "-b", "feat")
        self.env = mock.patch.dict(os.environ, {pstate.STATE_HOME_ENV: str(base / "state")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, stdin=None):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch("sys.stdin", io.StringIO(stdin or "")):
            rc = pstate.main(list(argv))
        return rc, buf.getvalue()

    def hook(self, cwd):
        return self.run_cli("hook", stdin=json.dumps({"cwd": str(cwd), "hook_event_name": "SessionStart"}))

    def test_flow(self):
        rc, out = self.hook(self.main)
        self.assertEqual((rc, out), (0, ""), "没有任务时 hook 必须静默")

        rc, path = self.run_cli("--cwd", str(self.wt2), "new", "t1", "--title", "第一件")
        self.assertEqual(rc, 0)
        self.assertIn("wt2", Path(path.strip()).read_text(encoding="utf-8"))

        rc, out = self.run_cli("--cwd", str(self.main), "status")
        self.assertIn("t1：第一件", out, "从另一个 worktree 也要看到同一份任务")

        rc, out = self.hook(self.wt2)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("当前工作区的任务全文", ctx)
        rc, out = self.hook(self.main)
        self.assertNotIn("当前工作区的任务全文", json.loads(out)["hookSpecificOutput"]["additionalContext"])

        self.assertEqual(self.run_cli("--cwd", str(self.wt2), "new", "t1", "--title", "重复")[0], 1)
        self.assertEqual(self.run_cli("--cwd", str(self.main), "done", "t1")[0], 0)
        self.assertEqual(self.hook(self.wt2), (0, ""))

    def test_hook_outside_repo_is_silent(self):
        self.assertEqual(self.hook(self.tmp.name), (0, ""))


if __name__ == "__main__":
    unittest.main()
