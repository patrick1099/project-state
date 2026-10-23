import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstate_sweep import runtime as cli, config, core, migration
from pstate_sweep.adapters import claude_judge, docgate, schtask, store
from pstate_sweep.ports import JudgeError
from sweep_helpers import DAY, NOW, FakeGate, FakeJudge, assistant, human, make_cfg, recap, sid, write_transcript

ROOT = Path(__file__).resolve().parent.parent


class RunSweepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(self.tmp.name)
        self.proj = self.cfg["projects_dir"]
        self.out = Path(self.tmp.name) / "out"
        self.state = str(Path(self.tmp.name) / "sweep")
        self.workdir = Path(self.tmp.name) / "work"
        self.workdir.mkdir()
        self.t = NOW - 5 * DAY
        migration.switch(self.cfg, Path(self.state).parent, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, judge, gate=None, **kw):
        return cli.run_sweep(self.cfg, judge, lambda: gate or FakeGate(), str(self.out), self.state,
                             now=NOW, log=lambda *_: None, **kw)

    def page(self):
        return (self.out / self.cfg["page_name"]).read_text(encoding="utf-8")

    def test_end_to_end_and_idempotent(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir)), assistant("等你定", self.t + 5),
                                             recap("X 做了一半，等你定", self.t + 300)])
        write_transcript(self.proj, sid(2), [human("做 Y", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(3), [human("刚聊过", NOW - 3600, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(4), [human("ai-room 派发", self.t, entry="sdk-cli")])
        judge = FakeJudge({sid(1): {"status": "悬置", "title": "X 事", "stopped_at": "等你定"},
                           sid(2): {"status": "完成", "title": "Y", "stopped_at": ""}})
        r = self.run_once(judge)
        self.assertEqual([(q["sid"], q["mode"]) for q in judge.requests], [(sid(1), "recap"), (sid(2), "resume")])
        self.assertEqual(r["calls"], 2)
        page = self.page()
        self.assertIn("X 事", page)
        self.assertNotIn(sid(2), page)
        self.assertNotIn(sid(3), page)
        self.assertNotIn(sid(4), page)
        self.assertTrue((self.out / self.cfg["closed_name"]).exists())

        judge2 = FakeJudge()
        r2 = self.run_once(judge2)
        self.assertEqual(judge2.requests, [], "没有新对话就不重判")
        self.assertFalse(r2["page_written"], "内容不变不写")
        self.assertIn("X 事", self.page(), "跳过模型调用时仍用缓存渲染")

    def closed_page(self):
        return (self.out / self.cfg["closed_name"]).read_text(encoding="utf-8")

    def toggle(self, name, n, mark):
        """模拟用户在 Obsidian 里只改某条的勾选。"""
        path = self.out / self.cfg[name]
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            if f"%%ss:claude:{sid(n)}@" in line:
                lines[i] = line.replace("[ ]", f"[{mark}]", 1).replace("[x]", f"[{mark}]", 1)
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_tick_closes_untick_restores(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(2), [human("做 Y", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge({sid(1): {"status": "悬置", "title": "X 事", "stopped_at": "s"}}))
        self.assertIn("最近没有关闭的条目", self.closed_page())
        self.toggle("page_name", 1, "x")
        r = self.run_once(FakeJudge())
        self.assertNotIn(sid(1), self.page())
        self.assertIn(sid(2), self.page())
        self.assertIn(f"- [x] **X 事**", self.closed_page())
        self.assertEqual(r["closed"], 1)

        r = self.run_once(FakeJudge())
        self.assertFalse(r["page_written"] or r["closed_written"], "没人动勾选就不重写")

        self.toggle("closed_name", 1, " ")
        self.run_once(FakeJudge())
        self.assertIn(f"- [ ] **X 事**", self.page())
        self.assertNotIn(sid(1), self.closed_page())

    def test_closed_reopens_on_new_conversation(self):
        p = write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        self.toggle("page_name", 1, "x")
        self.run_once(FakeJudge())
        self.assertNotIn(sid(1), self.page())
        rows = [human("做 X", self.t, cwd=str(self.workdir)), human("又聊了一句", self.t + DAY, cwd=str(self.workdir))]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        judge = FakeJudge()
        self.run_once(judge)
        self.assertEqual(len(judge.requests), 1)
        self.assertIn(sid(1), self.page())
        self.assertNotIn(sid(1), self.closed_page(), "旧版本的关闭不再生效，也不再列在已处理页")
        self.assertEqual(store.load_state(self.state)["closures"], {})

    def test_legacy_record_migrated(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        version = store.load_state(self.state)["sessions"]["claude:" + sid(1)]["summary"]["version"]
        closed_path = self.out / self.cfg["closed_name"]
        closed_path.write_text(f"# 旧页\n\n关闭 {sid(1)} @ {version}\n", encoding="utf-8")
        self.run_once(FakeJudge())
        self.assertNotIn(sid(1), self.page())
        self.assertIn(f"- [x] **默认标题**", self.closed_page())
        self.assertNotIn("关闭 ", self.closed_page(), "旧格式行被新格式取代")

    def test_tick_during_judgment_not_lost(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        write_transcript(self.proj, sid(2), [human("做 Y", self.t, cwd=str(self.workdir))])
        outer = self

        class TickWhileJudging(FakeJudge):
            def judge(self, request):
                outer.toggle("page_name", 1, "x")  # 判断跑着的时候用户打了勾
                return super().judge(request)

        judge = TickWhileJudging()
        self.run_once(judge)
        self.assertEqual([q["sid"] for q in judge.requests], [sid(2)])
        self.assertNotIn(sid(1), self.page())
        self.assertIn(sid(1), self.closed_page())

    def test_work_items_hidden_everywhere(self):
        write_transcript(self.proj, sid(1), [human("个人工具", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(2), [human("固件需求", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(3), [human("老的固件需求", NOW - 20 * DAY, cwd=str(self.workdir))])
        judge = FakeJudge({sid(1): {"status": "悬置", "title": "工具事", "stopped_at": "s", "owner": "个人"},
                           sid(2): {"status": "悬置", "title": "固件事", "stopped_at": "s", "owner": "公司"},
                           sid(3): {"status": "悬置", "title": "老固件事", "stopped_at": "s", "owner": "公司"}})
        r = self.run_once(judge)
        self.assertIn("工具事", self.page())
        self.assertNotIn("固件事", self.page())
        self.assertEqual((r["hidden_work"], r["archive_files"]), (2, 0), "公司工作连归档也不进")
        req = judge.requests[0]
        self.assertEqual(req["hints"]["cwd"], str(self.workdir), "归属线索随请求一起给判断器")

    def test_owner_backfill_only_missing(self):
        write_transcript(self.proj, sid(1), [human("A", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(2), [human("B", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        st = store.load_state(self.state)
        del st["sessions"]["claude:" + sid(1)]["verdict"]["owner"]          # 模拟升级前判过、没有归属的条目
        store.save_state(self.state, st)
        judge = FakeJudge(owners={sid(1): "公司"})
        r = self.run_once(judge)
        self.assertEqual([(q["sid"], q["mode"]) for q in judge.requests], [(sid(1), "owner")])
        self.assertEqual(judge.requests[0]["title"], "默认标题", "补判的材料是已有结论")
        self.assertEqual((r["calls"], r["owner_planned"], r["hidden_work"]), (1, 1, 1))
        self.assertNotIn(sid(1), self.page())
        judge2 = FakeJudge()
        self.run_once(judge2)
        self.assertEqual(judge2.requests, [], "补过就不再补")

    def test_owner_backfill_failure_follows_retry_rules(self):
        write_transcript(self.proj, sid(1), [human("A", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        st = store.load_state(self.state)
        del st["sessions"]["claude:" + sid(1)]["verdict"]["owner"]
        store.save_state(self.state, st)
        for _ in range(3):
            self.run_once(FakeJudge(owners={sid(1): "乱写"}))
        judge = FakeJudge()
        self.run_once(judge)
        self.assertEqual(judge.requests, [], "补判连败 3 次后不再占名额")
        self.assertIn(sid(1), self.page(), "补不出归属的照常显示")

    def archive_dir(self):
        return self.out / self.cfg["archive_dir"]

    def archive_files(self):
        d = self.archive_dir()
        return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []

    def toggle_archive(self, n, mark):
        for p in self.archive_dir().glob("*.md"):
            text = p.read_text(encoding="utf-8")
            if f"%%ss:claude:{sid(n)}@" in text:
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if f"%%ss:claude:{sid(n)}@" in line:
                        lines[i] = line.replace("[ ]", f"[{mark}]", 1).replace("[x]", f"[{mark}]", 1)
                p.write_text("\n".join(lines), encoding="utf-8")

    def test_old_items_go_to_weekly_archive(self):
        old = NOW - 10 * DAY
        write_transcript(self.proj, sid(1), [human("新事", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(2), [human("旧事", old, cwd=str(self.workdir))])
        r = self.run_once(FakeJudge({sid(2): {"status": "悬置", "title": "旧事", "stopped_at": "s"}}))
        name = core.archive_name(core.week_start(old))
        self.assertEqual(self.archive_files(), [name])
        self.assertEqual((r["listed"], r["archived"]), (1, 1))
        self.assertNotIn(sid(2), self.page())
        self.assertIn(f"[[归档/{name[:-3]}|", self.page(), "主清单链到各周归档")
        text = (self.archive_dir() / name).read_text(encoding="utf-8")
        self.assertIn("- [ ] **旧事**", text)
        self.assertIn(f"claude --resume {sid(2)}", text, "原会话还在就给恢复命令")

    def test_archive_tick_and_untick(self):
        old = NOW - 10 * DAY
        write_transcript(self.proj, sid(1), [human("旧事", old, cwd=str(self.workdir))])
        self.run_once(FakeJudge({sid(1): {"status": "悬置", "title": "旧事", "stopped_at": "s"}}))
        name = self.archive_files()[0]
        self.toggle_archive(1, "x")
        r = self.run_once(FakeJudge())
        self.assertEqual(r["closed"], 1)
        text = (self.archive_dir() / name).read_text(encoding="utf-8")
        self.assertIn("## 已处理", text)
        self.assertIn("- [x] **旧事**", text)
        self.toggle_archive(1, " ")
        r = self.run_once(FakeJudge())
        self.assertEqual(r["closed"], 0)
        self.assertIn("- [ ] **旧事**", (self.archive_dir() / name).read_text(encoding="utf-8"))

    def test_emptied_archive_removed_but_user_files_kept(self):
        old = NOW - 10 * DAY
        p = write_transcript(self.proj, sid(1), [human("旧事", old, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        name = self.archive_files()[0]
        mine = self.archive_dir() / "我自己的笔记.md"
        mine.write_text("# 随手记\n- [x] 跟脚本无关\n", encoding="utf-8")
        rows = [human("旧事", old, cwd=str(self.workdir)), human("又聊了，回到窗口里", self.t, cwd=str(self.workdir))]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        r = self.run_once(FakeJudge())
        self.assertNotIn(name, self.archive_files(), "那一周空了，生成页删掉")
        self.assertTrue(mine.exists(), "用户自己放的文件不碰")
        self.assertEqual(r["archive_removed"], 1)
        self.assertIn(sid(1), self.page())

    def test_archive_tick_during_judgment_not_lost(self):
        old = NOW - 10 * DAY
        write_transcript(self.proj, sid(1), [human("旧事", old, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        write_transcript(self.proj, sid(2), [human("新来的", self.t, cwd=str(self.workdir))])
        outer = self

        class TickWhileJudging(FakeJudge):
            def judge(self, request):
                outer.toggle_archive(1, "x")
                return super().judge(request)

        r = self.run_once(TickWhileJudging())
        self.assertEqual(r["closed"], 1)

    def test_no_judge_also_applies_ticks(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge())
        self.toggle("page_name", 1, "x")
        self.run_once(FakeJudge(), no_judge=True)
        self.assertNotIn(sid(1), self.page())

    def test_failures_are_listed_not_hidden(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        write_transcript(self.proj, sid(2), [human("目录没了", self.t, cwd=str(Path(self.tmp.name) / "deleted"))],
                         project="C--other")
        judge = FakeJudge(fail={sid(1): JudgeError("超时")})
        r = self.run_once(judge)
        self.assertEqual(r["calls"], 1, "目录不存在的不消耗调用")
        page = self.page()
        self.assertIn("判断失败（下次重试）", page)
        self.assertIn("判断失败（不会自动重试）", page)
        judge2 = FakeJudge()
        self.run_once(judge2)
        self.assertEqual([q["sid"] for q in judge2.requests], [sid(1)], "可重试的下次再判，不可重试的不再判")

    def test_persistent_failure_stops_after_three_and_resets_on_new_conversation(self):
        p = write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        for _ in range(3):
            judge = FakeJudge({sid(1): {"status": "schema 一直不合格"}})
            self.run_once(judge)
            self.assertEqual(len(judge.requests), 1)
        self.assertIn("判断失败（不会自动重试）", self.page())
        judge = FakeJudge()
        self.run_once(judge)
        self.assertEqual(judge.requests, [], "第 3 次失败后不再占调用名额")
        rows = [human("做 X", self.t, cwd=str(self.workdir)), human("又聊了", self.t + DAY, cwd=str(self.workdir))]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        judge = FakeJudge(fail={sid(1): JudgeError("超时")})
        self.run_once(judge)
        self.assertEqual(len(judge.requests), 1, "有新对话后重新判断")
        err = store.load_state(self.state)["sessions"]["claude:" + sid(1)]["error"]
        self.assertEqual((err["count"], err["retry"]), (1, True))
        self.assertIn("判断失败（下次重试）", self.page())

    def test_bad_verdict_is_retryable_failure(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge({sid(1): {"status": "乱写"}}))
        self.assertIn("判断失败（下次重试）", self.page())

    def test_max_calls_and_queue(self):
        for n in range(1, 5):
            write_transcript(self.proj, sid(n), [human(f"事 {n}", self.t + n, cwd=str(self.workdir))])
        judge = FakeJudge()
        r = self.run_once(judge, max_calls=2)
        self.assertEqual(r["calls"], 2)
        self.assertEqual(r["queued"], 2)
        self.assertIn("排队待判断 2 个会话", self.page())

    def test_no_judge(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        judge = FakeJudge()
        r = self.run_once(judge, no_judge=True)
        self.assertEqual((judge.requests, r["calls"], r["queued"]), ([], 0, 1))

    def test_gone_session_kept(self):
        p = write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])
        self.run_once(FakeJudge({sid(1): {"status": "悬置", "title": "X 事", "stopped_at": "s"}}))
        p.unlink()
        self.run_once(FakeJudge())
        page = self.page()
        self.assertIn("X 事", page)
        self.assertIn("无法恢复", page)

    def test_discard_when_session_changes_during_judgment(self):
        p = write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir))])

        class Racing(FakeJudge):
            def judge(self, request):
                rows = [human("做 X", NOW - 5 * DAY, cwd=request["cwd"]), human("新话", NOW - 3 * DAY, cwd=request["cwd"])]
                p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
                return super().judge(request)

        r = self.run_once(Racing())
        self.assertEqual(r["discarded"], 1)
        self.assertIsNone(store.load_state(self.state)["sessions"]["claude:" + sid(1)].get("verdict"))

    def test_doc_probe_and_unavailable(self):
        write_transcript(self.proj, sid(1), [human("做 X", self.t, cwd=str(self.workdir)),
                                             assistant("写了", self.t + 1, tool=("t1", "C:/w/tool/a.py")),
                                             {"type": "user", "timestamp": "2020-01-01T00:00:00Z", "message": {"content": [
                                                 {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}])
        gate = FakeGate([("stale", {"root": "C:/w/tool"}, [])])
        self.run_once(FakeJudge(), gate=gate)
        self.assertEqual(gate.seen, [["C:/w/tool/a.py"]])
        self.assertIn("`C:/w/tool`：BLUEPRINT 和 CHANGELOG 都比代码旧", self.page())

        def broken():
            raise FileNotFoundError("没装 vibe-flow")
        cli.run_sweep(self.cfg, FakeJudge(), broken, str(self.out), self.state, now=NOW, log=lambda *_: None)
        self.assertIn("文档探测不可用：FileNotFoundError: 没装 vibe-flow", self.page())

    def test_newest_per_dir(self):
        times = {"C:/a/x.py": 1, "C:/a/y.py": 5, "C:/b/z.py": 2}

        def mtime(p):
            if p not in times:
                raise OSError
            return times[p]
        got = docgate.newest_per_dir(["C:/a/x.py", "C:/a/y.py", "C:/b/z.py", "C:/c/deleted.py"], mtime)
        self.assertEqual(sorted(got), ["C:/a/y.py", "C:/b/z.py", "C:/c/deleted.py"])

    def test_lock_prevents_overlap(self):
        with store.RunLock(self.state):
            with self.assertRaises(RuntimeError):
                self.run_once(FakeJudge())


class ClaudeJudgeTest(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg(tempfile.gettempdir())

    def test_isolation_flags(self):
        req = core.make_request(sid(1), {"cwd": "C:/x", "recap": {"text": "r"}, "last_human": "h", "last_assistant": "a"},
                                "recap")
        argv, prompt = claude_judge.build_command(self.cfg, req)
        for flag in ("-p", "--safe-mode", "--no-session-persistence", "--json-schema"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "haiku")
        self.assertNotIn("--resume", argv)
        self.assertIn("r", prompt)
        req["mode"] = "resume"
        argv, prompt = claude_judge.build_command(self.cfg, req)
        self.assertEqual(argv[argv.index("--resume") + 1], sid(1))
        self.assertIn("--fork-session", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertNotIn("--bare", argv)

    def test_owner_mode_and_hints_in_prompt(self):
        summary = {"cwd": "C:/fw", "written": ["C:/x/my-repos/t.py"]}
        hints = core.owner_hints(summary, self.cfg["personal_paths"])
        req = core.make_request(sid(1), summary, "owner", verdict={"title": "某事", "stopped_at": "等你"}, hints=hints)
        argv, prompt = claude_judge.build_command(self.cfg, req)
        schema = json.loads(argv[argv.index("--json-schema") + 1])
        self.assertEqual(schema["required"], ["owner"])
        self.assertEqual(argv[argv.index("--model") + 1], "haiku", "补判归属只用 Haiku")
        self.assertNotIn("--resume", argv)
        for text in ("个人工具", "公司固件", "某事", "等你", "C:/fw", "C:/x/my-repos/t.py"):
            self.assertIn(text, prompt)
        req = core.make_request(sid(1), {"cwd": "C:/fw", "recap": {"text": "r"}}, "recap", hints=hints)
        argv, prompt = claude_judge.build_command(self.cfg, req)
        self.assertIn("owner", json.loads(argv[argv.index("--json-schema") + 1])["required"])
        self.assertIn("公司固件", prompt)

    def test_model_follows_config(self):
        self.cfg["recap_model"] = "test-model-x"
        req = core.make_request(sid(1), {"cwd": "C:/x"}, "recap")
        argv, _ = claude_judge.build_command(self.cfg, req)
        self.assertEqual(argv[argv.index("--model") + 1], "test-model-x")

    def test_parse_output(self):
        out = {"type": "result", "is_error": False, "structured_output": {"status": "完成", "title": "t", "stopped_at": "s"}}
        self.assertEqual(claude_judge.parse_output(json.dumps(out))["status"], "完成")
        out = {"type": "result", "result": json.dumps({"status": "悬置", "title": "t", "stopped_at": "s"})}
        self.assertEqual(claude_judge.parse_output(json.dumps(out))["status"], "悬置")
        with self.assertRaises(JudgeError):
            claude_judge.parse_output(json.dumps({"is_error": True, "result": "budget"}))
        with self.assertRaises(JudgeError):
            claude_judge.parse_output("not json")

    def test_resume_without_cwd_is_not_retryable(self):
        j = claude_judge.ClaudeJudge(self.cfg, scratch_dir=tempfile.gettempdir())
        with self.assertRaises(JudgeError) as cm:
            j.judge({"sid": sid(1), "mode": "resume", "cwd": "Z:/definitely/missing"})
        self.assertFalse(cm.exception.retry)


class ConfigAndTaskTest(unittest.TestCase):
    def test_shipped_config_loads(self):
        cfg = config.load(ROOT / "sweep.toml")
        self.assertTrue(cfg["vault_dir"].endswith("03-悬着的事"))
        self.assertNotIn("state_dir", cfg)
        self.assertIn("claude", cfg["history_roots"])
        self.assertEqual(cfg["window_days"], 7)
        self.assertEqual(cfg["archive_dir"], "归档")
        self.assertTrue(cfg["personal_desc"] and cfg["work_desc"])
        self.assertFalse(any(p.startswith("~") for p in cfg["personal_paths"]), "~ 已展开")
        self.assertIn("my-repos", cfg["personal_paths"])

    def test_missing_required_key_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            text = (ROOT / "sweep.toml").read_text(encoding="utf-8").replace('recap_model = "haiku"', "")
            p.write_text(text, encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load(p)

    def test_since_parsed_and_validated(self):
        cfg = config.load(ROOT / "sweep.toml")
        self.assertIsInstance(cfg["since_ts"], float)
        self.assertIsNone(config.parse_since(""))
        with self.assertRaises(config.ConfigError):
            config.parse_since("9月1日")

    def test_task_script(self):
        with tempfile.TemporaryDirectory() as temporary:
            stable = Path(temporary) / "stable"
            (stable / "scripts").mkdir(parents=True)
            (stable / "scripts/pstate.py").touch()
            launcher = stable / "run_hidden.pyw"
            launcher.touch()
            s = schtask.install_script("session-sweep", 4, launcher, schtask.default_pythonw())
        self.assertIn("MultipleInstances IgnoreNew", s)
        self.assertIn("-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries", s)
        self.assertIn("-AtLogOn", s)
        self.assertIn("New-TimeSpan -Hours 4", s)
        self.assertIn("run_hidden.pyw", s)
        self.assertIn("Unregister-ScheduledTask", schtask.uninstall_script("session-sweep"))


if __name__ == "__main__":
    unittest.main()