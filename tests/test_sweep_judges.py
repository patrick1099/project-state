"""Backend switching and structured Codex calls without real paid model calls."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pstate_sweep import config, core
from pstate_sweep.adapters import judges, codex_judge
from pstate_sweep.ports import JudgeError
from sweep_helpers import human, assistant, write_transcript, sid, NOW


class JudgeBackendsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = config.load(ROOT / "sweep.toml")
        self.cfg.update(codex_cmd="fixture-codex", codex_model="fixture-model", codex_effort="high")

    def tearDown(self):
        self.temp.cleanup()

    def test_registry_selects_one_backend_without_constructing_other(self):
        self.assertEqual(set(judges.REGISTRY), {"claude", "codex"})
        selected = judges.create(self.cfg, self.root)
        self.assertIsInstance(selected, codex_judge.CodexJudge)
        self.cfg["backend"] = "claude"
        self.assertEqual(type(judges.create(self.cfg, self.root)).__name__, "ClaudeJudge")

    def test_codex_command_is_isolated_and_configuration_drives_selection(self):
        argv = codex_judge.build_command(self.cfg, self.root / "schema.json")
        for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral", "--output-schema"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--model") + 1], "fixture-model")
        self.assertEqual(argv[-1], "-")
        self.assertIn('approval_policy="never"', argv)
        self.assertNotIn("--resume", argv)

    def test_structured_message_and_errors(self):
        answer = {"status": "悬置", "title": "fixture", "stopped_at": "next", "owner": "个人"}
        line = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}})
        self.assertEqual(codex_judge.parse_output(line), answer)
        for text in ("not json", '{}', '{"type":"turn.failed","error":{"message":"failed"}}'):
            with self.assertRaises(JudgeError):
                codex_judge.parse_output(text)

    def test_resume_classifies_full_native_text_even_when_original_cwd_is_gone(self):
        path = write_transcript(self.root, sid(42), [human("original objective", NOW - 10), assistant("latest result", NOW)])
        judge = codex_judge.CodexJudge(self.cfg, self.root / "scratch")
        request = core.make_request(sid(42), {"cwd": str(self.root / "gone")}, "resume")
        request.update(platform="claude", paths=[str(path)])
        prompt = judge.prompt(request)
        self.assertIn("original objective", prompt)
        self.assertIn("latest result", prompt)
        self.assertFalse(judge.requires_original_cwd)
        self.cfg["max_input_chars"] = 1
        with self.assertRaisesRegex(JudgeError, "no silent truncation"):
            judge.prompt(request)

    def test_subprocess_is_utf8_stdin_schema_and_nonzero_never_looks_successful(self):
        judge = codex_judge.CodexJudge(self.cfg, self.root / "scratch")
        request = core.make_request("fixture", {}, "owner")
        answer = {"owner": "个人"}
        stdout = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}})
        with patch.object(codex_judge.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr="")) as run:
            self.assertEqual(judge.judge(request), answer)
            self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
            self.assertIn("只读", run.call_args.kwargs["input"])
        with patch.object(codex_judge.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=stdout, stderr="auth expired")):
            with self.assertRaisesRegex(JudgeError, "auth expired"):
                judge.judge(request)

    def test_unknown_backend_is_clear_configuration_failure(self):
        path = self.root / "config.toml"
        path.write_text((ROOT / "sweep.toml").read_text(encoding="utf-8").replace('backend = "codex"', 'backend = "unknown"'), encoding="utf-8")
        with self.assertRaisesRegex(config.ConfigError, "unknown judge.backend"):
            config.load(path)
