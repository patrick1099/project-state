"""Shared native history events: body review, activity, and background clues."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstate_history import PLATFORMS, discover, load_sessions, verify_source
from test_history import STAMP, assistant, claude_user, human, meta


LATER = "2026-10-22T12:00:03Z"


class HistoryEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options = {"roots": {}, "files": {}}

    def write(self, platform, rows, name=None):
        path = self.root / (name or platform + ".jsonl")
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        paths = self.options["files"].setdefault(platform, [])
        if path not in paths:
            paths.append(path)
        return path

    def session(self, **kw):
        return load_sessions(self.options, **kw)["sessions"][0]

    def tool(self, stamp=LATER, path="C:/personal/tool.py", call_id="write1"):
        return {"type": "assistant", "sessionId": "c1", "uuid": call_id, "timestamp": stamp,
                "message": {"content": [{"type": "tool_use", "id": call_id, "name": "Write",
                                          "input": {"file_path": path, "content": "new"}}]}}

    def result(self, call_id="write1", error=False, stamp=LATER):
        return {"type": "user", "sessionId": "c1", "uuid": call_id + "-result", "timestamp": stamp,
                "message": {"content": [{"type": "tool_result", "tool_use_id": call_id,
                                          "is_error": error, "content": "ok"}]}}

    def test_recap_becomes_stale_after_tool_activity_without_body_change(self):
        recap = {"type": "system", "subtype": "away_summary", "sessionId": "c1",
                 "timestamp": "2026-10-22T12:00:01Z",
                 "content": "Pending work (disable recaps in /config)"}
        rows = [claude_user(), recap]
        self.write("claude", rows)
        before = self.session()
        source = discover(self.options)["sessions"][0]["sources"][0]
        self.assertTrue(before["summary"]["recap_fresh"])
        self.assertEqual(before["summary"]["recap"]["text"], "Pending work")
        self.write("claude", rows + [self.tool(), self.result()])
        after = self.session()
        self.assertEqual(before["version"], after["review_version"])
        self.assertEqual(after["version"], after["review_version"])
        self.assertNotEqual(before["activity_version"], after["activity_version"])
        self.assertEqual(after["activity_time"], LATER)
        self.assertEqual(after["summary"]["version"], LATER)
        self.assertFalse(after["summary"]["recap_fresh"])
        self.assertTrue(verify_source(source, self.options)["verified"])
        seen = {"claude:c1": {"version": before["review_version"]}}
        self.assertEqual(discover({**self.options, "seen": seen})["sessions"], [])

    def test_tool_payload_change_same_timestamp_changes_only_activity_hash(self):
        rows = [claude_user(), self.tool(), self.result()]
        self.write("claude", rows)
        before = self.session()
        rows[1]["message"]["content"][0]["input"]["content"] = "different"
        self.write("claude", rows)
        after = self.session()
        self.assertEqual(before["review_version"], after["review_version"])
        self.assertEqual(before["summary"]["version"], after["summary"]["version"])
        self.assertNotEqual(before["activity_version"], after["activity_version"])

    def test_successful_write_paths_and_owner_clues_preserved(self):
        user = claude_user(cwd="C:/company/cwd", gitBranch="feature")
        reply = {"type": "assistant", "sessionId": "c1", "uuid": "a", "timestamp": STAMP,
                 "message": {"content": [{"type": "text", "text": "Still working"}]}}
        rows = [user, reply, self.tool(), self.result(),
                self.tool(path="C:/personal/failed.py", call_id="failed"), self.result("failed", True),
                self.tool(path="C:/personal/pending.py", call_id="pending")]
        self.write("claude", rows)
        session = self.session()
        summary = session["summary"]
        self.assertEqual(session["identity"], "claude:c1")
        self.assertEqual(summary["native_session"], "c1")
        self.assertEqual(summary["cwd"], "C:/company/cwd")
        self.assertEqual(summary["branch"], "feature")
        self.assertEqual(summary["entrypoint"], "cli")
        self.assertEqual(summary["human_count"], 1)
        self.assertEqual(summary["first_human"], "Continue")
        self.assertEqual(summary["last_human"], "Continue")
        self.assertEqual(summary["last_assistant"], "Still working")
        self.assertEqual(summary["written"], ["C:/personal/tool.py"])
        self.assertEqual(summary["owner_hints"], {"cwd": "C:/company/cwd", "written": ["C:/personal/tool.py"]})
        page = discover(self.options)["sessions"][0]
        self.assertNotIn("summary", page)
        self.assertNotIn("file_path", json.dumps(page))
        self.assertEqual([m["text"] for m in page["messages"]], ["Continue", "Still working"])

    def test_native_identity_does_not_merge_platforms_or_use_file_stem(self):
        self.write("claude", [claude_user(sessionId="same")], "unrelated-one.jsonl")
        self.write("codex", [meta("same", cwd="C:/other", git={"branch": "main"}), human()], "unrelated-two.jsonl")
        sessions = load_sessions(self.options)["sessions"]
        self.assertEqual({s["identity"] for s in sessions}, {"claude:same", "codex:same"})
        codex = next(s for s in sessions if s["platform"] == "codex")
        self.assertEqual(codex["summary"]["cwd"], "C:/other")
        self.assertEqual(codex["summary"]["branch"], "main")
        self.assertEqual(codex["summary"]["originators"], ["codex_exec"])
        self.assertTrue(codex["interactive"])
        self.assertTrue(PLATFORMS["claude"]["background"])
        self.assertFalse(PLATFORMS["codex"]["background"])

    def test_codex_tool_activity_and_only_confirmed_patch_paths(self):
        def call(call_id, path):
            return {"type": "response_item", "timestamp": LATER, "payload": {
                "type": "custom_tool_call", "call_id": call_id, "name": "apply_patch",
                "input": "*** Begin Patch\n*** Add File: " + path + "\n+data\n*** End Patch"}}
        def output(call_id, text, **kw):
            return {"type": "response_item", "timestamp": LATER, "payload": {
                "type": "custom_tool_call_output", "call_id": call_id, "output": text, **kw}}
        rows = [meta(cwd="C:/personal", git={"branch": "topic"}), human(), assistant()]
        self.write("codex", rows)
        before = self.session()
        rows += [call("ok", "written.py"), output("ok", "Success. Updated the following files:\nA: written.py"),
                 call("failed", "failed.py"), output("failed", "failed", is_error=True),
                 call("unknown", "unknown.py"), output("unknown", "unrecognized output"),
                 call("pending", "pending.py")]
        self.write("codex", rows)
        after = self.session()
        self.assertEqual(before["review_version"], after["review_version"])
        self.assertNotEqual(before["activity_version"], after["activity_version"])
        self.assertEqual(after["summary"]["version"], LATER)
        self.assertEqual(after["summary"]["written"], ["written.py"])
        self.assertEqual(after["summary"]["owner_hints"]["written"], ["written.py"])
        self.assertEqual(len(discover(self.options)["sessions"][0]["messages"]), 2)

    def test_archive_copies_do_not_create_new_activity_or_human_count(self):
        rows = [claude_user(), self.tool(), self.result()]
        self.write("claude", rows)
        before = self.session()
        self.write("claude", rows, "archive-copy.jsonl")
        after = self.session()
        self.assertEqual(before["review_version"], after["review_version"])
        self.assertEqual(before["activity_version"], after["activity_version"])
        self.assertEqual(after["summary"]["human_count"], 1)
        self.assertEqual(len(after["paths"]), 2)

    def test_headless_clues_available_explicitly_but_never_discovered(self):
        self.write("claude", [claude_user(entrypoint="sdk-cli", cwd="C:/background")])
        self.write("codex", [meta("child", source={"subagent": {"parent_thread_id": "parent"}}), human()])
        self.assertEqual(load_sessions(self.options)["sessions"], [])
        self.assertEqual(discover(self.options)["sessions"], [])
        sessions = load_sessions(self.options, include_excluded=True)["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(s["headless"] and not s["interactive"] for s in sessions))
        claude = next(s for s in sessions if s["platform"] == "claude")
        self.assertEqual(claude["summary"]["entrypoint"], "sdk-cli")
        self.assertEqual(claude["summary"]["cwd"], "C:/background")
        codex = next(s for s in sessions if s["platform"] == "codex")
        self.assertEqual(codex["summary"]["native_sources"], [{"subagent": {"parent_thread_id": "parent"}}])

    def test_recap_update_is_not_new_conversation_activity(self):
        rows = [claude_user()]
        self.write("claude", rows)
        before = self.session()
        rows += [{"type": "system", "subtype": "away_summary", "sessionId": "c1",
                  "timestamp": LATER, "content": "A new recap"}]
        self.write("claude", rows)
        after = self.session()
        self.assertEqual(before["activity_version"], after["activity_version"])
        self.assertEqual(before["review_version"], after["review_version"])
        self.assertEqual(after["summary"]["version"], STAMP)
        self.assertTrue(after["summary"]["recap_fresh"])

    def test_registry_capabilities_and_shared_loader_have_no_platform_list(self):
        calls = []
        def fake(files, warnings, counts):
            calls.append(files)
            return []
        with patch.dict(PLATFORMS, {"future": {"parse": fake, "parse_all": fake, "background": True}}):
            load_sessions(self.options, include_excluded=True)
            self.assertIn("future", [name for name, adapter in PLATFORMS.items() if adapter.get("background")])
            discover(self.options)
        self.assertEqual(calls, [[], []])


    def test_image_only_human_metadata_remains_available_to_background(self):
        row = claude_user()
        row["message"]["content"] = [{"type": "image", "source": {"type": "base64", "data": "fake"}}]
        self.write("claude", [row])
        self.assertEqual(discover(self.options)["sessions"], [])
        summary = self.session(include_excluded=True)["summary"]
        self.assertEqual(summary["human_count"], 1)
        self.assertTrue(summary["interactive"])
        self.assertEqual(summary["first_human"], "")

    def test_internal_recap_and_text_are_not_exposed_by_discover_metadata(self):
        secret = "sk-abcdefghijklmnopqrstuv0123456789"
        self.write("claude", [claude_user("Please continue; api_key=" + secret),
                              {"type": "system", "subtype": "away_summary", "sessionId": "c1",
                               "timestamp": LATER, "content": "Internal recap password=" + secret}])
        self.assertIn(secret, self.session()["summary"]["recap"]["text"])
        output = json.dumps(discover(self.options))
        self.assertNotIn(secret, output)
        self.assertNotIn("Internal recap", output)
        self.assertIn("[REDACTED]", output)


if __name__ == "__main__":
    unittest.main()
