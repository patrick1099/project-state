import unittest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pstate_sweep import core
from sweep_helpers import summarize_rows, DAY, NOW, assistant, human, recap, sid, tool_result


def entry(rows, **extra):
    e = {"summary": summarize_rows(rows), "gone": False}
    e.update(extra)
    return e


class SummarizeTest(unittest.TestCase):
    def test_basic_fields(self):
        t = NOW - 3 * DAY
        rows = [human("帮我改 A", t), assistant("改好了", t + 10, tool=("t1", "C:/p/a.py")),
                tool_result("t1", t + 11), assistant("还有 B 等你定", t + 12)]
        s = summarize_rows(rows)
        self.assertEqual(s["cwd"], "C:/work/proj")
        self.assertEqual(s["entrypoint"], "cli")
        self.assertEqual(s["human_count"], 1)
        self.assertEqual(s["last_human"], "帮我改 A")
        self.assertEqual(s["last_assistant"], "还有 B 等你定")
        self.assertEqual(s["written"], ["C:/p/a.py"])
        self.assertAlmostEqual(s["last_ts"], t + 12, places=0)

    def test_failed_write_not_counted(self):
        t = NOW - 3 * DAY
        rows = [human("x", t), assistant("y", t + 1, tool=("t1", "C:/p/a.py")), tool_result("t1", t + 2, error=True)]
        self.assertEqual(summarize_rows(rows)["written"], [])

    def test_tool_results_and_meta_are_not_human(self):
        t = NOW - 3 * DAY
        meta = {"type": "user", "isMeta": True, "message": {"content": "skill text"}, "timestamp": "x"}
        notif = {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": "done"}}
        legacy_cmd = {"type": "user", "message": {"content": "<command-name>/x</command-name>"}}
        rows = [human("真的话", t), tool_result("t9", t + 1), meta, notif, legacy_cmd]
        self.assertEqual(summarize_rows(rows)["human_count"], 1)

    def test_recap_suffix_stripped_and_fresh(self):
        t = NOW - 3 * DAY
        rows = [human("x", t), assistant("y", t + 5), recap("停在等你点头", t + 200)]
        s = summarize_rows(rows)
        self.assertEqual(s["recap"]["text"], "停在等你点头")
        self.assertTrue(core.recap_is_fresh(s))

    def test_recap_stale_when_assistant_continued(self):
        t = NOW - 3 * DAY
        rows = [human("x", t), recap("旧总结", t + 100), assistant("后来又干完了", t + 300)]
        self.assertFalse(core.recap_is_fresh(summarize_rows(rows)))

    def test_recap_stale_when_tool_result_after(self):
        t = NOW - 3 * DAY
        rows = [human("x", t), assistant("y", t + 1, tool=("t1", "a")), recap("r", t + 100), tool_result("t1", t + 200)]
        self.assertFalse(core.recap_is_fresh(summarize_rows(rows)))


class CandidateTest(unittest.TestCase):
    def test_idle_threshold_and_entrypoint(self):
        eps = ["cli", "claude-vscode"]
        old = summarize_rows([human("x", NOW - 3 * DAY)])
        fresh = summarize_rows([human("x", NOW - 1 * DAY)])
        sdk = summarize_rows([human("x", NOW - 3 * DAY, entry="sdk-cli")])
        ancient = summarize_rows([human("x", NOW - 400 * DAY)])
        self.assertTrue(core.is_candidate(old, NOW, 2, eps))
        self.assertFalse(core.is_candidate(fresh, NOW, 2, eps))
        self.assertFalse(core.is_candidate(sdk, NOW, 2, eps))
        self.assertTrue(core.is_candidate(ancient, NOW, 2, eps), "没有上限：越久越容易忘")

    def test_since_floor(self):
        eps = ["cli"]
        before = summarize_rows([human("x", NOW - 40 * DAY)])
        after = summarize_rows([human("x", NOW - 10 * DAY)])
        floor = NOW - 20 * DAY
        self.assertFalse(core.is_candidate(before, NOW, 2, eps, floor))
        self.assertTrue(core.is_candidate(after, NOW, 2, eps, floor))
        self.assertTrue(core.is_candidate(before, NOW, 2, eps, None))

    def test_since_floor_hides_gone_and_skips_plan(self):
        floor = NOW - 20 * DAY
        sessions = {sid(1): entry([human("a", NOW - 40 * DAY)]), sid(2): entry([human("b", NOW - 10 * DAY)])}
        sessions[sid(1)]["gone"] = True
        plan = core.plan_judgments(sessions, NOW, 2, ["cli"], since_ts=floor)
        self.assertEqual([s for s, _ in plan], [sid(2)])
        items, queued, hidden = core.build_items(sessions, {}, NOW, 2, ["cli"], floor)
        self.assertEqual((items, queued, hidden), ([], 1, 0))

    def test_single_message_counts(self):
        s = summarize_rows([human("一句话的大任务", NOW - 5 * DAY)])
        self.assertTrue(core.is_candidate(s, NOW, 2, ["cli"]))


class PlanTest(unittest.TestCase):
    def test_order_recap_first_then_newest(self):
        t = NOW - 10 * DAY
        sessions = {
            sid(1): entry([human("a", t)]),                                   # resume, 旧
            sid(2): entry([human("b", t + DAY)]),                              # resume, 新
            sid(3): entry([human("c", t), recap("r", t + 10)]),                # recap
        }
        plan = core.plan_judgments(sessions, NOW, 2, ["cli"])
        self.assertEqual(plan, [(sid(3), "recap"), (sid(2), "resume"), (sid(1), "resume")])

    def test_skip_judged_same_version_and_rejudge_new_version(self):
        rows = [human("a", NOW - 5 * DAY)]
        e = entry(rows)
        e["verdict"] = {"status": "悬置", "owner": "个人", "version": e["summary"]["version"]}
        self.assertEqual(core.plan_judgments({sid(1): e}, NOW, 2, ["cli"]), [])
        e["summary"] = summarize_rows(rows + [human("b", NOW - 3 * DAY)])
        self.assertEqual(len(core.plan_judgments({sid(1): e}, NOW, 2, ["cli"])), 1)

    def test_error_retry_flag(self):
        e = entry([human("a", NOW - 5 * DAY)])
        v = e["summary"]["version"]
        e["error"] = {"version": v, "retry": True}
        self.assertEqual(len(core.plan_judgments({sid(1): e}, NOW, 2, ["cli"])), 1)
        e["error"] = {"version": v, "retry": False}
        self.assertEqual(core.plan_judgments({sid(1): e}, NOW, 2, ["cli"]), [])

    def test_owner_backfill_only_for_shown_without_owner(self):
        def judged(n, status, **extra):
            e = entry([human(f"事 {n}", NOW - 5 * DAY)])
            e["verdict"] = {"status": status, "title": f"T{n}", "stopped_at": "s", "version": e["summary"]["version"],
                            **extra}
            return e
        sessions = {sid(1): judged(1, "悬置"),                      # 缺归属、会显示 → 补判
                    sid(2): judged(2, "不确定", owner="说不清"),      # 已有归属 → 不补
                    sid(3): judged(3, "完成"),                      # 不显示 → 不补
                    sid(4): judged(4, "悬置")}                      # 缺归属，但补判已不可重试 → 不补
        sessions[sid(4)]["owner_error"] = {"version": sessions[sid(4)]["summary"]["version"], "retry": False}
        sessions[sid(5)] = judged(5, "悬置")                        # 已被清理也能补（不需要原会话）
        sessions[sid(5)]["gone"] = True
        plan = core.plan_judgments(sessions, NOW, 2, ["cli"])
        self.assertEqual(sorted(plan), [(sid(1), "owner"), (sid(5), "owner")])

    def test_owner_mode_runs_first(self):
        a = entry([human("a", NOW - 3 * DAY)])                       # 没判过 → resume
        b = entry([human("b", NOW - 9 * DAY)])
        b["verdict"] = {"status": "悬置", "title": "B", "stopped_at": "", "version": b["summary"]["version"]}
        self.assertEqual(core.plan_judgments({sid(1): a, sid(2): b}, NOW, 2, ["cli"]),
                         [(sid(2), "owner"), (sid(1), "resume")])

    def test_owner_hints(self):
        s = summarize_rows([human("x", NOW - 3 * DAY, cwd="C:\\Work\\Fw"),
                                 assistant("y", NOW - 3 * DAY + 1, tool=("t1", "C:\\Users\\u\\Desktop\\my-repos\\tool\\a.py")),
                                 tool_result("t1", NOW - 3 * DAY + 2)])
        h = core.owner_hints(s, ["my-repos", "treasury-vault"])
        self.assertEqual(h["cwd"], "C:\\Work\\Fw")
        self.assertEqual(h["personal_hits"], ["C:\\Users\\u\\Desktop\\my-repos\\tool\\a.py"])

    def test_gone_not_planned_and_only_mode(self):
        e = entry([human("a", NOW - 5 * DAY)], gone=True)
        self.assertEqual(core.plan_judgments({sid(1): e}, NOW, 2, ["cli"]), [])
        e2 = entry([human("a", NOW - 5 * DAY)])
        self.assertEqual(core.plan_judgments({sid(2): e2}, NOW, 2, ["cli"], only_mode="recap"), [])


class VerdictTest(unittest.TestCase):
    def test_clean(self):
        v = core.clean_verdict({"status": "悬置", "title": "x" * 100, "stopped_at": "y"})
        self.assertEqual(len(v["title"]), 41)
        self.assertNotIn("owner", v, "缺归属不算失败，留给下次补判")
        self.assertEqual(core.clean_verdict({"status": "悬置", "owner": "公司"})["owner"], "公司")
        with self.assertRaises(ValueError):
            core.clean_verdict({"status": "悬置", "owner": "别人的"})
        with self.assertRaises(ValueError):
            core.clean_owner({"owner": None})
        with self.assertRaises(ValueError):
            core.clean_verdict({"status": "进行中"})
        with self.assertRaises(ValueError):
            core.clean_verdict("not a dict")

    def test_record_failure_caps_retries_per_version(self):
        e = core.record_failure(None, "m", "v1", True, 3, 0)
        self.assertEqual((e["count"], e["retry"]), (1, True))
        e = core.record_failure(e, "m", "v1", True, 3, 0)
        self.assertEqual((e["count"], e["retry"]), (2, True))
        e = core.record_failure(e, "m", "v1", True, 3, 0)
        self.assertEqual((e["count"], e["retry"]), (3, False))
        e = core.record_failure(e, "m", "v2", True, 3, 0)
        self.assertEqual((e["count"], e["retry"]), (1, True), "版本变了计数清零")
        self.assertFalse(core.record_failure(None, "m", "v1", False, 3, 0)["retry"], "不可重试的错误第一次就停")


class ClosedTest(unittest.TestCase):
    V = "2026-10-01T01:02:03.000Z"

    def test_legacy_record(self):
        closed = core.parse_legacy_closed(f"随便写点\n- 关闭 {sid(7)} @ {self.V}  这个不用管了\n")
        self.assertEqual(closed, {core.closure_key(sid(7), self.V)})

    def test_parse_marks_ignores_title_edits(self):
        text = (f"- [x] **被用户改过的标题** %%ss:{sid(1)}@{self.V}%%\n"
                f"    - 状态：悬置\n"
                f"- [ ] **另一条** %%ss:{sid(2)}@{self.V}%%\n"
                f"  * [X] 缩进的也认 %%ss:{sid(3)}@{self.V}%%\n"
                f"- [x] 没有标记的行不认\n")
        self.assertEqual(core.parse_marks(text), {core.closure_key(sid(1), self.V): True,
                                                  core.closure_key(sid(2), self.V): False,
                                                  core.closure_key(sid(3), self.V): True})

    def test_merge(self):
        k1, k2, k3 = (core.closure_key(sid(n), self.V) for n in (1, 2, 3))
        out = core.merge_closures({k2: {"at": 1}}, {k1: True, k3: False}, {k2: False}, set(), 9)
        self.assertEqual(out, {k1: {"at": 9}}, "清单页勾上=关闭；已处理页取消=恢复")
        out = core.merge_closures({k1: {"at": 1}}, {k1: True}, {k1: False}, set(), 9)
        self.assertEqual(out, {}, "两边矛盾时以已处理页的取消为准")
        out = core.merge_closures({k1: {"at": 1}}, {k1: True}, {}, {k2}, 9)
        self.assertEqual(out, {k1: {"at": 1}, k2: {"at": 9}}, "已关的保留原关闭时间；旧格式记录迁入")
        out = core.merge_closures({k2: {"at": 1}}, {}, {}, set(), 9, archive_marks={k1: True, k2: False, k3: False})
        self.assertEqual(out, {k1: {"at": 9}}, "归档页勾上=关闭，取消=恢复")

    def test_effective_drops_changed_version(self):
        e = entry([human("x", NOW - 5 * DAY)])
        v = e["summary"]["version"]
        closures = {core.closure_key(sid(1), v): {"at": 1}, core.closure_key(sid(1), "旧版本"): {"at": 1},
                    core.closure_key(sid(9), v): {"at": 1}}
        kept, pairs = core.effective_closures(closures, {sid(1): e})
        self.assertEqual(list(kept), [core.closure_key(sid(1), v)])
        self.assertEqual(pairs, {(sid(1), v)})

    def test_closed_page_roundtrip(self):
        e = entry([human("第一件事", NOW - 5 * DAY, cwd="C:/w/alpha")])
        v = e["summary"]["version"]
        e["verdict"] = {"status": "悬置", "title": "A\n事", "stopped_at": "", "version": v}
        items, _, _ = core.build_items({sid(1): e}, {core.closure_key(sid(1), v): {"at": NOW}}, NOW, 2, ["cli"])
        pending, closed, archives = core.layout(items, NOW, 7)
        self.assertEqual((len(pending), len(closed), archives), (0, 1, {}))
        page = core.render_closed_page(closed, "悬着的事.md", 7)
        self.assertIn("- [x] **A 事** · alpha", page)
        self.assertIn(f"claude --resume {sid(1)}", page)
        self.assertEqual(core.parse_marks(page), {core.closure_key(sid(1), v): True})
        self.assertIn("最近没有关闭的条目", core.render_closed_page([], "悬着的事.md", 7))


class ItemsAndPageTest(unittest.TestCase):
    NAMES = {"closed": "悬着的事-已处理.md", "archive_dir": "归档"}

    def setUp(self):
        self.t = NOW - 4 * DAY
        a = entry([human("第一件事", self.t, cwd="C:/w/alpha")])
        a["verdict"] = {"status": "悬置", "title": "A 事", "stopped_at": "等你点头", "version": a["summary"]["version"],
                        "owner": "个人"}
        b = entry([human("第二件", self.t, cwd="C:/w/beta")])
        b["verdict"] = {"status": "完成", "title": "B", "stopped_at": "", "version": b["summary"]["version"]}
        c = entry([human("第三件", self.t, cwd="C:/w/beta")])
        c["error"] = {"msg": "超时", "version": c["summary"]["version"], "retry": True}
        d = entry([human("第四件", self.t, cwd="C:/w/beta")])                      # 未判断 → 排队
        g = entry([human("被清理的", self.t, cwd="C:/w/gone")], gone=True)
        g["verdict"] = {"status": "不确定", "title": "G", "stopped_at": "?", "version": g["summary"]["version"],
                        "owner": "说不清"}
        w = entry([human("固件的事", self.t, cwd="C:/w/fw")])
        w["verdict"] = {"status": "悬置", "title": "W", "stopped_at": "", "version": w["summary"]["version"],
                        "owner": "公司"}
        self.sessions = {sid(1): a, sid(2): b, sid(3): c, sid(4): d, sid(5): g, sid(6): w}

    def test_items(self):
        items, queued, hidden = core.build_items(self.sessions, {}, NOW, 2, ["cli"])
        states = {i["sid"]: i["state"] for i in items}
        self.assertEqual(states, {sid(1): "悬置", sid(3): "判断失败（下次重试）", sid(5): "待确认"})
        self.assertEqual((queued, hidden), (1, 1), "公司工作不进任何页面，只计数")

    def test_closed_only_that_version(self):
        v = self.sessions[sid(1)]["summary"]["version"]
        items, _, _ = core.build_items(self.sessions, {core.closure_key(sid(1), v): {"at": NOW}}, NOW, 2, ["cli"])
        self.assertTrue(next(i for i in items if i["sid"] == sid(1))["closed"])
        items, _, _ = core.build_items(self.sessions, {core.closure_key(sid(1), "其他版本"): {"at": NOW}},
                                       NOW, 2, ["cli"])
        self.assertFalse(next(i for i in items if i["sid"] == sid(1))["closed"])

    def test_page(self):
        items, queued, _ = core.build_items(self.sessions, {}, NOW, 2, ["cli"])
        pending, closed, archives = core.layout(items, NOW, 7)
        docs = [{"root": "C:/w/tool", "text": "BLUEPRINT 和 CHANGELOG 都比代码旧"}]
        page = core.render_page(pending, queued, len(closed), archives, docs, None, self.NAMES, 7)
        self.assertIn(f"## {core._day_label(self.t)}", page)
        v1 = self.sessions[sid(1)]["summary"]["version"]
        self.assertIn(f"- [ ] **A 事** · alpha %%ss:{sid(1)}@{v1}%%", page)
        self.assertEqual(core.parse_marks(page)[core.closure_key(sid(1), v1)], False)
        self.assertIn(f'claude --resume {sid(1)}', page)
        self.assertIn("原文不可用，无法恢复", page)
        self.assertNotIn(f"claude --resume {sid(5)}", page)
        self.assertIn("待确认 · 归属说不清", page)
        self.assertNotIn("固件的事", page)
        self.assertIn("排队待判断 1 个会话", page)
        self.assertIn("## 疑似文档未同步", page)
        self.assertIn("[[悬着的事-已处理]]", page)
        self.assertEqual(page, core.render_page(pending, queued, len(closed), archives, docs, None, self.NAMES, 7),
                         "同样输入页面字节不变")

    def test_empty_page(self):
        self.assertIn("最近没有悬着的事", core.render_page([], 0, 0, {}, [], None, self.NAMES, 7))


class LayoutTest(unittest.TestCase):
    NAMES = {"closed": "悬着的事-已处理.md", "archive_dir": "归档"}

    def item(self, n, days_ago, closed=False):
        ts = NOW - days_ago * DAY
        return {"sid": sid(n), "version": f"v{n}", "title": f"事{n}", "state": "悬置", "stopped_at": "",
                "cwd": "C:/w/p", "branch": None, "last_ts": ts, "gone": False, "owner": "个人",
                "closed": closed, "closed_at": NOW if closed else 0}

    def test_window_and_weeks(self):
        items = [self.item(1, 3), self.item(2, 5, closed=True), self.item(3, 10), self.item(4, 11, closed=True),
                 self.item(5, 30)]
        pending, closed, archives = core.layout(items, NOW, 7)
        self.assertEqual([i["sid"] for i in pending], [sid(1)])
        self.assertEqual([i["sid"] for i in closed], [sid(2)])
        by_sid = {i["sid"]: monday for monday, g in archives.items() for i in g}
        self.assertEqual(set(by_sid), {sid(3), sid(4), sid(5)}, "超过窗口的关没关都归档")
        for s, monday in by_sid.items():
            self.assertEqual(monday.weekday(), 0, "按周一分组")
        self.assertEqual(by_sid[sid(5)], core.week_start(NOW - 30 * DAY))

    def test_archive_name_and_page(self):
        from datetime import date
        monday = date(2026, 9, 29)
        self.assertEqual(core.archive_name(monday), "2026-09-29~10-05.md")
        group = [self.item(3, 10), self.item(4, 11, closed=True)]
        page = core.render_archive_page(monday, group, "悬着的事.md")
        self.assertTrue(core.is_generated_archive(page))
        marks = core.parse_marks(page)
        self.assertEqual(marks, {core.closure_key(sid(3), "v3"): False, core.closure_key(sid(4), "v4"): True})
        self.assertLess(page.index("## 没处理的"), page.index("## 已处理"))
        self.assertFalse(core.is_generated_archive("# 我自己写的笔记"))

    def test_page_groups_by_day_newest_first_and_links_archives(self):
        items = [self.item(1, 3), self.item(2, 2.5), self.item(3, 6), self.item(4, 10), self.item(5, 11, closed=True)]
        pending, closed, archives = core.layout(items, NOW, 7)
        page = core.render_page(pending, 0, len(closed), archives, [], None, self.NAMES, 7)
        days = [core._day_label(NOW - d * DAY) for d in (2.5, 3, 6)]
        positions = [page.index(f"## {d}") for d in dict.fromkeys(days)]
        self.assertEqual(positions, sorted(positions), "最新的一天在上")
        monday = core.week_start(NOW - 10 * DAY)
        self.assertIn(f"[[归档/{core.archive_name(monday)[:-3]}|", page)
        if core.week_start(NOW - 11 * DAY) == monday:   # 两条落在同一周（本机时区下成立）
            self.assertIn("未处理 1 · 已处理 1", page)


if __name__ == "__main__":
    unittest.main()