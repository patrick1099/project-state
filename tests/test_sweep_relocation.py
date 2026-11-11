"""Portable ownership acceptance, including interrupted physical directory moves."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pstate_sweep import migration, relocation, runtime
from pstate_sweep.adapters import output, store
from sweep_helpers import FakeGate, FakeJudge, NOW, make_cfg


class RelocationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.state = self.home / "sweep"
        self.old = self.root / "old-output"
        self.new = self.root / "new-output"
        self.cfg = make_cfg(self.root)
        migration.switch(self.cfg, self.home, self.old)

    def tearDown(self):
        self.temp.cleanup()

    def move(self):
        self.old.rename(self.new)

    def raw(self, name):
        return output.read_json(self.state / name)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p.name != "run.lock"}

    def move_registration(self, **kwargs):
        return relocation.relocate(self.home, self.new, self.old, **kwargs)

    def test_new_switch_is_relative_and_external_root_is_local_only(self):
        for data in (self.raw(output.ACTIVATION_FILE), output.read_json(self.old / output.OWNER_FILE)):
            self.assertEqual(data["version"], 2)
            self.assertEqual((data["state"], data["out"]), (".", "."))
        self.assertEqual(output.local_output(self.state), output.canonical(self.old))
        output.validate_owner(self.state, self.old)

    def test_move_is_closed_until_explicit_relocate_dryrun_and_idempotent_real(self):
        self.move()
        with self.assertRaises(RuntimeError):
            output.validate_owner(self.state, self.new)
        before = self.snapshot()
        result = self.move_registration(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(before, self.snapshot())
        body = {p.name: p.read_bytes() for p in self.new.rglob("*.md")}
        self.move_registration()
        output.validate_owner(self.state, self.new)
        self.assertEqual(body, {p.name: p.read_bytes() for p in self.new.rglob("*.md")})
        self.move_registration()
        self.assertEqual(len(list((self.state / "relocations").glob("*.json"))), 1)
        self.assertTrue(self.raw(output.RELOCATION_FILE)["complete"])

    def test_v1_upgrade_preserves_phase_and_does_not_modify_sources_threads_or_pages(self):
        token = self.raw(output.ACTIVATION_FILE)["token"]
        active = {"version": 1, "state": output.canonical(self.state), "out": output.canonical(self.old), "token": token, "phase": "paused"}
        (self.state / output.ACTIVATION_FILE).write_text(json.dumps(active), encoding="utf-8")
        (self.old / output.OWNER_FILE).write_text(json.dumps({k: active[k] for k in ("version", "state", "out", "token")}), encoding="utf-8")
        self.move()
        self.move_registration()
        self.assertEqual(self.raw(output.ACTIVATION_FILE)["phase"], "paused")
        with self.assertRaises(RuntimeError):
            output.validate_owner(self.state, self.new)
        output.validate_owner(self.state, self.new, enabled=False)
        journal = self.raw(output.RELOCATION_FILE)
        self.assertEqual(journal["plan"]["before"]["active"], active)

    def test_existing_original_foreign_token_wrong_old_path_and_malformed_are_rejected(self):
        shutil.copytree(self.old, self.new)
        with self.assertRaisesRegex(RuntimeError, "still exists"):
            self.move_registration(dry_run=True)
        shutil.rmtree(self.new)
        self.move()
        with self.assertRaisesRegex(RuntimeError, "old location"):
            relocation.relocate(self.home, self.new, self.root / "wrong")
        owner = output.read_json(self.new / output.OWNER_FILE)
        owner["token"] = "foreign"
        (self.new / output.OWNER_FILE).write_text(json.dumps(owner), encoding="utf-8")
        for dry in (True, False):
            with self.assertRaisesRegex(RuntimeError, "matching"):
                self.move_registration(dry_run=dry)
        (self.new / output.OWNER_FILE).write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.move_registration()

    def test_all_registration_write_interruptions_recover_with_same_parameters(self):
        self.move()
        before = self.snapshot()
        original = store.write_atomic
        for stop in range(1, 9):
            # Each case gets a distinct isolated root and its own matching registration.
            case = self.root / ("case" + str(stop))
            home, old, new = case / "home", case / "old", case / "new"
            cfg = make_cfg(case)
            migration.switch(cfg, home, old)
            old.rename(new)
            count = 0
            def fail(path, text):
                nonlocal count
                count += 1
                if count == stop:
                    raise OSError("interrupted")
                return original(path, text)
            with patch.object(store, "write_atomic", side_effect=fail):
                with self.assertRaises(OSError):
                    relocation.relocate(home, new, old)
            active = output.activation(home / "sweep")
            if active["phase"] == "relocating":
                with self.assertRaises(RuntimeError):
                    output.validate_owner(home / "sweep", new)
            relocation.relocate(home, new, old)
            output.validate_owner(home / "sweep", new)

    def test_real_cli_contract_and_default_mapping(self):
        self.move()
        env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        base = [sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep", "relocate", "--from-out", str(self.old), "--out", str(self.new)]
        for options in (["--dry-run", "--json"], ["--format", "json"]):
            proc = subprocess.run(base + options, env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
        bad = subprocess.run(base[:5] + ["--json"], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        self.assertEqual(json.loads(bad.stderr)["error"]["code"], "E_VALIDATION")
    def test_historical_v1_path_is_lexical_while_alias_lock_is_physical(self):
        alias = self.root / "legacy-alias"
        if os.name == "nt":
            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.old)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            alias.symlink_to(self.old, target_is_directory=True)
        try:
            token = self.raw(output.ACTIVATION_FILE)["token"]
            active = {"version": 1, "state": output.canonical(self.state), "out": os.path.normcase(os.path.abspath(alias)), "token": token, "phase": "enabled"}
            (self.state / output.ACTIVATION_FILE).write_text(json.dumps(active), encoding="utf-8")
            (self.old / output.OWNER_FILE).write_text(json.dumps({k: active[k] for k in ("version", "state", "out", "token")}), encoding="utf-8")
            relocation.relocate(self.home, self.old, alias, dry_run=True)
            relocation.relocate(self.home, self.old, alias)
            output.validate_owner(self.state, self.old)
        finally:
            alias.rmdir() if os.name == "nt" else alias.unlink()

