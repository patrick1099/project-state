"""Actual CLI probes remain isolated from official data, pages and model calls."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pstate_sweep import config, core
from pstate_sweep.adapters import claude_judge, store


class SweepCLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep", *args],
                              env=self.env, capture_output=True, text=True, encoding="utf-8")

    def test_default_run_is_disabled_before_any_scan_model_or_output(self):
        proc = self.invoke("run", "--no-judge", "--out", str(self.root / "out"), "--json")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        result = json.loads(proc.stderr)
        self.assertEqual(set(result), {"ok", "data", "error", "meta"})
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result["error"]["message"])
        self.assertFalse(self.home.exists())
        self.assertFalse((self.root / "out").exists())

    def test_status_format_alias_and_eager_help(self):
        first = self.invoke("status", "--json")
        second = self.invoke("status", "--format", "json")
        self.assertEqual((first.returncode, second.returncode), (0, 0))
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        self.assertEqual(json.loads(first.stdout)["data"]["state_dir"], str(self.home / "sweep"))
        self.assertFalse(self.home.exists())
        help_result = self.invoke("--ai-help", "--invalid")
        self.assertEqual(help_result.returncode, 0)
        for heading in ("Quick Reference", "Side Effects & Safety", "Exit Codes", "Errors & Recovery"):
            self.assertIn(heading, help_result.stdout)

    def test_usage_errors_are_json_and_unknown_schema_is_io_failure(self):
        invalid = self.invoke("--json", "bad-command")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "E_VALIDATION")
        directory = self.home / "sweep"
        directory.mkdir(parents=True)
        path = directory / store.STATE_FILE
        path.write_text('{"version": 99, "sessions": {}}', encoding="utf-8")
        before = path.read_bytes()
        invalid = self.invoke("status", "--json")
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "E_IO")
        self.assertEqual(path.read_bytes(), before)

    def test_model_choice_follows_real_config_load_and_bad_config_stops_entry(self):
        path = self.root / "config.toml"
        text = (ROOT / "sweep.toml").read_text(encoding="utf-8")
        path.write_text(text.replace('recap_model = "haiku"', 'recap_model = "fixture-model"'), encoding="utf-8")
        cfg = config.load(path)
        argv, _ = claude_judge.build_command(cfg, core.make_request("fixture", {"cwd": str(self.root)}, "recap"))
        self.assertEqual(argv[argv.index("--model") + 1], "fixture-model")
        path.write_text(text.replace('recap_model = "haiku"', ''), encoding="utf-8")
        invalid = self.invoke("run", "--config", str(path), "--json")
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stderr)["error"]["code"], "E_VALIDATION")
        self.assertFalse(self.home.exists())
