"""Frozen capture contracts use temporary state and explicit native fixtures only."""
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
import pstate_history
from pstate_sweep import review
from pstate_threads import store
from pstate_threads.core import ThreadError


class FrozenReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.directory = self.home / "threads"
        self.log = self.root / "native.jsonl"
        self.rows = [
            {"sessionId": "native-review", "uuid": "u" + str(i), "type": "user",
             "timestamp": "2026-10-22T00:00:0" + str(i) + "Z", "origin": {"kind": "human"},
             "entrypoint": "cli", "message": {"role": "user", "content": "Continue matter " + str(i)}}
            for i in range(3)]
        self.write_log()
        self.options = {"files": {"claude": [str(self.log)]}, "roots": {}}
        self.session = pstate_history.discover(self.options)["sessions"][0]
        self.sources = self.session["sources"]

    def write_log(self):
        self.log.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n", encoding="utf-8")

    def plan(self, complete=True):
        return copy.deepcopy({"platform": "claude", "session": "native-review",
                "review_version": self.session["review_version"], "files": self.options["files"],
                "items": [{"payload": {"title": "Matter A", "sources": [self.sources[0]]}},
                          {"payload": {"title": "Matter B", "sources": [self.sources[0]]}, "distinct": True}],
                "complete": complete})

    def test_partial_failure_same_frozen_retry_after_rename_no_duplicate_or_early_seen(self):
        plan = self.plan()
        frozen = copy.deepcopy(plan)
        real = store.mutate
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("injected second capture failure")
            return real(*args, **kwargs)

        with patch.object(store, "mutate", side_effect=fail_second):
            with self.assertRaises(ThreadError) as failure:
                review.review(self.home, plan)
        self.assertEqual(failure.exception.code, "E_PARTIAL_FAILURE")
        state = store.read(self.directory)
        self.assertEqual(len(state["items"]), 1)
        self.assertEqual(state["seen"], {})
        ident = next(iter(state["items"]))
        store.mutate(self.directory, "set", {"title": "Renamed by user"}, ident=ident, rev=1)
        result = review.review(self.home, plan)
        self.assertEqual(plan, frozen)
        self.assertEqual(result["input_hash"], failure.exception.details["input_hash"])
        self.assertTrue(result["saved"][0]["replayed"])
        state = store.read(self.directory)
        self.assertEqual(len(state["items"]), 2)
        self.assertEqual(state["items"][ident]["title"], "Renamed by user")
        self.assertEqual(state["seen"]["claude:native-review"]["version"], plan["review_version"])

    def test_partial_complete_and_empty_review_seen_last(self):
        plan = self.plan(complete=False)
        result = review.review(self.home, plan)
        self.assertFalse(result["seen"])
        self.assertFalse(store.read(self.directory)["seen"])
        plan["complete"] = True
        events = []
        real = store.mutate

        def observe(directory, op, payload, **kwargs):
            events.append(op)
            return real(directory, op, payload, **kwargs)

        with patch.object(store, "mutate", side_effect=observe):
            review.review(self.home, plan)
        self.assertEqual(events, ["add", "add", "seen"])
        plan["items"] = []
        self.assertTrue(review.review(self.root / "empty-home", plan)["seen"])
        self.assertEqual(store.read(self.root / "empty-home" / "threads")["items"], {})

    def test_into_replay_ignores_obsolete_rev_after_sources_already_persisted(self):
        item = store.mutate(self.directory, "add", {"title": "Existing", "sources": [self.sources[0]]})["item"]
        plan = self.plan()
        plan["items"] = [{"payload": {"sources": [self.sources[1]]}, "into": item["id"], "rev": 1}]
        review.review(self.home, plan)
        state = store.read(self.directory)
        self.assertEqual(state["items"][item["id"]]["rev"], 2)
        result = review.review(self.home, plan)
        self.assertTrue(result["saved"][0]["replayed"])
        self.assertEqual(len(store.read(self.directory)["items"]), 1)
        self.assertEqual(store.read(self.directory)["items"][item["id"]]["rev"], 2)

    def test_independent_readback_failure_never_marks_seen_and_replays(self):
        real = store.read
        count = 0

        def fail_readback(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("independent readback failed")
            return real(*args)

        with patch.object(store, "read", side_effect=fail_readback):
            with self.assertRaises(ThreadError):
                review.review(self.home, self.plan())
        self.assertEqual(len(real(self.directory)["items"]), 1)
        self.assertEqual(real(self.directory)["seen"], {})
        result = review.review(self.home, self.plan())
        self.assertTrue(result["saved"][0]["replayed"])
        self.assertEqual(len(real(self.directory)["items"]), 2)

    def test_all_input_prevalidated_no_implicit_distinct_no_partial_schema_write(self):
        for edit in ("distinct", "field", "hash"):
            plan = self.plan()
            if edit == "distinct":
                plan["items"][1].pop("distinct")
            elif edit == "field":
                plan["items"][1]["payload"]["state"] = "done"
            else:
                plan["items"][1]["payload"]["sources"][0]["excerpt_hash"] = "fake"
            with self.assertRaises(ThreadError):
                review.review(self.home, plan)
            self.assertFalse((self.directory / "threads.json").exists())

    def test_summary_cannot_impersonate_original_user_or_verified_confirmation(self):
        plan = self.plan()
        forged = plan["items"][0]["payload"]["sources"][0]
        forged["excerpt"] = "invented old AI summary"
        forged["excerpt_hash"] = hashlib.sha256(forged["excerpt"].encode()).hexdigest()
        with self.assertRaises(ThreadError):
            review.review(self.home, plan)
        plan = self.plan()
        plan["items"][0]["payload"]["sources"][0]["role"] = "assistant"
        with self.assertRaises(ThreadError):
            review.review(self.home, plan)
        plan = self.plan()
        plan["items"] = plan["items"][:1]
        plan["items"][0]["payload"]["confirmed"] = [{"said": self.sources[0]["excerpt"], "source": self.sources[0]}]
        result = review.review(self.home, plan)
        item = store.read(self.directory)["items"][result["saved"][0]["id"]]
        self.assertFalse(item["confirmed"][0]["verified"], "explicit files never expand confirmation trust")
        self.assertEqual(item["state"], "open")

    def test_confirmation_in_default_native_roots_reuses_existing_verifier(self):
        plan = self.plan()
        plan["items"] = plan["items"][:1]
        plan["items"][0]["payload"]["confirmed"] = [{"said": self.sources[0]["excerpt"], "source": self.sources[0]}]
        with patch.dict(pstate_history.DEFAULT_ROOTS, {"claude": (self.root,)}):
            result = review.review(self.home, plan)
        item = store.read(self.directory)["items"][result["saved"][0]["id"]]
        self.assertTrue(item["confirmed"][0]["verified"])
        self.assertEqual(item["state"], "open")

    def test_version_changed_before_write_or_before_seen_remains_unreviewed(self):
        plan = self.plan()
        self.rows[0]["message"]["content"] += " new body"
        self.write_log()
        with self.assertRaises(ThreadError):
            review.review(self.home, plan)
        self.assertFalse((self.directory / "threads.json").exists())
        self.rows[0]["message"]["content"] = "Continue matter 0"
        self.write_log()
        real = store.mutate

        def append_after_capture(directory, op, payload, **kwargs):
            result = real(directory, op, payload, **kwargs)
            if op == "add":
                self.rows[0]["message"]["content"] += " changed during review"
                self.write_log()
            return result

        with patch.object(store, "mutate", side_effect=append_after_capture):
            with self.assertRaises(ThreadError):
                review.review(self.home, plan)
        self.assertEqual(len(store.read(self.directory)["items"]), 2)
        self.assertEqual(store.read(self.directory)["seen"], {})

    def test_dry_run_shared_mutation_gate_handles_sequential_into_revisions(self):
        item = store.mutate(self.directory, "add", {"title": "Existing", "sources": [self.sources[0]]})["item"]
        before = (self.directory / "threads.json").read_bytes()
        plan = self.plan()
        plan["items"] = [{"payload": {"sources": [self.sources[i]]}, "into": item["id"], "rev": i}
                         for i in (1, 2)]
        result = review.review(self.home, plan, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["seen"])
        self.assertEqual([s["rev"] for s in result["saved"]], [2, 3])
        self.assertTrue(all(not s["persisted"] for s in result["saved"]))
        self.assertEqual((self.directory / "threads.json").read_bytes(), before)
        self.assertEqual(store.read(self.directory)["seen"], {})

    def test_real_cli_freeze_file_replay_format_and_hash_error_details(self):
        value = self.root / "review.json"
        value.write_text(json.dumps(self.plan()), encoding="utf-8")
        env = dict(os.environ, PSTATE_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        args = [sys.executable, "-B", str(ROOT / "scripts/pstate.py"), "sweep", "review",
                "--input-file", str(value)]
        for flags in (["--dry-run", "--json"], ["--json"], ["--format", "json"]):
            proc = subprocess.run([*args, *flags], env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(set(data), {"ok", "data", "error", "meta"})
        self.assertEqual(len(store.read(self.directory)["items"]), 2)
        plan = self.plan()
        plan["review_version"] = "stale"
        value.write_text(json.dumps(plan), encoding="utf-8")
        proc = subprocess.run([*args, "--json"], env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        error = json.loads(proc.stderr)["error"]
        self.assertEqual(error["details"]["input_hash"], review.input_hash(plan))
        self.assertFalse(error["retryable"])


if __name__ == "__main__":
    unittest.main()
