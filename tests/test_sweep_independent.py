"""Independent merge acceptance: malformed durable inputs and configuration boundaries."""
import contextlib
import copy
import io
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
from pstate_sweep import cli, config, core
from pstate_sweep.adapters import claude_judge, store
from pstate_history import PLATFORMS
from pstate_sweep import runtime


class IndependentAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.out = self.root / "out"
        self.env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.files = self.root / "files.json"
        self.files.write_text(json.dumps({"files": {}, "roots": {}}), encoding="utf-8")

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/pstate.py"),
                               "sweep", *args, "--json"], env=self.env,
                              capture_output=True, text=True, encoding="utf-8")

    def config_file(self, changes):
        text = (ROOT / "sweep.toml").read_text(encoding="utf-8")
        for old, new in changes:
            self.assertIn(old, text)
            text = text.replace(old, new)
        path = self.root / "isolated.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_malformed_permanent_summary_fails_without_overwriting_state(self):
        self.assertEqual(self.invoke("switch", "--out", str(self.out)).returncode, 0)
        path = self.home / "sweep" / store.STATE_FILE
        valid = {"entrypoint": "cli", "human_count": 1, "last_ts": 1,
                 "version": "original", "first_human": "original", "last_human": "original",
                 "last_assistant": "", "written": [], "cwd": "", "recap": None}
        cases = [{}, dict(valid, last_ts="broken-time"), dict(valid, human_count="broken-count")]
        for summary in cases:
            with self.subTest(summary=summary):
                before = json.dumps({"version": 1, "sessions": {"claude:damaged": {"summary": summary}},
                                     "closures": {}, "thread_closures": {}, "legacy_marks": {}}).encode()
                path.write_bytes(before)
                result = self.invoke("run", "--out", str(self.out), "--no-judge",
                                     "--input-file", str(self.files))
                self.assertEqual(path.read_bytes(), before, "invalid permanent material was rewritten")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(json.loads(result.stderr)["error"]["code"], "E_IO")

    def test_blank_required_model_or_command_fails_before_external_or_business_write(self):
        cases = [("recap_model", "haiku"), ("resume_model", "sonnet"), ("claude_cmd", "claude")]
        for key, value in cases:
            for blank in ("", "   "):
                with self.subTest(key=key, blank=blank):
                    path = self.config_file([(key + ' = "' + value + '"', key + ' = "' + blank + '"')])
                    with patch.object(runtime, "require_enabled"), patch.object(runtime, "run_sweep") as run:
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            result = cli.main(["run", "--config", str(path), "--input-file", str(self.files),
                                               "--out", str(self.out), "--json"], self.home)
                    run.assert_not_called()
                    self.assertEqual(result, 2)
                    self.assertFalse(self.home.exists())
                    self.assertFalse(self.out.exists())

    def test_nonfinite_required_numeric_config_fails_at_load(self):
        for key, old in (("timeout_s", "300"), ("budget_usd_per_call", "2.0"),
                         ("interval_hours", "4"), ("idle_days", "2"), ("window_days", "7")):
            for invalid in ("nan", "inf"):
                with self.subTest(key=key, invalid=invalid):
                    path = self.config_file([(key + " = " + old, key + " = " + invalid)])
                    with self.assertRaises(config.ConfigError):
                        config.load(path)

    def test_real_config_model_budget_timeout_reaches_final_fake_subprocess(self):
        path = self.config_file([('recap_model = "haiku"', 'recap_model = "independent-model"'),
                                 ("budget_usd_per_call = 2.0", "budget_usd_per_call = 0.37"),
                                 ("timeout_s = 300", "timeout_s = 17")])
        cfg = config.load(path)
        judge = claude_judge.ClaudeJudge(cfg, str(self.root / "scratch"))
        fake = subprocess.CompletedProcess([], 0, json.dumps({"structured_output":
            {"status": "悬置", "title": "isolated", "stopped_at": "later", "owner": "个人"}}), "")
        with patch.object(claude_judge.subprocess, "run", return_value=fake) as external:
            judge.judge(core.make_request("fixture", {"cwd": str(self.root)}, "recap"))
        argv = external.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "independent-model")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "0.37")
        self.assertEqual(external.call_args.kwargs["timeout"], 17)
        self.assertIn("--safe-mode", argv)

    def test_backend_capability_toggle_follows_registry_without_caller_edits(self):
        cfg = {"history_roots": {"fake-platform": ["fake-root"]}}
        with patch.dict(PLATFORMS, {"fake-platform": {"background": True}}):
            self.assertEqual(runtime.history_options(cfg)["roots"]["fake-platform"], ["fake-root"])
            PLATFORMS["fake-platform"]["background"] = False
            self.assertEqual(runtime.history_options(cfg)["roots"]["fake-platform"], [])


if __name__ == "__main__":
    unittest.main()
