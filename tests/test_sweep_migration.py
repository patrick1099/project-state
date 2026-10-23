"""Isolated migration, ownership, interruption and actual CLI acceptance."""
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
from pstate_history import load_sessions
from pstate_sweep import core, migration, runtime
from pstate_sweep.adapters import output, store
from pstate_threads import store as threads
from sweep_helpers import NOW, DAY, FakeGate, FakeJudge, human, make_cfg, sid, write_transcript


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.state = self.home / "sweep"
        self.out = self.root / "output"
        self.old = self.root / "old" / "state.json"
        self.old.parent.mkdir()
        self.cfg = make_cfg(self.root)
        self.stamp = NOW - 4 * DAY
        self.path = write_transcript(self.cfg["projects_dir"], sid(1), [human("original words", self.stamp)])
        modern = load_sessions({"files": {"claude": [str(self.path)]}})["sessions"][0]["summary"]
        old_summary = {k: copy.deepcopy(v) for k, v in modern.items() if k not in
                       ("activity_version", "review_version", "identity", "platform", "native_session")}
        self.legacy = {"version": 1, "sessions": {sid(1): {"summary": old_summary, "path": str(self.path),
            "verdict": {"version": modern["version"], "status": "悬置", "title": "original title",
                        "stopped_at": "full original summary", "owner": "个人"}}},
            "closures": {}, "last_run": {"at": 123}, "custom_original": ["unknown legacy field"]}
        self.write_legacy()

    def tearDown(self):
        self.tmp.cleanup()

    def write_legacy(self):
        self.old.write_text(json.dumps(self.legacy, ensure_ascii=False), encoding="utf-8")

    def enable(self, dry_run=False):
        return migration.switch(self.cfg, self.home, self.out, self.old, dry_run=dry_run)

    def run_once(self, judge=None, **kwargs):
        return runtime.run_sweep(self.cfg, judge or FakeJudge(), lambda: FakeGate(), self.out,
                                 self.state, now=NOW, threads_dir=self.home / "threads", **kwargs)

    def page(self):
        return self.out / self.cfg["page_name"]

    def test_import_exact_material_all_filters_no_threads_or_seen_idempotent(self):
        for n, (status, owner) in enumerate((("完成", "个人"), ("放弃", "个人"), ("悬置", "公司")), 2):
            entry = copy.deepcopy(self.legacy["sessions"][sid(1)])
            entry["verdict"].update(status=status, owner=owner, title="hidden" + str(n))
            entry.pop("path")
            self.legacy["sessions"][sid(n)] = entry
        bad = copy.deepcopy(self.legacy["sessions"][sid(1)])
        bad.pop("verdict")
        bad["error"] = {"msg": "all failure details", "version": bad["summary"]["version"], "retry": False, "at": 11}
        self.legacy["sessions"][sid(5)] = bad
        self.write_legacy()
        self.out.mkdir()
        self.page().write_text("complete original main page", encoding="utf-8")
        archive = self.out / self.cfg["archive_dir"] / "2001-01-01~01-07.md"
        archive.parent.mkdir()
        archive.write_text(core.ARCHIVE_MARK + "\ncomplete orphan archive", encoding="utf-8")
        result = migration.import_legacy(self.cfg, self.home, self.old, self.out)
        state = store.load_state(self.state)
        original = state["legacy_imports"][result["legacy_import"]]
        self.assertEqual(original["state"], self.legacy)
        self.assertIn("complete original main page", original["pages"].values())
        self.assertIn("complete orphan archive", original["pages"]["归档/2001-01-01~01-07.md"])
        self.assertEqual(len(state["sessions"]), 5)
        before = (self.state / store.STATE_FILE).read_bytes()
        repeated = migration.import_legacy(self.cfg, self.home, self.old, self.out)
        self.assertTrue(repeated["repeated"])
        self.assertEqual(before, (self.state / store.STATE_FILE).read_bytes())
        self.assertFalse((self.home / "threads" / "threads.json").exists())
        self.enable()
        self.run_once(no_judge=True)
        self.assertTrue(archive.exists())
        self.assertIn("complete orphan archive", archive.read_text(encoding="utf-8"))
        self.assertNotIn("hidden2", self.page().read_text(encoding="utf-8"))
        self.assertNotIn("hidden3", self.page().read_text(encoding="utf-8"))
        self.assertNotIn("hidden4", self.page().read_text(encoding="utf-8"))

    def test_unchanged_import_binds_old_timestamp_without_model(self):
        stamp = self.legacy["sessions"][sid(1)]["summary"]["version"]
        self.out.mkdir()
        self.page().write_text("- [x] old %%ss:" + sid(1) + "@" + stamp + "%%\n", encoding="utf-8")
        self.enable()
        judge = FakeJudge()
        self.run_once(judge)
        self.assertFalse(judge.requests)
        state = store.load_state(self.state)
        summary = state["sessions"]["claude:" + sid(1)]["summary"]
        self.assertEqual(summary["last_ts"], self.stamp)
        self.assertEqual(state["legacy_marks"][sid(1) + "@" + stamp]["activity_version"], summary["activity_version"])
        self.assertIn("claude:" + sid(1) + "@" + summary["activity_version"], state["closures"])
        self.assertFalse((self.home / "threads" / "threads.json").exists())

    def test_conflict_retains_current_summary_and_original_is_queryable(self):
        self.enable()
        state = store.load_state(self.state)
        state["sessions"]["claude:" + sid(1)]["summary"]["first_human"] = "newer original source"
        store.save_state(self.state, state)
        result = migration.import_legacy(self.cfg, self.home, self.old, self.out)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(store.load_state(self.state)["sessions"]["claude:" + sid(1)]["summary"]["first_human"],
                         "newer original source")
        self.assertEqual(store.load_state(self.state)["legacy_imports"][result["legacy_import"]]["state"], self.legacy)

    def test_old_lock_never_stolen_even_if_ancient(self):
        lock = self.old.parent / "run.lock"
        lock.write_text("99999999", encoding="ascii")
        os.utime(lock, (1, 1))
        before = lock.read_bytes()
        for dry in (False, True):
            with self.assertRaisesRegex(RuntimeError, "legacy sweep is busy"):
                self.enable(dry_run=dry)
        self.assertEqual(before, lock.read_bytes())
        self.assertFalse(self.state.joinpath(store.STATE_FILE).exists())
        self.assertFalse(self.out.exists())

    def test_switch_dry_run_and_import_dry_run_have_no_business_writes(self):
        migration.import_legacy(self.cfg, self.home, self.old, self.out, dry_run=True)
        result = self.enable(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.state / store.STATE_FILE).exists())
        self.assertFalse((self.state / output.ACTIVATION_FILE).exists())
        self.assertFalse(self.out.exists())
        self.assertFalse((self.old.parent / "run.lock").exists())

    def test_low_level_writes_and_deletes_require_owner_even_dry_run(self):
        for dry in (True, False):
            with self.assertRaises(RuntimeError):
                store.write_page_if_changed(self.root / "bypass.md", "x", dry_run=dry)
            with self.assertRaises(RuntimeError):
                store.remove_generated(self.root / "bypass.md", lambda _: True, dry_run=dry)
        self.enable()
        migration.rollback(self.cfg, self.home, self.out)
        with output.OutputLease(self.out, self.state, switching=True):
            for dry in (True, False):
                with self.assertRaises(RuntimeError):
                    store.write_page_if_changed(self.page(), "bypass paused", dry_run=dry)

    def test_new_output_wrong_home_or_tampered_owner_fails_before_scan(self):
        self.enable()
        loader = lambda *a, **k: self.fail("must reject before scan")
        with self.assertRaises(RuntimeError):
            runtime.run_sweep(self.cfg, FakeJudge(), lambda: FakeGate(), self.root / "otherout",
                              self.state, history_loader=loader, dry_run=True)
        with self.assertRaises(RuntimeError):
            runtime.run_sweep(self.cfg, FakeJudge(), lambda: FakeGate(), self.out,
                              self.root / "otherhome" / "sweep", history_loader=loader)
        owner = self.out / output.OWNER_FILE
        owner.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_once(history_loader=loader)

    def test_output_lease_blocks_other_home_and_nested_or_model_run(self):
        self.enable()
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
        code = ("from pstate_sweep.adapters.output import OutputLease; import sys; "
                "OutputLease(sys.argv[1],sys.argv[2],switching=True).__enter__()")
        with output.OutputLease(self.out, self.state):
            proc = subprocess.run([sys.executable, "-B", "-c", code, str(self.out),
                                   str(self.root / "other" / "sweep")], env=env, capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("another sweep run is active", proc.stderr)

    def test_path_escape_and_archive_junction_are_rejected(self):
        self.enable()
        with output.OutputLease(self.out, self.state):
            with self.assertRaises(ValueError):
                store.write_page_if_changed(self.out / ".." / "escape.md", "x", dry_run=True)
        target = self.root / "external"
        target.mkdir()
        archive = self.out / self.cfg["archive_dir"]
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(archive), str(target)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            archive.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_once(no_judge=True)
        self.assertFalse(list(target.iterdir()))
        archive.rmdir() if os.name == "nt" else archive.unlink()

    def test_root_alias_uses_same_owner_and_same_cross_process_lock(self):
        self.enable()
        alias = self.root / "alias"
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.out)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            alias.symlink_to(self.out, target_is_directory=True)
        try:
            self.assertEqual(output.canonical(alias), output.canonical(self.out))
            runtime.run_sweep(self.cfg, FakeJudge(), lambda: FakeGate(), alias, self.state,
                              now=NOW, no_judge=True, threads_dir=self.home / "threads")
            env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
            code = "from pstate_sweep.adapters.output import OutputLease; import sys; OutputLease(*sys.argv[1:]).__enter__()"
            with output.OutputLease(self.out, self.state):
                proc = subprocess.run([sys.executable, "-B", "-c", code, str(alias), str(self.state)],
                                      env=env, capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("another sweep run is active", proc.stderr)
        finally:
            alias.rmdir() if os.name == "nt" else alias.unlink()

    def test_interrupted_switch_is_closed_until_forward_retry(self):
        with patch.object(store, "write_page_if_changed", side_effect=OSError("injected interruption")):
            with self.assertRaises(OSError):
                self.enable()
        self.assertEqual(output.activation(self.state)["phase"], "switching")
        with self.assertRaises(RuntimeError):
            self.run_once(no_judge=True)
        self.enable()
        self.assertEqual(output.activation(self.state)["phase"], "enabled")
        self.run_once(no_judge=True)
        self.assertIn("original title", self.page().read_text(encoding="utf-8"))

    def test_rollback_reconciles_latest_ticks_and_preserves_new_thread_decision(self):
        self.enable()
        self.run_once(no_judge=True)
        excerpt = "original words"
        source = {"platform": "claude", "session": sid(1), "anchor": "line:1",
                  "time": "2026-10-01T00:00:00Z", "role": "user", "excerpt": excerpt,
                  "excerpt_hash": hashlib.sha256(excerpt.encode()).hexdigest()}
        threads.mutate(self.home / "threads", "add", {"title": "new thread", "sources": [source]},
                       ident="after-switch", distinct=True)
        threads.mutate(self.home / "threads", "later", {"said": "original words", "source": source},
                       ident="after-switch", rev=1)
        before = threads.read(self.home / "threads")
        self.page().write_text(self.page().read_text(encoding="utf-8").replace("[ ]", "[x]"), encoding="utf-8")
        page_before = self.page().read_bytes()
        result = migration.rollback(self.cfg, self.home, self.out)
        self.assertFalse(result["legacy_writer_restored"])
        self.assertEqual(page_before, self.page().read_bytes())
        self.assertEqual(before, threads.read(self.home / "threads"))
        self.assertTrue(store.load_state(self.state)["closures"])
        with self.assertRaises(RuntimeError):
            self.run_once(no_judge=True)
        self.enable()
        self.assertEqual(before, threads.read(self.home / "threads"))
        self.run_once(no_judge=True)
        self.assertTrue(store.load_state(self.state)["closures"])

    def test_cli_closed_loop_and_machine_contract(self):
        config_path = self.root / "sweep.toml"
        config_path.write_text((ROOT / "sweep.toml").read_text(encoding="utf-8"), encoding="utf-8")
        inputs = self.root / "files.json"
        inputs.write_text(json.dumps({"files": {"claude": [str(self.path)]}}), encoding="utf-8")
        env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        def cli(*args, success=True):
            proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep",
                *args, "--config", str(config_path), "--json"], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode == 0, success, proc.stdout + proc.stderr)
            envelope = json.loads(proc.stdout if success else proc.stderr)
            self.assertEqual(set(envelope), {"ok", "data", "error", "meta"})
            if not success:
                self.assertEqual(proc.stdout, "")
            return envelope
        cli("run", "--out", str(self.out), "--no-judge", "--input-file", str(inputs), success=False)
        cli("import-legacy", "--legacy-state", str(self.old), "--out", str(self.out), "--dry-run")
        cli("import-legacy", "--legacy-state", str(self.old), "--out", str(self.out))
        cli("reconcile", "--legacy-state", str(self.old), "--out", str(self.out))
        cli("switch", "--legacy-state", str(self.old), "--out", str(self.out), "--dry-run")
        cli("switch", "--legacy-state", str(self.old), "--out", str(self.out))
        cli("run", "--out", str(self.out), "--no-judge", "--input-file", str(inputs), "--dry-run")
        cli("run", "--out", str(self.out), "--no-judge", "--input-file", str(inputs))
        all_sources = cli("sources")["data"]
        self.assertIn("claude:" + sid(1), all_sources["sessions"])
        self.assertTrue(all_sources["legacy_imports"])
        cli("rollback", "--out", str(self.out), "--dry-run")
        cli("rollback", "--out", str(self.out))
        cli("run", "--out", str(self.out), "--no-judge", "--input-file", str(inputs), success=False)
        cli("switch", "--out", str(self.out))
        self.assertEqual(cli("status")["data"]["activation"]["phase"], "enabled")
    def test_missing_original_without_activity_hash_keeps_old_closed_checked(self):
        self.path.unlink()
        entry = self.legacy["sessions"][sid(1)]
        entry["summary"]["last_ts"] = 0
        stamp = entry["summary"]["version"]
        self.legacy["closures"][sid(1) + "@" + stamp] = {"at": 123}
        self.write_legacy()
        self.out.mkdir()
        closed = self.out / self.cfg["closed_name"]
        closed.write_text("- [x] old %%ss:" + sid(1) + "@" + stamp + "%%\n", encoding="utf-8")
        self.enable()
        archives = list((self.out / self.cfg["archive_dir"]).glob("*.md"))
        self.assertEqual(len(archives), 1)
        text = archives[0].read_text(encoding="utf-8")
        self.assertIn("- [x] **original title**", text)
        state = store.load_state(self.state)
        self.assertNotIn("activity_version", state["sessions"]["claude:" + sid(1)]["summary"])
        self.assertFalse(state["legacy_marks"])

    def test_untick_then_reimport_old_native_alias_never_recloses(self):
        stamp = self.legacy["sessions"][sid(1)]["summary"]["version"]
        self.legacy["closures"][sid(1) + "@" + stamp] = {"at": 123}
        self.write_legacy()
        self.enable()
        self.run_once(no_judge=True)
        archive = list((self.out / self.cfg["archive_dir"]).glob("*.md"))
        closed = self.out / self.cfg["closed_name"]
        candidates = [closed] + archive
        for path in candidates:
            path.write_text(path.read_text(encoding="utf-8").replace("[x]", "[ ]"), encoding="utf-8")
        self.run_once(no_judge=True)
        self.assertFalse(store.load_state(self.state)["closures"])
        migration.import_legacy(self.cfg, self.home, self.old, self.out)
        self.assertFalse(store.load_state(self.state)["closures"])

    def test_model_holds_output_lease_second_process_cannot_scan_or_judge(self):
        self.enable()
        write_transcript(self.cfg["projects_dir"], sid(1), [human("changed source", self.stamp + DAY, cwd=str(self.root))])
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
        code = ("from pstate_sweep.adapters.output import OutputLease; import sys; "
                "OutputLease(sys.argv[1], sys.argv[2], switching=True).__enter__()")
        outer = self
        class RacingJudge(FakeJudge):
            def judge(self, request):
                proc = subprocess.run([sys.executable, "-B", "-c", code, str(outer.out),
                                       str(outer.root / "racinghome" / "sweep")],
                                      env=env, capture_output=True, text=True)
                outer.assertNotEqual(proc.returncode, 0)
                outer.assertIn("another sweep run is active", proc.stderr)
                return super().judge(request)
        judge = RacingJudge()
        self.run_once(judge)
        self.assertEqual(len(judge.requests), 1)

    def test_preexisting_tmp_link_cannot_redirect_atomic_page_write(self):
        self.enable()
        external = self.root / "external.txt"
        external.write_text("unchanged", encoding="utf-8")
        old_tmp = self.page().with_name(self.page().name + ".tmp")
        try:
            old_tmp.symlink_to(external)
        except OSError:
            old_tmp.write_text("preexisting temporary sentinel", encoding="utf-8")
        before = old_tmp.read_bytes()
        try:
            with output.OutputLease(self.out, self.state):
                store.write_page_if_changed(self.page(), "new page")
            self.assertEqual(external.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(old_tmp.read_bytes(), before)
        finally:
            old_tmp.unlink()

    def test_interruption_before_owner_record_is_fail_closed_and_resumable(self):
        original = store.write_atomic
        def interrupt_owner(path, text):
            if Path(path).name == output.OWNER_FILE:
                raise OSError("interrupted before owner")
            return original(path, text)
        with patch.object(store, "write_atomic", side_effect=interrupt_owner):
            with self.assertRaises(OSError):
                self.enable()
        self.assertEqual(output.activation(self.state)["phase"], "switching")
        self.assertFalse((self.out / output.OWNER_FILE).exists())
        with self.assertRaises(RuntimeError):
            self.run_once(no_judge=True)
        self.enable()
        self.run_once(no_judge=True)
