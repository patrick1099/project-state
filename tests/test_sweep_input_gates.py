"""Regression for durable-input rejection and old-summary compatibility."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pstate_sweep import config, migration
from pstate_sweep.adapters import store
from sweep_helpers import make_cfg


class InputGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"

    def material(self, summary):
        return {"version": 1, "sessions": {"claude:old": {"summary": summary}},
                "unknown_top_level": {"retain": [1, "old"]}}

    def test_nullable_old_fields_and_unknown_material_round_trip(self):
        summary = {"version": None, "last_ts": None, "cwd": None, "branch": None, "entrypoint": None,
                   "human_count": 0, "first_human": "old words", "recap": None,
                   "unknown_summary": {"retain": ["nested", 1]}}
        original = self.material(summary)
        store.save_state(self.state, copy.deepcopy(original))
        loaded = store.load_state(self.state)
        for key in original:
            self.assertEqual(loaded[key], original[key])
        self.assertNotIn("activity_version", loaded["sessions"]["claude:old"]["summary"])

    def test_minimal_old_import_without_source_or_activity_hash_preserves_optional_absence(self):
        old = self.root / "old" / "state.json"
        old.parent.mkdir()
        summary = {"version": None, "first_human": "permanent original summary",
                   "unknown_summary": {"retain": True}}
        original = self.material(summary)
        old.write_text(json.dumps(original), encoding="utf-8")
        home, out = self.root / "home", self.root / "out"
        report = migration.import_legacy(make_cfg(self.root), home, old, out)
        state = store.load_state(home / "sweep")
        self.assertEqual(state["legacy_imports"][report["legacy_import"]]["state"], original)
        stored = state["sessions"]["claude:old"]["summary"]
        self.assertEqual(stored["version"], None)
        self.assertEqual(stored["unknown_summary"], summary["unknown_summary"])
        for field in ("activity_version", "review_version", "human_count", "recap", "last_ts"):
            self.assertNotIn(field, stored)
        self.assertFalse((home / "threads" / "threads.json").exists())

    def test_bad_known_summary_fields_fail_before_actual_and_dry_save(self):
        store.save_state(self.state, self.material({"version": "old"}))
        path = self.state / store.STATE_FILE
        before = path.read_bytes()
        malformed = [{}, {"version": 1}, {"version": "old", "human_count": True},
                     {"version": "old", "human_count": -1}, {"version": "old", "last_ts": "1"},
                     {"version": "old", "last_ts": True}, {"version": "old", "last_ts": float("nan")},
                     {"version": "old", "last_ts": float("inf")},
                     {"version": "old", "recap": {"ts": float("inf"), "text": "old"}},
                     {"version": "old", "recap": {"ts": 1, "text": []}},
                     {"version": "old", "written": "path"}, {"version": "old", "written": [1]},
                     {"version": "old", "interactive": "true"}, {"version": "old", "cwd": []},
                     {"version": "old", "first_human": None}]
        for summary in malformed:
            for dry_run in (False, True):
                with self.subTest(summary=summary, dry_run=dry_run):
                    with patch.object(store, "write_atomic") as write:
                        with self.assertRaises(store.SourceStateError):
                            store.save_state(self.state, self.material(summary), dry_run=dry_run)
                        write.assert_not_called()
                    self.assertEqual(path.read_bytes(), before)

    def test_config_preserves_optional_empty_and_existing_zero_positive_rules(self):
        original = (ROOT / "sweep.toml").read_text(encoding="utf-8")
        path = self.root / "config.toml"
        path.write_text(original.replace("idle_days = 2", "idle_days = 0").replace("max_calls = 20", "max_calls = 0"),
                        encoding="utf-8")
        cfg = config.load(path)
        self.assertEqual((cfg["idle_days"], cfg["max_calls"], cfg["doc_gate"]), (0, 0, ""))
        for key, old in (("timeout_s", "300"), ("budget_usd_per_call", "2.0"),
                         ("interval_hours", "4"), ("idle_days", "2"), ("window_days", "7")):
            with self.subTest(key=key, invalid="-inf"):
                path.write_text(original.replace(key + " = " + old, key + " = -inf"), encoding="utf-8")
                with self.assertRaises(config.ConfigError):
                    config.load(path)
        for key, old in (("timeout_s", "300"), ("budget_usd_per_call", "2.0"),
                         ("interval_hours", "4"), ("window_days", "7")):
            with self.subTest(key=key, invalid="0"):
                path.write_text(original.replace(key + " = " + old, key + " = 0"), encoding="utf-8")
                with self.assertRaises(config.ConfigError):
                    config.load(path)
