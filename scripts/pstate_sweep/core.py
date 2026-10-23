"""纯逻辑：对话记录摘要、候选判定、判断计划、关闭记录解析、页面渲染。

本模块不读写文件、不起子进程、不打印；输入显式传入，结果返回。
"""
import re
from datetime import datetime, timedelta

STATUSES = ("悬置", "完成", "放弃", "不确定")
SHOWN_STATUSES = {"悬置", "不确定"}
STATUS_LABEL = {"悬置": "悬置", "不确定": "待确认"}
OWNERS = ("个人", "公司", "说不清")
HIDDEN_OWNER = "公司"   # 公司工作用户自己记得住，所有页面都不显示
WEEKDAYS = "一二三四五六日"
# 脚本生成的归档页带这个标记；只有带标记的文件才会被读勾选、被删除，用户自己放的文件不碰
ARCHIVE_MARK = "%%session-sweep 自动生成的归档页%%"
MAX_HINT_PATHS = 20
RECAP_SUFFIX = re.compile(r"\s*\(disable recaps in /config\)\s*$")
SID_RE = r"[A-Za-z0-9_.:-]+"
# 旧版「关闭 <sid> @ <版本>」粘贴记录，只为迁移识别
CLOSE_RECORD = re.compile(rf"({SID_RE})\s*@\s*(\S+)")
# 每条复选框行末尾的隐藏标记：Obsidian 的 %%注释%% 不显示，用户改标题也不影响对应关系
CHECK_LINE = re.compile(rf"^[ \t]*[-*+][ \t]+\[([^\]])\].*?%%ss:({SID_RE})@([^%\s]+)%%", re.M)
TEXT_LIMIT = 1500


# ---------- 时间 ----------

def parse_ts(stamp):
    """transcript 里的 ISO 时间串 → epoch 秒；拿不到返回 None。"""
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fmt_local(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _clip(text, limit=TEXT_LIMIT):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def recap_is_fresh(summary):
    """recap 写在最后一条有效对话（含助手回复和工具结果）之后，才算覆盖了会话现状。"""
    if "recap_fresh" in summary:
        return bool(summary["recap_fresh"])
    recap, last = summary.get("recap"), summary.get("last_ts")
    return bool(recap) and last is not None and recap["ts"] >= last


def is_candidate(summary, now, idle_days, entrypoints, since_ts=None):
    last = summary.get("last_ts")
    return (summary.get("interactive", True)
            and summary.get("entrypoint") in entrypoints
            and summary.get("human_count", 0) >= 1
            and last is not None
            and now - last >= idle_days * 86400
            and (since_ts is None or last >= since_ts))


# ---------- 判断计划与结果 ----------

def matches_version(record, summary):
    token = record.get("activity_version", record.get("version"))
    return token == summary.get("activity_version", summary.get("version")) or (
        "activity_version" not in record and token == summary.get("version"))


def needs_judgment(entry):
    """当前对话版本还没有有效结论（判过、或判失败且不可重试）就需要判断。"""
    version = entry["summary"].get("version")
    verdict, error = entry.get("verdict"), entry.get("error")
    if verdict and matches_version(verdict, entry["summary"]):
        return False
    if error and matches_version(error, entry["summary"]) and not error.get("retry"):
        return False
    return True


def needs_owner(entry):
    """已判过、会显示（悬置/不确定）、但还没有归属的条目，要补判一次归属。"""
    version = entry["summary"].get("version")
    verdict, error = entry.get("verdict"), entry.get("owner_error")
    if not verdict or not matches_version(verdict, entry["summary"]) or verdict.get("status") not in SHOWN_STATUSES:
        return False
    if verdict.get("owner") in OWNERS:
        return False
    if error and matches_version(error, entry["summary"]) and not error.get("retry"):
        return False
    return True


def _listable(entry, now, idle_days, entrypoints, since_ts):
    """会出现在某个页面上的会话：仍是候选，或已被清理但当初是交互会话、且不早于日期下限。"""
    summary = entry["summary"]
    if not entry.get("gone"):
        return is_candidate(summary, now, idle_days, entrypoints, since_ts)
    if summary.get("entrypoint") not in entrypoints:
        return False
    return since_ts is None or (summary.get("last_ts") or 0) >= since_ts


MODE_ORDER = {"owner": 0, "recap": 1, "resume": 2}


def plan_judgments(sessions, now, idle_days, entrypoints, only_mode=None, since_ts=None):
    """返回 [(sid, mode)]。mode：
    - owner：已判过但缺归属，只补判归属（便宜，材料是已有结论，会话被清理了也能补）；
    - recap：有新鲜 recap，只做短分类；
    - resume：让会话自答。
    便宜的排前面；同类里最近活跃的在前。已消失的会话无法再判断状态，不进 recap/resume。
    """
    plan = []
    for sid, entry in sessions.items():
        summary = entry["summary"]
        if not _listable(entry, now, idle_days, entrypoints, since_ts):
            continue
        if needs_owner(entry):
            mode = "owner"
        elif entry.get("gone") or not needs_judgment(entry):
            continue
        else:
            mode = "recap" if recap_is_fresh(summary) else "resume"
        if only_mode and mode != only_mode:
            continue
        plan.append((sid, mode))
    plan.sort(key=lambda x: (MODE_ORDER[x[1]], -(sessions[x[0]]["summary"]["last_ts"] or 0)))
    return plan


def owner_hints(summary, personal_paths):
    """归属线索：会话目录、写过的文件（截一部分），以及其中命中「个人路径」的那些。"""
    frags = [_norm_path(p) for p in personal_paths if p]
    written = summary.get("written") or []
    sample = written[-MAX_HINT_PATHS:]
    hits = [p for p in [summary.get("cwd") or ""] + written if p and any(f in _norm_path(p) for f in frags)]
    return {"cwd": summary.get("cwd") or "", "written": sample,
            "personal_hits": list(dict.fromkeys(hits))[:MAX_HINT_PATHS]}


def _norm_path(p):
    return str(p).replace("\\", "/").lower()


def make_request(sid, summary, mode, verdict=None, hints=None):
    req = {
        "sid": summary.get("native_session", sid),
        "mode": mode,
        "cwd": summary.get("cwd"),
        "recap": (summary.get("recap") or {}).get("text", ""),
        "last_human": summary.get("last_human", ""),
        "last_assistant": summary.get("last_assistant", ""),
        "hints": hints or {"cwd": summary.get("cwd") or "", "written": [], "personal_hits": []},
    }
    if verdict:
        req.update(title=verdict.get("title", ""), stopped_at=verdict.get("stopped_at", ""))
    return req


def record_failure(prev, msg, version, retry, max_failures, at):
    """记一次判断失败。同一对话版本累计失败 max_failures 次后不再自动重试；版本变了计数从头算。"""
    count = prev.get("count", 1) + 1 if prev and prev.get("version") == version else 1
    return {"msg": msg, "version": version, "retry": bool(retry) and count < max_failures,
            "count": count, "at": at}


def clean_verdict(raw):
    """校验判断器返回的结构；不合格抛 ValueError（按可重试的失败处理）。"""
    if not isinstance(raw, dict):
        raise ValueError(f"判断结果不是对象：{raw!r:.200}")
    status = raw.get("status")
    if status not in STATUSES:
        raise ValueError(f"判断结果状态非法：{status!r}")
    title = _clip(str(raw.get("title") or ""), 40)
    stopped = _clip(str(raw.get("stopped_at") or ""), 400)
    out = {"status": status, "title": title, "stopped_at": stopped}
    if "owner" in raw:   # 缺归属不算失败：下次运行会单独补判
        out["owner"] = clean_owner(raw)
    return out


def clean_owner(raw):
    """校验归属；不合格抛 ValueError（按可重试的失败处理）。"""
    owner = raw.get("owner") if isinstance(raw, dict) else None
    if owner not in OWNERS:
        raise ValueError(f"归属非法：{owner!r}")
    return owner


# ---------- 关闭（打勾） ----------
# 关闭状态以 state 为准，键是 "sid@版本"；两页的勾选只是用户改它的入口。

def closure_key(sid, version):
    return f"{sid.lower()}@{version}"


def _marker(sid, version):
    return f"%%ss:{sid.lower()}@{version}%%"


def parse_marks(text):
    """页面里带标记的复选框行 → {"sid@版本": 是否勾选}。"""
    return {closure_key(m.group(2), m.group(3)): m.group(1) in "xX"
            for m in CHECK_LINE.finditer(text or "")}


def parse_legacy_closed(text):
    """旧版粘贴的「关闭 <sid> @ <版本>」→ {"sid@版本"}。"""
    return {closure_key(m.group(1), m.group(2)) for m in CLOSE_RECORD.finditer(text or "")}


def merge_closures(closures, pending_marks, closed_marks, legacy, now, archive_marks=None):
    """把各页的勾选并进关闭状态，返回新的 dict。

    清单页勾上 = 关闭；已处理页取消勾选 = 恢复；归档页两种都有（勾上 = 关闭，取消 = 恢复）。
    恢复最后处理：同一条几页矛盾时（比如 Obsidian 把旧的清单页写回来了），以恢复为准。
    """
    archive_marks = archive_marks or {}
    out = dict(closures or {})
    for key in sorted(legacy):
        out.setdefault(key, {"at": now})
    for marks in (pending_marks, archive_marks):
        for key, checked in marks.items():
            if checked:
                out.setdefault(key, {"at": now})
    for marks in (closed_marks, archive_marks):
        for key, checked in marks.items():
            if not checked:
                out.pop(key, None)
    return out


def effective_closures(closures, sessions):
    """只留仍然生效的关闭：会话还在记录里、且对话版本没变。返回 (精简后的 dict, {(sid, 版本)})。"""
    kept, pairs = {}, set()
    for key, info in closures.items():
        sid, _, version = key.partition("@")
        entry = sessions.get(sid) or next((e for e in sessions.values()
                   if str(e["summary"].get("native_session", "")).lower() == sid), None)
        if entry and version in (entry["summary"].get("activity_version"), entry["summary"].get("version")):
            kept[key] = info
            pairs.add((sid, version))
    return kept, pairs


# ---------- 页面 ----------
# 每条只放在一个地方：窗口内没关 → 主清单；窗口内已关 → 已处理页；超过窗口 → 按最后对话所在周归档。

def build_items(sessions, closures, now, idle_days, entrypoints, since_ts=None):
    """挑出要上页面的条目（含已关的，带 closed 标记）。返回 (条目, 排队待判断数, 因属公司工作而隐藏的数)。

    closures：仍生效的关闭 {"sid@版本": {"at": 关闭时间}}。
    """
    items, queued, hidden = [], 0, 0
    for sid, entry in sessions.items():
        if not _listable(entry, now, idle_days, entrypoints, since_ts):
            continue
        summary = entry["summary"]
        gone = bool(entry.get("gone"))
        version = summary.get("version")
        verdict, error = entry.get("verdict"), entry.get("error")
        fallback_title = _clip(summary.get("first_human") or "（无标题）", 30)
        owner = None
        if verdict and matches_version(verdict, entry["summary"]):
            if verdict["status"] not in SHOWN_STATUSES:
                continue
            owner = verdict.get("owner")
            if owner == HIDDEN_OWNER:
                hidden += 1
                continue
            item = {"state": STATUS_LABEL[verdict["status"]], "title": verdict["title"] or fallback_title,
                    "stopped_at": verdict["stopped_at"]}
        elif error and matches_version(error, entry["summary"]):
            hint = "下次重试" if error.get("retry") else "不会自动重试"
            item = {"state": f"判断失败（{hint}）", "title": fallback_title,
                    "stopped_at": f"失败原因：{error.get('msg', '')}"}
        elif gone:
            item = {"state": "未判断", "title": fallback_title, "stopped_at": "原会话在判断前已被清理。"}
        else:
            queued += 1
            continue
        native_sid = summary.get("native_session", sid)
        mark_sid = summary.get("identity", native_sid)
        mark_version = summary.get("activity_version", version)
        closed = closures.get(closure_key(mark_sid, mark_version)) if mark_version else None
        if not closed and "activity_version" not in summary:
            closed = closures.get(closure_key(native_sid, version)) if version else None
        item.update(identity=summary.get("identity", sid), review_version=summary.get("review_version"),
                    sid=native_sid, version=version, mark_sid=mark_sid, mark_version=mark_version, cwd=summary.get("cwd") or "", branch=summary.get("branch"),
                    last_ts=summary.get("last_ts") or 0, gone=gone, owner=owner,
                    source_unavailable=entry.get("source_unavailable"),
                    closed=closed is not None, closed_at=(closed or {}).get("at") or 0)
        items.append(item)
    return items, queued, hidden


def week_start(epoch):
    """最后对话所在周的周一（本地日期）。"""
    d = datetime.fromtimestamp(epoch).date()
    return d - timedelta(days=d.weekday())


def archive_name(monday):
    """归档文件名：2026-09-29~10-05.md（周一到周日）。"""
    return f"{monday:%Y-%m-%d}~{monday + timedelta(days=6):%m-%d}.md"


def layout(items, now, window_days):
    """按"多久没聊"和"关没关"分页。返回 (主清单, 已处理页, {周一: [条目]})。"""
    pending, closed, archives = [], [], {}
    for it in items:
        if now - it["last_ts"] <= window_days * 86400:
            (closed if it["closed"] else pending).append(it)
        else:
            archives.setdefault(week_start(it["last_ts"]), []).append(it)
    return pending, closed, archives


def _project_name(cwd):
    parts = [p for p in re.split(r"[\\/]", cwd or "") if p]
    return parts[-1] if parts else "（未知目录）"


def _one_line(text):
    """列表项里不能有换行，否则复选框行和标记会被拆开。"""
    return " ".join(str(text or "").split())


def _link(name):
    return name[:-3] if name.endswith(".md") else name


def _resume_line(it):
    if it.get("source_unavailable"):
        return "原文暂不可读；摘要保留，恢复前须核实原会话。"
    if it["gone"]:
        return "原文不可用，无法恢复；原摘要保留。"
    return f"接着做：`cd \"{it['cwd']}\"; claude --resume {it['sid']}`"


def _day_label(epoch):
    d = datetime.fromtimestamp(epoch).date()
    return f"{d:%m-%d} 周{WEEKDAYS[d.weekday()]}"


def _item_lines(it, with_closed_at=False):
    """一条的复选框行 + 缩进的细节。勾选状态取自条目的 closed。"""
    if it.get("thread"):
        return thread_lines(it, with_closed_at)
    box = "x" if it["closed"] else " "
    lines = [f"- [{box}] **{_one_line(it['title'])}** · {_project_name(it['cwd'])} {_marker(it.get('mark_sid', it['sid']), it.get('mark_version', it['version']))}"]
    state = it["state"] + (" · 归属说不清" if it.get("owner") == "说不清" else "")
    lines.append(f"    - 状态：{state}")
    if it.get("reviewed"):
        lines.append("    - 来源审阅：整版已审，摘要作为来源资料保留。")
    elif it.get("linked"):
        lines.append(f"    - 来源审阅：已关联{len(it['linked'])}项，余部未审。")
    else:
        lines.append("    - 来源审阅：未审。")
    for link in it.get("linked", []):
        lines.append(f"    - 关联事项：{link['title']}（{link['id']}，{link['state']}）")
    if it["stopped_at"]:
        lines.append(f"    - 停在哪：{_one_line(it['stopped_at'])}")
    when = fmt_local(it["last_ts"]) if it["last_ts"] else "未知"
    branch = f" · 当时分支 `{it['branch']}`" if it.get("branch") else ""
    lines.append(f"    - 最后对话：{when}{branch}")
    if with_closed_at and it["closed"]:
        lines.append(f"    - 关闭于：{fmt_local(it['closed_at']) if it['closed_at'] else '未知'}")
    lines.append(f"    - {_resume_line(it)}")
    return lines


def _newest_first(items):
    return sorted(items, key=lambda i: (-i["last_ts"], i["sid"]))


def render_page(pending, queued, closed_count, archives, doc_issues, doc_error, names, window_days):
    """主清单：最近 window_days 天没关的，按天分组、最新在上。不含扫描时间，内容不变时字节也不变。

    names：{"closed": 已处理页文件名, "archive_dir": 归档目录名}；archives：layout 的第三项。
    """
    lines = [
        "# 悬着的事",
        "",
        "> 由 session-sweep 自动生成，除了勾选，其他手改会被覆盖。",
        f"> 不用管的勾上，下次运行（最多 4 小时）挪到 [[{_link(names['closed'])}]]。"
        "只关这一次提醒；那个会话之后有新对话会重新判断。",
        f"> 这里只放最近 {_fmt_days(window_days)} 天的，更早的按周归档。",
        "",
    ]
    head = f"最近 {_fmt_days(window_days)} 天：未处理 {len(pending)} 件 · 已处理 {closed_count} 件"
    if queued:
        head += f"　｜　排队待判断 {queued} 个会话（下次扫描继续）"
    lines += [head, ""]
    if archives:
        lines.append("归档：")
        for monday in sorted(archives, reverse=True):
            group = archives[monday]
            n_closed = sum(1 for i in group if i["closed"])
            name = _link(archive_name(monday))
            lines.append(f"- [[{names['archive_dir']}/{name}|{monday:%m-%d} 那周]]："
                         f"未处理 {len(group) - n_closed} · 已处理 {n_closed}")
        lines.append("")
    if not pending:
        lines += ["最近没有悬着的事。", ""]

    days = {}
    for it in pending:
        days.setdefault(datetime.fromtimestamp(it["last_ts"]).date(), []).append(it)
    for day in sorted(days, reverse=True):
        lines += [f"## {_day_label(days[day][0]['last_ts'])}", ""]
        for it in _newest_first(days[day]):
            lines += _item_lines(it)
        lines.append("")

    if doc_issues or doc_error:
        lines += ["## 疑似文档未同步", "", "只提示，不会自动补写。蓝图/CHANGELOG 的修改时间比这批代码旧，不代表内容一定缺。", ""]
        if doc_error:
            lines.append(f"- 文档探测不可用：{doc_error}")
        for issue in doc_issues:
            lines.append(f"- `{issue['root']}`：{issue['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _fmt_days(days):
    return int(days) if float(days).is_integer() else days


def render_closed_page(items, page_name, window_days):
    """已处理页：最近 window_days 天内关掉的，全部显示为已勾；取消勾选 = 恢复到清单。"""
    lines = [
        "# 悬着的事 · 已处理",
        "",
        "> 由 session-sweep 自动生成，除了勾选，其他手改会被覆盖。",
        f"> 关错了就取消勾选，下次运行（最多 4 小时）回到 [[{_link(page_name)}]]。"
        f"超过 {_fmt_days(window_days)} 天的按周归档。",
        "",
        f"共 {len(items)} 件" if items else "最近没有关闭的条目",
        "",
    ]
    for it in sorted(items, key=lambda i: (-i["closed_at"], i["sid"])):
        lines += _item_lines(it, with_closed_at=True)
    return "\n".join(lines).rstrip() + "\n"


def render_archive_page(monday, items, page_name):
    """一周的归档页：没处理的在前（未勾），已处理的在后（已勾）；勾上 = 关闭，取消 = 恢复。"""
    open_items = [i for i in items if not i["closed"]]
    closed_items = [i for i in items if i["closed"]]
    lines = [
        f"# 悬着的事 · 归档 {monday:%Y-%m-%d} ~ {monday + timedelta(days=6):%m-%d}",
        "",
        ARCHIVE_MARK,
        "> 由 session-sweep 自动生成，除了勾选，其他手改会被覆盖。最后对话在这一周、已超出主清单窗口的都在这里。",
        f"> 勾上 = 不用管了；取消勾选 = 恢复。回到 [[{_link(page_name)}]]。",
        "",
        f"未处理 {len(open_items)} 件 · 已处理 {len(closed_items)} 件",
        "",
    ]
    if open_items:
        lines += ["## 没处理的", ""]
        for it in _newest_first(open_items):
            lines += _item_lines(it)
        lines.append("")
    if closed_items:
        lines += ["## 已处理", ""]
        for it in _newest_first(closed_items):
            lines += _item_lines(it, with_closed_at=True)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def is_generated_archive(text):
    return ARCHIVE_MARK in (text or "")

THREAD_MARK = re.compile(r"^[ \t]*[-*+][ \t]+\[([^\]])\].*?%%pt:([A-Za-z0-9_.:-]+)@([0-9]+)%%", re.M)


def parse_thread_marks(text):
    return {m.group(2) + "@" + m.group(3): m.group(1) in "xX"
            for m in THREAD_MARK.finditer(text or "")}


def annotate_sources(items, snapshot):
    for item in items:
        identity = item["identity"]
        item["linked"] = [t for t in snapshot["items"].values()
                          if any(s["platform"] + ":" + s["session"] == identity for s in t["sources"])]
        seen = snapshot["seen"].get(identity, {})
        item["reviewed"] = bool(item.get("review_version")) and seen.get("version") == item["review_version"]
    return items


def thread_items(snapshot, closures, now):
    out = []
    for thread in snapshot["items"].values():
        if thread["state"] not in ("open", "decided"):
            continue
        key = thread["id"] + "@" + str(thread["rev"])
        closure = closures.get(key)
        # Keep active matter cards on the current page; source times remain in the full card.
        out.append({"thread": thread, "sid": thread["id"], "last_ts": now,
                    "closed": closure is not None, "closed_at": (closure or {}).get("at", 0)})
    return out


def thread_lines(it, with_closed_at=False):
    t = it["thread"]
    box = "x" if it["closed"] else " "
    lines = [f"- [{box}] **{_one_line(t['title'])}** %%pt:{t['id']}@{t['rev']}%%",
             f"    - 事项：{t['id']} · {t['state']} · rev {t['rev']}",
             "    - 勾选只关闭此版本提醒，不改变事项决定。"]
    labels = {"motivation": "动机", "stopped_at": "历史停点", "next_suggestion": "AI建议"}
    for field, label in labels.items():
        value = t.get(field)
        if isinstance(value, dict):
            value = value.get("text")
        if value:
            lines.append(f"    - {label}：")
            lines.extend("        " + line for line in str(value).splitlines())
    for fact in t.get("confirmed", []):
        lines.append(f"    - 已有结论（原话核验{fact['verified']}）：")
        lines.extend("        " + line for line in fact["said"].splitlines())
        lines.append("        来源：" + _source_line(fact["source"]))
        lines.extend("        " + line for line in fact["source"]["excerpt"].splitlines())
        lines.append("        核验说明：" + fact["verification_note"])
    for field, label in (("questions", "未定问题"), ("paths", "关联工程"), ("handoff", "进度现场")):
        for value in t.get(field, []):
            lines.append(f"    - {label}：{value}")
    for source in t["sources"]:
        lines.append("    - 原话来源：" + _source_line(source))
        lines.extend("        " + line for line in source["excerpt"].splitlines())
    for event in t["log"]:
        lines.append("    - 决定记录：" + event["at"] + " · " + event["action"])
        if event.get("actor"):
            lines.append("        记录者：" + event["actor"] + " · 已核验用户决定：" + str(event.get("confirmed", False)))
        if event.get("verification"):
            lines.append("        核验：" + str(event["verification"]))
        if event.get("source"):
            lines.append("        原话来源：" + _source_line(event["source"]))
            lines.extend("        " + line for line in event["source"]["excerpt"].splitlines())
        if event.get("said"):
            lines.extend("        " + line for line in event["said"].splitlines())
        for evidence in event.get("evidence", []):
            lines.append("        证据：" + evidence)
    if with_closed_at and it["closed"]:
        lines.append("    - 提醒关闭于：" + fmt_local(it["closed_at"]))
    lines.append(f"    - 回顾：`pstate thread show {t['id']}`；选择：继续聊 / 推进 / 暂放 / 放弃。")
    return lines


def _source_line(source):
    return (f"{source['platform']}:{source['session']} · {source['anchor']} · "
            f"{source['time']} · {source['role']} · SHA256 {source['excerpt_hash']}"
            + (" · " + source["path"] if source.get("path") else ""))


def append_reviewed(text, items):
    if not items:
        return text
    lines = [text.rstrip(), "", "## 已审来源资料", "", "整版已审；以下保留原摘要，不作为待审线索。", ""]
    for item in _newest_first(items):
        lines.extend(_item_lines(item, with_closed_at=True))
    return "\n".join(lines).rstrip() + "\n"
