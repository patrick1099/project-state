import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstate_history import PLATFORMS, discover, verify_source
from pstate_history.common import digest


STAMP = "2026-10-22T12:00:00Z"


def meta(sid="s1", **kw):
    return {"type": "session_meta", "payload": {"id": sid, "source": "vscode", "thread_source": "user", "originator": "codex_exec", **kw}}


def human(text="Please continue topic A and keep topic B for later.", anchor="u1", stamp=STAMP):
    return {"type": "event_msg", "timestamp": stamp, "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": anchor, "content": [{"type": "text", "text": text}]}}}


def assistant(text="Answer", anchor="a1", channel="final", stamp=STAMP):
    return {"type": "response_item", "timestamp": stamp, "payload": {"type": "message", "id": anchor, "role": "assistant", "channel": channel, "content": [{"type": "output_text", "text": text}]}}


def claude_user(text="Continue", anchor="cu1", **kw):
    return {"type": "user", "uuid": anchor, "sessionId": "c1", "timestamp": STAMP, "entrypoint": "cli", "origin": {"kind": "human"}, "message": {"role": "user", "content": text}, **kw}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.archive = self.root / "archive"
        for path in (self.claude, self.codex, self.archive):
            path.mkdir()
        self.options = {"roots": {"claude": [self.claude], "codex": [self.codex, self.archive]}}

    def write(self, root, name, rows):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        return path

    def discover(self, **options):
        return discover({**self.options, **options})

    def test_modern_codex_native_user_and_assistant_only(self):
        fake = {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Injected user content"}]}}
        self.write(self.codex, "s.jsonl", [meta(), fake, human(), assistant("Hidden thought", channel="analysis"), assistant(), {"type": "compacted", "payload": {"replacement_history": [human("Copy", "copy")]}}])
        result = self.discover()
        messages = result["sessions"][0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertIn("topic A", messages[0]["text"])
        self.assertIn("topic B", messages[0]["text"])
        self.assertTrue(verify_source(messages[0]["source"], self.options)["verified"])
        self.assertFalse(verify_source(messages[1]["source"], self.options)["verified"])

    def test_modern_claude_filters_tools_meta_thinking_and_injection(self):
        rows = [claude_user(), claude_user("meta", "meta", isMeta=True), claude_user("side", "side", isSidechain=True), claude_user("<environment_context>hidden</environment_context>", "env"), claude_user("# AGENTS.md instructions\nhidden", "agents"), claude_user("<task-notification>hidden</task-notification>", "task"), claude_user("copy", "copy", isCompactSummary=True)]
        rows += [{"type": "user", "sessionId": "c1", "message": {"content": [{"type": "tool_result", "content": "tool"}]}}, {"type": "assistant", "sessionId": "c1", "uuid": "ca", "timestamp": STAMP, "message": {"content": [{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "Visible"}]}}]
        self.write(self.claude, "c.jsonl", rows)
        messages = self.discover()["sessions"][0]["messages"]
        self.assertEqual([m["text"] for m in messages], ["Continue", "Visible"])
        self.assertTrue(verify_source(messages[0]["source"], self.options)["verified"])

    def test_headless_and_subagents_excluded(self):
        for index, source in enumerate(("exec", "headless", {"subagent": {"parent_thread_id": "p"}}, "guardian_review", "agent_created_thread")):
            self.write(self.codex, "%s.jsonl" % index, [meta(str(index), source=source), human()])
        self.write(self.codex, "agent.jsonl", [meta("agent", thread_source="agent_created_thread"), human()])
        self.write(self.claude, "headless.jsonl", [claude_user(entrypoint="sdk-cli")])
        self.write(self.claude, "subagents/agent.jsonl", [claude_user()])
        self.assertEqual(self.discover()["sessions"], [])

    def test_legacy_whole_session_degrades_and_cannot_authorize(self):
        old = {"type": "event_msg", "timestamp": STAMP, "payload": {"type": "user_message", "message": "Old user", "id": "old"}}
        self.write(self.codex, "old.jsonl", [meta(), old, assistant()])
        row = claude_user()
        del row["origin"]
        self.write(self.claude, "old.jsonl", [row])
        result = self.discover()
        self.assertEqual(result["coverage"]["legacy"], 2)
        self.assertTrue(all(s["legacy"] for s in result["sessions"]))
        for session in result["sessions"]:
            self.assertFalse(verify_source(session["sources"][0], self.options)["verified"])

    def test_legacy_response_user_supported_without_old_events(self):
        row = {"type": "response_item", "timestamp": STAMP, "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Old question"}]}}
        self.write(self.codex, "old.jsonl", [meta(), row])
        session = self.discover()["sessions"][0]
        self.assertTrue(session["legacy"])
        self.assertEqual(session["messages"][0]["text"], "Old question")
        self.assertTrue(session["sources"][0]["anchor"].startswith("line:"))

    def test_archive_copies_merge_and_real_equal_utterances_remain(self):
        rows = [meta(), human("Same", "u1"), assistant()]
        self.write(self.codex, "s.jsonl", rows)
        self.write(self.archive, "copy.jsonl", rows + [human("Same", "u2", "2026-10-22T12:00:01Z")])
        result = self.discover()
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual([m["text"] for m in result["sessions"][0]["messages"]], ["Same", "Answer", "Same"])

    def test_paging_preserves_first_message_and_expands_large_message(self):
        body = "EARLY_UNFINISHED_" + "x" * 89 + "_END"
        self.write(self.codex, "s.jsonl", [meta(), human(body), assistant("LAST")])
        pages, fragments, cursor = [], [], None
        while True:
            result = self.discover(max_chars=17, cursor=cursor)
            pages.append(result)
            fragments.extend(m["text"] for s in result["sessions"] for m in s["messages"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        self.assertEqual("".join(fragments), body + "LAST")
        self.assertTrue(pages[0]["sessions"][0]["messages"][0]["text"].startswith("EARLY_UNFINISHED"))
        self.assertEqual(pages[1]["sessions"][0]["messages"][0]["offset"], 17)
        source = pages[1]["sessions"][0]["sources"][0]
        self.assertTrue(verify_source(source, self.options)["verified"])

    def test_stale_cursor_rejected(self):
        self.write(self.codex, "s.jsonl", [meta(), human("abcdefghijklmnop")])
        cursor = self.discover(max_chars=4)["next_cursor"]
        self.write(self.codex, "s.jsonl", [meta(), human("changed abcdefghijklmnop")])
        with self.assertRaisesRegex(ValueError, "stale"):
            self.discover(max_chars=4, cursor=cursor)

    def test_seen_explicit_version_and_current_session_exclusion(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        first = self.discover()
        session = first["sessions"][0]
        seen = {"codex:s1": {"version": session["version"], "reviewed_at": STAMP}}
        original = copy.deepcopy(seen)
        self.assertEqual(self.discover(seen=seen)["sessions"], [])
        self.assertEqual(seen, original)
        self.write(self.codex, "s.jsonl", [meta(), human(), human("New subject", "u2")])
        self.assertEqual(len(self.discover(seen=seen)["sessions"]), 1)
        self.assertEqual(self.discover(exclude_sessions=["codex:s1"])["sessions"], [])
        self.assertEqual(self.discover(exclude_sessions=["s1"])["sessions"], [])

    def test_session_limit_and_since(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        self.write(self.codex, "s2.jsonl", [meta("s2"), human("Later", stamp="2026-10-23T12:00:00Z")])
        result = self.discover(limit=1)
        self.assertEqual(result["sessions"][0]["session"], "s2")
        self.assertEqual(self.discover(limit=1, cursor=result["next_cursor"])["sessions"][0]["session"], "s1")
        self.assertEqual(len(self.discover(since="2026-10-23")["sessions"]), 1)

    def test_unknown_missing_and_malformed_coverage(self):
        self.write(self.codex, "unknown.jsonl", [{"type": "unknown"}])
        (self.codex / "broken.jsonl").write_text("invalid\n", encoding="utf-8")
        options = {"roots": {"codex": [self.codex, self.root / "absent"]}}
        result = discover(options)
        self.assertEqual(result["coverage"]["unknown"], 2)
        self.assertEqual(result["coverage"]["malformed_lines"], 1)
        self.assertEqual(result["coverage"]["missing_roots"], 1)
        self.assertFalse(result["coverage"]["history_complete"])

    def test_read_failure_reports_gap(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            result = self.discover()
        self.assertEqual(result["coverage"]["unreadable"], 1)
        self.assertEqual(result["sessions"], [])

    def test_sweep_closed_completed_company_are_only_hints(self):
        self.write(self.claude, "c.jsonl", [claude_user()])
        state = self.root / "sweep.json"
        state.write_text(json.dumps({"sessions": {"c1": {"summary": {"version": "v2"}, "verdict": {"version": "v1", "status": "完成", "owner": "公司", "title": "Old title"}, "gone": True}}, "closures": {"c1@v2": {"at": STAMP}}}), encoding="utf-8")
        result = self.discover(sweep_state=state)
        hint = result["sessions"][0]["sweep_hint"]
        self.assertTrue(hint["closed"])
        self.assertTrue(hint["stale"])
        self.assertEqual(hint["authority"], "hint_only")
        self.assertEqual(result["coverage"]["gone"], 1)
        result = self.discover(sweep_state=self.root / "missing.json")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertTrue(any(w["kind"] == "sweep_unreadable" for w in result["warnings"]))

    def test_verification_rejects_tampered_identity_hash_role_and_path(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        source = self.discover()["sessions"][0]["sources"][0]
        for key, value in (("session", "wrong"), ("platform", "claude"), ("anchor", "wrong"), ("time", "wrong"), ("role", "assistant"), ("excerpt_hash", "wrong"), ("path", str(self.root / "outside.jsonl"))):
            with self.subTest(key=key):
                self.assertFalse(verify_source({**source, key: value}, self.options)["verified"])
        altered = {**source, "excerpt": "Invented statement", "excerpt_hash": digest("Invented statement")}
        self.assertFalse(verify_source(altered, self.options)["verified"])
        other = self.write(self.codex, "other.jsonl", [meta("other"), human()])
        self.assertFalse(verify_source({**source, "path": str(other)}, self.options)["verified"])

    def test_source_missing_or_partially_written_is_unverified(self):
        path = self.write(self.codex, "s.jsonl", [meta(), human()])
        source = self.discover()["sessions"][0]["sources"][0]
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"unfinished":')
        self.assertFalse(verify_source(source, self.options)["verified"])
        path.unlink()
        self.assertFalse(verify_source(source, self.options)["verified"])

    def test_secrets_redacted_in_body_excerpt_and_verified_consistently(self):
        secret = "sk-abcdefghijklmnopqrstuv0123456789"
        self.write(self.codex, "s.jsonl", [meta(), human("Continue; api_key=" + secret)])
        session = self.discover()["sessions"][0]
        self.assertNotIn(secret, json.dumps(session))
        self.assertIn("[REDACTED]", session["messages"][0]["text"])
        self.assertTrue(verify_source(session["sources"][0], self.options)["verified"])

    def test_registry_extension_is_automatically_discovered(self):
        called = []
        def fake(files, warnings, counts):
            called.append(files)
            return []
        with patch.dict(PLATFORMS, {"future": {"parse": fake}}):
            self.discover()
        self.assertEqual(called, [[]])

    def test_external_mapping_is_reported_without_silent_merge(self):
        (self.claude / "external_agent_session_imports.json").write_text('{"c1":"s1"}', encoding="utf-8")
        self.write(self.claude, "c.jsonl", [claude_user()])
        result = self.discover()
        self.assertTrue(any(w["kind"] == "external_import_mapping" for w in result["warnings"]))
        self.assertEqual(len(result["sessions"]), 1)

    def test_seen_update_between_pages_does_not_invalidate_cursor(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        self.write(self.codex, "s2.jsonl", [meta("s2"), human("Latest", stamp="2026-10-23T00:00:00Z")])
        page = self.discover(limit=1)
        first = page["sessions"][0]
        seen = {"codex:" + first["session"]: {"version": first["version"]}}
        next_page = self.discover(limit=1, cursor=page["next_cursor"], seen=seen)
        self.assertEqual(next_page["sessions"][0]["session"], "s1")
        self.assertIsNone(next_page["next_cursor"])

    def test_explicit_files_do_not_enumerate_other_history(self):
        target = self.write(self.codex, "s.jsonl", [meta(), human()])
        self.write(self.codex, "other.jsonl", [meta("other"), human("Do not scan")])
        options = {"files": {"codex": [target]}, "roots": {}}
        with patch("pstate_history.core.enumerate_paths", side_effect=AssertionError("Unexpected scan")):
            result = discover(options)
        self.assertEqual([s["session"] for s in result["sessions"]], ["s1"])
        self.assertEqual(result["coverage"]["files"], 1)
        self.assertTrue(verify_source(result["sessions"][0]["sources"][0], options)["verified"])

    def test_whitespace_page_has_nonempty_verifiable_excerpt(self):
        self.write(self.codex, "s.jsonl", [meta(), human("First" + " " * 30 + "Keep this topic")])
        page = self.discover(max_chars=10)
        result = self.discover(max_chars=10, cursor=page["next_cursor"])
        self.assertEqual(result["sessions"][0]["sources"][0]["excerpt"], "First" + " " * 30 + "Keep this topic")
        self.assertTrue(verify_source(result["sessions"][0]["sources"][0], self.options)["verified"])
    def test_future_field_shapes_do_not_abort_recognized_history(self):
        self.write(self.codex, "s.jsonl", [meta(), {"type": "event_msg", "payload": ["future"]}, human()])
        self.write(self.codex, "future.jsonl", [meta("future", source=["future-source"]), human()])
        self.write(self.claude, "future.jsonl", [claude_user(origin="future-origin")])
        result = self.discover()
        self.assertEqual([s["session"] for s in result["sessions"]], ["s1"])
        self.assertEqual(result["coverage"]["unknown"], 2)

    def test_conflicting_session_metadata_cannot_verify(self):
        self.write(self.codex, "s.jsonl", [meta(), human()])
        source = self.discover()["sessions"][0]["sources"][0]
        self.write(self.codex, "s.jsonl", [meta(), meta("other"), human()])
        self.assertFalse(verify_source(source, self.options)["verified"])
        self.assertEqual(self.discover()["sessions"], [])

    def test_legacy_local_command_and_agent_notifications_excluded(self):
        rows = []
        for index, text in enumerate(("<command-name>/clear</command-name>", "<local-command-caveat>meta", "Message Type: NEW_TASK\nAutomated worker task", "Real old question")):
            row = claude_user(text, str(index))
            del row["origin"]
            rows.append(row)
        self.write(self.claude, "legacy.jsonl", rows)
        self.assertEqual([m["text"] for m in self.discover()["sessions"][0]["messages"]], ["Real old question"])
    def test_ide_and_reminder_wrappers_preserve_actual_user_text(self):
        wrapped = "<ide_opened_file>IDE file context</ide_opened_file>\n<system-reminder>Injected context</system-reminder>\nPlease continue the first task.\n<ide_selection start=\"1\">Selected code</ide_selection>"
        self.write(self.claude, "c.jsonl", [claude_user(wrapped)])
        self.write(self.codex, "s.jsonl", [meta(), human("<system-reminder>context</system-reminder>\nKeep second task")])
        result = self.discover()
        texts = [s["messages"][0]["text"] for s in result["sessions"]]
        self.assertCountEqual(texts, ["Please continue the first task.", "Keep second task"])
        for session in result["sessions"]:
            self.assertTrue(verify_source(session["sources"][0], self.options)["verified"])
            self.assertNotIn("context", session["sources"][0]["excerpt"])
    def test_invalid_limits_and_cursor_rejected(self):
        for options in ({"limit": 0}, {"max_chars": -1}, {"limit": True}, {"cursor": "not-a-cursor"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.discover(**options)


if __name__ == "__main__":
    unittest.main()