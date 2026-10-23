"""Joint-source/matter invariants using temporary stores and fake model calls."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pstate_history import PLATFORMS, load_sessions
from pstate_sweep import core, runtime, migration
from pstate_sweep.adapters import store
from pstate_threads import store as threads
from sweep_helpers import DAY, NOW, FakeGate, FakeJudge, assistant, human, make_cfg, sid, tool_result, write_transcript


class UnionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = make_cfg(self.root)
        self.work = self.root / "work"
        self.work.mkdir()
        self.state = self.root / "home" / "sweep"
        self.threads = self.root / "home" / "threads"
        self.out = self.root / "output"
        self.stamp = NOW - 5 * DAY
        self.rows = [human("build tool later", self.stamp, cwd=str(self.work))]
        self.path = write_transcript(self.cfg["projects_dir"], sid(1), self.rows)
        migration.switch(self.cfg, self.state.parent, self.out)

    def tearDown(self):
        self.temp.cleanup()

    def run_once(self, judge=None, **options):
        return runtime.run_sweep(self.cfg, judge or FakeJudge(), lambda: FakeGate(), self.out,
            self.state, now=NOW, threads_dir=self.threads, **options)

    def text(self, closed=False):
        return (self.out / self.cfg["closed_name" if closed else "page_name"]).read_text(encoding="utf-8")

    def source(self):
        text = "build tool later"
        return {"platform": "claude", "session": sid(1), "anchor": "line:1", "time": "2026-10-01T00:00:00Z",
                "role": "user", "excerpt": text, "excerpt_hash": hashlib.sha256(text.encode()).hexdigest()}

    def add_matter(self, ident="matter1"):
        return threads.mutate(self.threads, "add", {"title": "Tool design " + ident,
            "motivation": "motivation first line\nsecond line", "questions": ["unresolved choice"],
            "stopped_at": "as of original source time", "next_suggestion": "AI suggestion only",
            "paths": [str(self.work)], "handoff": [str(self.work / "docs/HANDOFF.md")],
            "sources": [self.source()]}, ident=ident, distinct=True)

    def check(self, prefix, closed=False):
        path = self.out / self.cfg["closed_name" if closed else "page_name"]
        text = path.read_text(encoding="utf-8")
        lines = [line.replace("[ ]", "[x]", 1) if prefix in line else line for line in text.splitlines()]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_partial_link_stays_unreviewed_seen_hides_only_pending(self):
        self.add_matter()
        report = self.run_once()
        self.assertEqual(report["listed"], 2)
        self.assertIn("已关联1项，余部未审", self.text())
        state = store.load_state(self.state)
        summary = state["sessions"]["claude:" + sid(1)]["summary"]
        threads.mutate(self.threads, "seen", {"platform": "claude", "session": sid(1),
            "version": summary["review_version"]})
        report = self.run_once(no_judge=True)
        self.assertEqual((report["listed"], report["reviewed_sources"]), (1, 1))
        self.assertIn("已审来源资料", self.text())
        self.assertIn("停在某处", self.text())
        self.assertEqual(summary, store.load_state(self.state)["sessions"]["claude:" + sid(1)]["summary"])

    def test_source_checkbox_closes_whole_version_without_thread_or_seen(self):
        self.add_matter()
        self.run_once()
        before = threads.read(self.threads)
        self.check("%%ss:claude:" + sid(1) + "@")
        report = self.run_once(no_judge=True)
        self.assertEqual(report["closed"], 1)
        self.assertEqual(before, threads.read(self.threads))
        self.assertIn("Tool design matter1", self.text())
        self.assertIn("停在某处", self.text(closed=True))
        discovered = load_sessions({"files": {"claude": [str(self.path)]}})
        self.assertEqual(len(discovered["sessions"]), 1)

    def test_matter_checkbox_only_closes_reminder_full_card_preserved(self):
        self.add_matter()
        self.run_once()
        before = threads.read(self.threads)
        self.check("%%pt:matter1@1%%")
        self.run_once(no_judge=True)
        self.assertEqual(before, threads.read(self.threads))
        text = self.text(closed=True)
        for value in ("motivation first line", "second line", "unresolved choice", "as of original source time",
                      "AI suggestion only", "build tool later", "HANDOFF.md", "事项：matter1 · open · rev 1"):
            self.assertIn(value, text)
        path = self.out / self.cfg["closed_name"]
        path.write_text(text.replace("[x]", "[ ]"), encoding="utf-8")
        self.run_once(no_judge=True)
        self.assertIn("%%pt:matter1@1%%", self.text())
        self.assertEqual(before, threads.read(self.threads))

    def test_new_source_never_reopens_parked_item(self):
        self.add_matter()
        threads.mutate(self.threads, "later", {"said": "later", "source": self.source()}, ident="matter1", rev=1)
        self.run_once()
        rows = self.rows + [human("new discussion", self.stamp + DAY, cwd=str(self.work))]
        write_transcript(self.cfg["projects_dir"], sid(1), rows)
        self.run_once()
        self.assertEqual(threads.read(self.threads)["items"]["matter1"]["state"], "parked")
        self.assertNotIn("%%pt:matter1@", self.text())
        self.assertIn("已关联1项，余部未审", self.text())

    def test_same_timestamp_tool_change_does_not_inherit_new_or_legacy_tick(self):
        rows = self.rows + [assistant("writing", self.stamp + 1, tool=("tool1", "A.py")),
                            tool_result("tool1", self.stamp + 2)]
        write_transcript(self.cfg["projects_dir"], sid(1), rows)
        self.run_once()
        original = store.load_state(self.state)["sessions"]["claude:" + sid(1)]["summary"]
        self.check("%%ss:claude:" + sid(1) + "@")
        self.run_once(no_judge=True)
        self.assertTrue(store.load_state(self.state)["closures"])
        rows[1]["message"]["content"][1]["input"]["file_path"] = "B.py"
        write_transcript(self.cfg["projects_dir"], sid(1), rows)
        report = self.run_once(no_judge=True)
        self.assertFalse(store.load_state(self.state)["closures"])
        self.assertEqual(report["queued"], 1)
        closed = self.out / self.cfg["closed_name"]
        closed.write_text(f"- [x] legacy %%ss:{sid(1)}@{original['version']}%%\n", encoding="utf-8")
        judge = FakeJudge()
        self.run_once(judge)
        self.assertEqual(len(judge.requests), 1)
        self.assertFalse(store.load_state(self.state)["closures"])
        self.assertIn("默认标题", self.text())

    def test_missing_and_recovered_source_preserves_summary_and_verdict(self):
        self.run_once()
        before = copy.deepcopy(store.load_state(self.state)["sessions"]["claude:" + sid(1)])
        self.path.unlink()
        self.run_once()
        self.assertIn("原文不可用", self.text())
        missing = store.load_state(self.state)["sessions"]["claude:" + sid(1)]
        self.assertEqual(before["summary"], missing["summary"])
        self.assertEqual(before["verdict"], missing["verdict"])
        write_transcript(self.cfg["projects_dir"], sid(1), self.rows)
        judge = FakeJudge()
        self.run_once(judge)
        self.assertFalse(store.load_state(self.state)["sessions"]["claude:" + sid(1)]["gone"])
        self.assertNotIn("原文不可用", self.text())
        self.assertFalse(judge.requests)

    def test_unreadable_history_not_asserted_gone(self):
        self.run_once()
        empty = lambda *args, **kw: {"sessions": [], "coverage": {"unreadable": 1},
                                    "warnings": [{"kind": "unreadable", "path": str(self.path)}]}
        self.run_once(history_loader=empty)
        entry = store.load_state(self.state)["sessions"]["claude:" + sid(1)]
        self.assertFalse(entry["gone"])
        self.assertIn("原文暂不可读", self.text())

    def test_dry_run_uses_write_gates_without_state_pages_or_model(self):
        self.add_matter()
        judge = FakeJudge()
        source_before = (self.state / store.STATE_FILE).read_bytes()
        pages_before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        report = self.run_once(judge, dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["planned"], 1)
        self.assertEqual(report["calls"], 0)
        self.assertFalse(judge.requests)
        self.assertEqual(source_before, (self.state / store.STATE_FILE).read_bytes())
        self.assertEqual(pages_before, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_matter_short_lock_is_not_held_across_model_call(self):
        outer = self
        class MutatingJudge(FakeJudge):
            def judge(self, request):
                outer.add_matter()
                return super().judge(request)
        self.run_once(MutatingJudge())
        self.assertEqual(threads.read(self.threads)["items"]["matter1"]["state"], "open")
        self.assertIn("Tool design matter1", self.text())

    def test_unreadable_page_stops_before_any_page_rewrite(self):
        self.run_once()
        path = self.out / self.cfg["page_name"]
        before = path.read_bytes()
        original = store.read_text
        def unreadable(target):
            if Path(target) == path:
                raise PermissionError("locked page")
            return original(target)
        with patch.object(store, "read_text", side_effect=unreadable):
            with self.assertRaises(PermissionError):
                self.run_once(no_judge=True)
        self.assertEqual(path.read_bytes(), before)

    def test_platform_selection_comes_only_from_capability_registry(self):
        self.cfg["history_roots"]["future"] = [str(self.root / "future")]
        with patch.dict(PLATFORMS, {"future": {"background": True}}):
            options = runtime.history_options(self.cfg)
            self.assertEqual(options["roots"]["future"], [str(self.root / "future")])
            disabled = [name for name, adapter in PLATFORMS.items() if not adapter.get("background")]
            self.assertTrue(all(options["roots"][name] == [] for name in disabled))

    def test_kernel_run_lock_blocks_a_second_process(self):
        script = "from pstate_sweep.adapters.store import RunLock; import sys; RunLock(sys.argv[1]).__enter__()"
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
        with store.RunLock(self.state):
            proc = subprocess.run([sys.executable, "-c", script, str(self.state)], env=env, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("another sweep run is active", proc.stderr)
