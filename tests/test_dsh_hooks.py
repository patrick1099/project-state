import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pstate
import pstate_dsh as D
import pstate_hooks as H  # noqa: F401  (shared decision module is exercised through D)
import pstate_lock as L
import pstate_tools as T
from test_pstate import FakeProc

ROOT = Path(__file__).resolve().parents[1]


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


class FakeStdin:
    def __init__(self, raw):
        self.buffer = io.BytesIO(raw)


class PayloadTranslationTests(unittest.TestCase):
    def test_native_tool_names_and_events_are_translated(self):
        payload = {"hook_event_name": "tools/pre-execute", "session_id": "A", "tool_use_id": "c1",
                   "cwd": r"C:\repo", "tool_name": "write",
                   "tool_input": {"file_path": r"C:\repo\a.txt", "content": "x"},
                   "dsh": {"session_log": r"C:\logs\s.jsonl", "turn": 3, "step": 1}}
        data = D.normalize(payload)
        self.assertEqual(data["tool_name"], "Write")
        self.assertEqual(data["tool_input"]["file_path"], r"C:\repo\a.txt")
        self.assertEqual(data["transcript_path"], r"C:\logs\s.jsonl")
        self.assertEqual(data["turn_id"], "3")
        self.assertEqual(data["hook_event_name"], "PreToolUse")
        self.assertEqual(data["native_event_name"], "tools/pre-execute")
        self.assertEqual(data["host"], "dsh")

    def test_event_names_cover_the_dsh_contract(self):
        for native, claude in D.EVENT_NAMES.items():
            self.assertEqual(D.normalize({"hook_event_name": native, "session_id": "A"})["hook_event_name"], claude)

    def test_relative_shell_workdir_is_anchored_to_payload_cwd(self):
        data = D.normalize({"cwd": r"C:\repo", "tool_name": "pwsh", "hook_event_name": "tools/pre-execute",
                            "tool_input": {"command": "x", "workdir": "sub"}})
        self.assertEqual(data["tool_name"], "Bash")
        self.assertTrue(os.path.isabs(data["tool_input"]["workdir"]))
        self.assertEqual(os.path.normcase(data["tool_input"]["workdir"]), os.path.normcase(r"C:\repo\sub"))

    def test_unknown_tool_stays_unknown_and_is_read_only(self):
        data = D.normalize({"tool_name": "read", "cwd": os.getcwd(), "tool_input": {"path": "x"}})
        self.assertEqual(data["tool_name"], "read")
        self.assertTrue(T.analyze(data, host="dsh").readonly)

    def test_lowercase_write_without_file_path_fails_closed_in_shared_analysis(self):
        with self.assertRaises(ValueError):
            T.analyze(D.normalize({"tool_name": "write", "cwd": os.getcwd(), "tool_input": {}}), host="dsh")


class HostIdentityTests(unittest.TestCase):
    def test_dsh_host_process_registration(self):
        self.assertEqual(L.HOST_PROCESSES["dsh"], ("deepseek harness.exe",))
        self.assertNotIn("node.exe", L.HOST_PROCESSES["dsh"])

    def test_identify_walks_up_to_the_deepseek_harness_host(self):
        proc = FakeProc()
        proc.chain = [(100, 1, "DeepSeek Harness.exe"), (90, 0, "DeepSeek Harness.exe"), (9, 0, "explorer.exe")]
        me, chain = L.identify(proc, "A", host="dsh")
        self.assertEqual(me["host"], "dsh")
        self.assertEqual(me["sid"], "dsh:A")
        self.assertEqual(me["pid"], 100)
        self.assertEqual(chain, [])
        self.assertIsNone(L.identify(proc, "A", host="codex")[0])

    def test_session_key_prefix_is_idempotent_and_short_spells_without_prefix(self):
        self.assertEqual(L.session_key("dsh", "A"), "dsh:A")
        self.assertEqual(L.session_key("dsh", "dsh:A"), "dsh:A")
        self.assertEqual(L.short("dsh:A"), "A")
        self.assertEqual(L.session_key("claude", "A"), "A")

    def test_unknown_host_is_still_rejected(self):
        with self.assertRaises(ValueError):
            L.identify(FakeProc(), "A", host="other")

    def test_host_session_prefers_primary_host_over_inherited_dsh(self):
        self.assertEqual(L.host_session({"DSH_SESSION_ID": "A"}), ("dsh", "A"))
        self.assertEqual(L.host_session({"CODEX_THREAD_ID": "B", "DSH_SESSION_ID": "A"}), ("codex", "B"))
        self.assertEqual(L.host_session({"CLAUDE_CODE_SESSION_ID": "C", "DSH_SESSION_ID": "A"}), ("claude", "C"))
        self.assertEqual(L.host_session({"CODEX_THREAD_ID": "B", "CLAUDE_CODE_SESSION_ID": "C",
                                         "DSH_SESSION_ID": "A"}), (None, None))
        self.assertEqual(L.host_session({}), (None, None))

    def test_link_identity_accepts_dsh_and_keeps_dual_primary_ambiguous(self):
        import pstate_sessions as S
        self.assertEqual(S.current_identity(environ={"DSH_SESSION_ID": "A"})["platform"], "dsh")
        with self.assertRaises(S.LinkError):
            S.current_identity(environ={"CLAUDE_CODE_SESSION_ID": "C", "CODEX_THREAD_ID": "B",
                                        "DSH_SESSION_ID": "A"})

    def test_current_host_session_ignores_inherited_dsh_when_codex_is_present(self):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "B", "CLAUDE_CODE_SESSION_ID": "",
                                          "DSH_SESSION_ID": "A"}):
            self.assertEqual(pstate.current_host_session(), ("codex", "B"))
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "", "CLAUDE_CODE_SESSION_ID": "",
                                          "DSH_SESSION_ID": "A"}):
            self.assertEqual(pstate.current_host_session(), ("dsh", "A"))


class DshHookIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pstate-dsh-tests-")
        cls.base = Path(cls.tmp.name)
        cls.repo = cls.base / "repo"
        (cls.repo / "docs").mkdir(parents=True)
        (cls.repo / "docs" / "BLUEPRINT.md").write_text("# fixture\n", encoding="utf-8")
        for name in ("a.txt", "b.txt"):
            (cls.repo / name).write_text("0\n", encoding="utf-8")
        git(cls.repo, "init", "-q", "-b", "main")
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-qm", "fixture")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.state = self.base / ("state-" + self._testMethodName)
        self.proc = FakeProc()
        self.proc.chain = [(100, 1, "DeepSeek Harness.exe"), (9, 0, "explorer.exe")]
        self.patch_backend = mock.patch.object(L, "process_backend", lambda: self.proc)
        self.patch_env = mock.patch.dict(os.environ, {"PSTATE_HOME": str(self.state), "DSH_SESSION_ID": "",
                                                      "CLAUDE_CODE_SESSION_ID": "", "CODEX_THREAD_ID": ""})
        self.patch_backend.start()
        self.patch_env.start()
        for name in ("a.txt", "b.txt"):
            (self.repo / name).write_text("0\n", encoding="utf-8")

    def tearDown(self):
        self.patch_env.stop()
        self.patch_backend.stop()

    # -- helpers ---------------------------------------------------------
    def payload(self, sid, name="a.txt", tool="write", call="call-1", cwd=None, command="", patch=None,
                event="tools/pre-execute", response=None):
        cwd = str(cwd or self.repo)
        if patch is not None:
            ti = {"command": patch}
        elif tool == "pwsh":
            ti = {"command": command, "workdir": cwd}
        else:
            ti = {"file_path": str(Path(cwd) / name), "content": "1\n"}
        data = {"hook_event_name": event, "session_id": sid, "tool_use_id": call, "cwd": cwd,
                "tool_name": tool, "tool_input": ti, "dsh": {"session_log": "", "turn": 1, "step": 1}}
        if response is not None:
            data["tool_response"] = response
        return data

    def run_hook(self, command, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", FakeStdin(raw)), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = D.main([command])
        return rc, out.getvalue(), err.getvalue()

    def state_data(self):
        common, _, _ = pstate.repo_context(self.repo)
        return L.load_state(pstate.state_dir_for(common))

    def decision(self, out):
        return json.loads(out)["hookSpecificOutput"]

    # -- pre-write decision ---------------------------------------------
    def test_pre_write_on_personal_repo_is_allowed_and_reserved(self):
        rc, out, err = self.run_hook("pre", self.payload("A"))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        claim = self.state_data()["claims"]["a.txt"]
        self.assertEqual(claim["holder"], "dsh:A")
        self.assertIn(H.correlation(D.normalize(self.payload("A")), "dsh"), claim["inflight"])

    def test_conflicting_session_is_denied_with_exit_2(self):
        self.assertEqual(self.run_hook("pre", self.payload("A"))[0], 0)
        rc, out, err = self.run_hook("pre", self.payload("B", call="call-2"))
        self.assertEqual(rc, 2)
        info = self.decision(out)
        self.assertEqual(info["permissionDecision"], "deny")
        self.assertIn("a.txt", info["permissionDecisionReason"])
        self.assertEqual(self.state_data()["claims"]["a.txt"]["holder"], "dsh:A")

    def test_multifile_patch_conflict_keeps_no_partial_reservation(self):
        patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-0\n+1\n*** End Patch"
        self.assertEqual(self.run_hook("pre", self.payload("A", tool="apply_patch", patch=patch))[0], 0)
        both = ("*** Begin Patch\n*** Update File: b.txt\n@@\n-0\n+1\n"
                "*** Update File: a.txt\n@@\n-0\n+1\n*** End Patch")
        rc, out, err = self.run_hook("pre", self.payload("B", tool="apply_patch", patch=both, call="call-2"))
        self.assertEqual(rc, 2)
        claims = self.state_data()["claims"]
        self.assertNotIn("b.txt", claims)
        self.assertEqual(claims["a.txt"]["holder"], "dsh:A")

    def test_unidentifiable_host_process_is_denied_with_exit_2(self):
        self.proc.chain = [(9, 0, "explorer.exe")]
        rc, out, err = self.run_hook("pre", self.payload("A"))
        self.assertEqual(rc, 2)
        self.assertEqual(self.decision(out)["permissionDecision"], "deny")
        self.assertIn("认不出", out + err)
        self.assertEqual(self.state_data()["claims"], {})

    def test_corrupt_lock_state_fails_closed_and_is_preserved(self):
        self.assertEqual(self.run_hook("pre", self.payload("A"))[0], 0)
        common, _, _ = pstate.repo_context(self.repo)
        path = pstate.state_dir_for(common) / L.LOCK_FILE
        path.write_text("BROKEN", encoding="utf-8")
        rc, out, err = self.run_hook("pre", self.payload("B", call="call-2"))
        self.assertEqual(rc, 2)
        self.assertEqual(self.decision(out)["permissionDecision"], "deny")
        self.assertEqual(path.read_text(encoding="utf-8"), "BROKEN")

    def test_stale_end_receipt_blocks_then_start_cleans_it(self):
        L.E.mark(self.state, "dsh:A")
        rc, out, err = self.run_hook("pre", self.payload("A"))
        self.assertEqual(rc, 2)
        self.assertIn("结束回执", out)
        start = self.payload("A", event="agent/created")
        self.assertEqual(self.run_hook("start", start)[0], 0)
        self.assertFalse(L.E.is_ended(self.state, "dsh:A"))
        self.assertEqual(self.run_hook("pre", self.payload("A", call="call-retry"))[0], 0)

    # -- settlement ------------------------------------------------------
    def test_post_success_marks_the_claim_written(self):
        pre = self.payload("A")
        self.assertEqual(self.run_hook("pre", pre)[0], 0)
        post = dict(pre, hook_event_name="tools/post-execute",
                    tool_response={"is_error": False, "content": "ok"})
        self.assertEqual(self.run_hook("post", post)[0], 0)
        self.assertTrue(self.state_data()["claims"]["a.txt"]["written"])

    def test_post_failure_rolls_back_a_new_claim(self):
        pre = self.payload("A")
        self.assertEqual(self.run_hook("pre", pre)[0], 0)
        post = dict(pre, hook_event_name="tools/post-execute",
                    tool_response={"is_error": True, "content": "boom"})
        self.assertEqual(self.run_hook("post", post)[0], 0)
        self.assertEqual(self.state_data()["claims"], {})

    def test_explicit_fail_settles_like_a_failed_post(self):
        pre = self.payload("A")
        self.assertEqual(self.run_hook("pre", pre)[0], 0)
        self.assertEqual(self.run_hook("fail", dict(pre, hook_event_name="tools/post-execute"))[0], 0)
        self.assertEqual(self.state_data()["claims"], {})

    # -- session lifecycle ----------------------------------------------
    def test_disposed_releases_session_claims_inflight_and_receipt(self):
        pre = self.payload("A")
        self.assertEqual(self.run_hook("pre", pre)[0], 0)
        self.assertTrue(list((self.state / "inflight").glob("*")))
        rc, out, err = self.run_hook("end", self.payload("A", event="agent/disposed"))
        self.assertEqual(rc, 0)
        st = self.state_data()
        self.assertEqual(st["sessions"], {})
        self.assertEqual(st["claims"], {})
        self.assertEqual(list((self.state / "inflight").glob("*")), [])
        self.assertFalse(L.E.is_ended(self.state, "dsh:A"))

    def test_end_keeps_other_sessions_claims(self):
        self.assertEqual(self.run_hook("pre", self.payload("A"))[0], 0)
        self.assertEqual(self.run_hook("pre", self.payload("B", name="b.txt", call="call-2"))[0], 0)
        self.assertEqual(self.run_hook("end", self.payload("A", event="agent/disposed"))[0], 0)
        self.assertEqual(set(self.state_data()["claims"]), {"b.txt"})

    def test_start_only_hints_and_does_not_scan_or_reserve(self):
        self.assertEqual(self.run_hook("pre", self.payload("A"))[0], 0)
        before = sorted(str(p) for p in self.state.rglob("*"))
        rc, out, err = self.run_hook("start", self.payload("B", event="agent/created"))
        self.assertEqual(rc, 0)
        info = self.decision(out)
        self.assertIn("additionalContext", info)
        self.assertIn("会话", info["additionalContext"])
        self.assertEqual(sorted(str(p) for p in self.state.rglob("*")), before)
        self.assertEqual(set(self.state_data()["claims"]), {"a.txt"})

    def test_prompt_without_tracked_cards_is_silent(self):
        rc, out, err = self.run_hook("prompt", self.payload("A", event="agent/inbox/inserted"))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_turn_stopping_is_a_no_op(self):
        self.assertEqual(self.run_hook("pre", self.payload("A"))[0], 0)
        rc, out, err = self.run_hook("turn", self.payload("A", event="agent/turn-stopping"))
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")
        self.assertTrue(self.state_data()["claims"]["a.txt"]["inflight"])

    def test_unknown_command_reports_a_nonblocking_error(self):
        rc, out, err = self.run_hook("bogus", self.payload("A"))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("unknown DSH hook command", err)

    def test_malformed_payload_on_pre_is_denied(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", FakeStdin(b"{not json")), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = D.main(["pre"])
        self.assertEqual(rc, 2)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("JSON", err.getvalue() + out.getvalue())

    # -- real process boundaries ----------------------------------------
    def test_script_process_exit_codes_allow_deny_recover(self):
        driver = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import pstate_lock as L\n"
            "class P:\n"
            "    chain=[(100,1,'DeepSeek Harness.exe'),(9,0,'explorer.exe')]\n"
            "    def ancestry(self): return self.chain\n"
            "    def alive(self,pid,ctime): return True\n"
            "L.process_backend=lambda:P()\n"
            "import pstate_dsh\n"
            "sys.exit(pstate_dsh.main([sys.argv[2]]))\n"
        )
        env = dict(os.environ, PSTATE_HOME=str(self.state), DSH_SESSION_ID="",
                   CLAUDE_CODE_SESSION_ID="", CODEX_THREAD_ID="")

        def run(command, payload):
            return subprocess.run([sys.executable, "-c", driver, str(ROOT / "scripts"), command],
                                  input=json.dumps(payload).encode("utf-8"), capture_output=True,
                                  cwd=str(self.repo), env=env)

        allow = run("pre", self.payload("A"))
        self.assertEqual(allow.returncode, 0, allow.stderr.decode("utf-8", "replace"))
        deny = run("pre", self.payload("B", call="call-2"))
        self.assertEqual(deny.returncode, 2)
        self.assertEqual(json.loads(deny.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        ended = run("end", self.payload("A", event="agent/disposed"))
        self.assertEqual(ended.returncode, 0)
        recover = run("pre", self.payload("B", call="call-3"))
        self.assertEqual(recover.returncode, 0)
        self.assertEqual(self.state_data()["claims"]["a.txt"]["holder"], "dsh:B")


class DshManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "hooks" / "dsh.json").read_text(encoding="utf-8"))

    def test_contract_event_names_and_commands(self):
        hooks = self.manifest["hooks"]
        expected = {"agent/created": "start", "agent/inbox/inserted": "prompt",
                    "tools/pre-execute": "pre", "tools/post-execute": "post",
                    "agent/disposed": "end"}
        self.assertEqual(set(hooks), set(expected))
        for event, command in expected.items():
            for group in hooks[event]:
                self.assertEqual(len(group["hooks"]), 1)
                hook = group["hooks"][0]
                self.assertEqual(hook["type"], "command")
                self.assertIn("/scripts/pstate_dsh.py\" " + command, hook["command"])
                self.assertIn("py -3", hook["commandWindows"])
                self.assertIn("/scripts/pstate_dsh.py\" " + command, hook["commandWindows"])
                self.assertGreater(hook["timeout"], 0)
                for field in ("command", "commandWindows"):
                    self.assertIn("${PLUGIN_ROOT}", hook[field])

    def test_start_matcher_and_write_matchers(self):
        self.assertEqual(self.manifest["hooks"]["agent/created"][0]["matcher"], "^(startup|resume|clear|compact)$")
        self.assertIn("write", self.manifest["hooks"]["tools/pre-execute"][0]["matcher"])
        self.assertIn("pwsh", self.manifest["hooks"]["tools/pre-execute"][0]["matcher"])
        self.assertIn("edit", self.manifest["hooks"]["tools/post-execute"][0]["matcher"])


if __name__ == "__main__":
    unittest.main()
