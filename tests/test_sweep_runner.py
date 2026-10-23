"""Stable runner and scheduler probes never register tasks or invoke a real model."""
import importlib.machinery
import importlib.util
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
from pstate_sweep import cli, config
from pstate_sweep.adapters import schtask


class HiddenRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")

    def test_actual_plaintext_runner_preserves_child_success_disabled_and_usage_exitcodes(self):
        for args, expected in ((["status", "--json"], 0),
                               (["run", "--no-judge", "--json"], 1),
                               (["status", "--unknown", "--json"], 2)):
            direct = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep", *args],
                                    env=self.env, capture_output=True, text=True, encoding="utf-8")
            hidden = subprocess.run([sys.executable, "-B", str(ROOT / "run_hidden.pyw"), *args],
                                    env=self.env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(hidden.returncode, expected, hidden.stdout + hidden.stderr)
            self.assertEqual(hidden.returncode, direct.returncode)
            self.assertEqual(hidden.stdout, direct.stdout)
            self.assertEqual(hidden.stderr, direct.stderr)
        self.assertTrue((self.home / "sweep/last_launch.log").is_file())
        self.assertFalse((self.home / "threads/threads.json").exists())
        self.assertFalse((self.home / "sweep/sources.json").exists())
        self.assertFalse((self.home / "sweep/activation.json").exists())

    def test_actual_pythonw_without_console_keeps_diagnostics_and_child_exit(self):
        pythonw = schtask.default_pythonw()
        for args, expected in ((["status", "--json"], 0), (["run", "--no-judge", "--json"], 1),
                               (["status", "--unknown", "--json"], 2)):
            process = subprocess.run([pythonw, str(ROOT / "run_hidden.pyw"), *args], env=self.env,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=0x08000000, timeout=30)
            self.assertEqual(process.returncode, expected)
            text = (self.home / "sweep/last_launch.log").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("exit " + str(expected) + "\n"))
            self.assertIn('"ok": true' if expected == 0 else '"ok": false', text)
        self.assertFalse((self.home / "sweep/sources.json").exists())
        self.assertFalse((self.home / "threads/threads.json").exists())

    def test_runner_fixed_absolute_python_no_window_and_exit_propagation(self):
        loader = importlib.machinery.SourceFileLoader("isolated_hidden", str(ROOT / "run_hidden.pyw"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        fake = subprocess.CompletedProcess([], 17, "", "")
        with patch.object(module.subprocess, "run", return_value=fake) as child:
            self.assertEqual(module.main(["status", "--json"]), 17)
        argv = child.call_args.args[0]
        self.assertEqual(Path(argv[0]), Path("C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe"))
        self.assertEqual(argv[-3:], ["sweep", "status", "--json"])
        self.assertEqual(child.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(child.call_args.kwargs["env"]["PSTATE_SWEEP_HIDDEN_LAUNCH"], "1")

    def test_worktree_and_version_cache_registration_refused_even_in_dryrun(self):
        with self.assertRaisesRegex(ValueError, "worktree"):
            schtask.install_script("isolated", 9, ROOT / "run_hidden.pyw", schtask.default_pythonw())
        cached = self.root / ".codex/plugins/cache/project-state/0.4.0"
        (cached / "scripts").mkdir(parents=True)
        (cached / "scripts/pstate.py").touch()
        (cached / "run_hidden.pyw").touch()
        with self.assertRaisesRegex(ValueError, "cache"):
            schtask.install_script("isolated", 9, cached / "run_hidden.pyw", schtask.default_pythonw())
        with patch.object(schtask, "run_powershell") as child:
            result = cli.main(["install-task", "--dry-run", "--json"], self.home)
        self.assertEqual(result, 2)
        child.assert_not_called()
        self.assertFalse(self.home.exists())

    def test_stable_script_and_config_driven_schedule_no_registration(self):
        stable = self.root / "stable"
        (stable / "scripts").mkdir(parents=True)
        (stable / "scripts/pstate.py").touch()
        launcher = stable / "run_hidden.pyw"
        launcher.touch()
        cfg_path = self.root / "schedule.toml"
        text = (ROOT / "sweep.toml").read_text(encoding="utf-8")
        text = text.replace('task_name = "session-sweep"', 'task_name = "isolated-review-task"')
        text = text.replace("interval_hours = 4", "interval_hours = 9")
        cfg_path.write_text(text, encoding="utf-8")
        cfg = config.load(cfg_path)
        script = schtask.install_script(cfg["task_name"], cfg["interval_hours"], launcher, schtask.default_pythonw())
        self.assertIn("isolated-review-task", script)
        self.assertIn("New-TimeSpan -Hours 9", script)
        self.assertIn("MultipleInstances IgnoreNew", script)
        self.assertIn(str(launcher), script)
        self.assertIn("Python314/pythonw.exe", script)
        with self.assertRaises(ValueError):
            schtask.install_script("isolated", 9, launcher, "C:/other/pythonw.exe")
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep",
                               "uninstall-task", "--config", str(cfg_path), "--dry-run", "--json"],
                              env=self.env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)["data"]
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["task_name"], "isolated-review-task")
        self.assertIn("Unregister-ScheduledTask", result["script"])
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
