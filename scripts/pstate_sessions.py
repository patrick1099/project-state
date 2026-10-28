"""Explicit task/session links. The task Markdown is the only association store."""
import hashlib
import html
import json
import os
import platform
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pstate_lock as L

BEGIN = '<!-- pstate:sessions:begin v1 -->'
END = '<!-- pstate:sessions:end -->'
HEADER = '| 首次关联时间 | 主机 | 平台 | 原生会话号 | agentId | 关联依据 | 起始目录 |'
SEPARATOR = '| --- | --- | --- | --- | --- | --- | --- |'


class LinkError(ValueError):
    """Malformed association data must never be replaced with an empty table."""


def text(value, name, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise LinkError(name + ' 必须是' + ('字符串' if optional else '非空字符串'))
    return value


def identity(platform_name, session, agent='', host=None, start_dir=''):
    if platform_name not in ('claude', 'codex'):
        raise LinkError('平台必须是 claude 或 codex')
    return {'host': text(platform.node() if host is None else host, '主机'),
            'platform': platform_name, 'session': text(session, '会话号'),
            'agent': text(agent, 'agentId', True), 'start_dir': text(start_dir, '起始目录', True)}


def current_identity(platform_name=None, session=None, agent=None, environ=None):
    env = os.environ if environ is None else environ
    if platform_name is not None or session is not None:
        if platform_name is None or session is None:
            raise LinkError('--platform 和 --session 必须一起提供')
        return identity(platform_name, session, agent or '')
    candidates = [(p, env.get(k)) for p, k in (('claude', 'CLAUDE_CODE_SESSION_ID'), ('codex', 'CODEX_THREAD_ID')) if env.get(k)]
    if len(candidates) != 1:
        raise LinkError('会话环境变量同时存在或均不存在；请显式提供 --platform 和 --session')
    return identity(*candidates[0], agent=agent or '')


def key(row):
    return tuple(row[k] for k in ('host', 'platform', 'session', 'agent'))


def cell(value):
    return html.escape(value, quote=False).replace('|', '&#124;').replace('\r', '&#13;').replace('\n', '&#10;')


def parse_document(raw):
    content = raw.decode('utf-8')
    if content.count(BEGIN) != 1 or content.count(END) != 1:
        raise LinkError('会话.md 受管区起止标记缺失或重复；保留原文件，请人工检查')
    start, end = content.index(BEGIN), content.index(END)
    if end < start or (start and content[start-1] != '\n') or content[end+len(END):end+len(END)+1] not in ('', '\r', '\n'):
        raise LinkError('会话.md 受管区标记位置损坏')
    lines = content[start+len(BEGIN):end].splitlines()
    if not lines or lines.pop(0) != '' or len(lines) < 2 or lines[:2] != [HEADER, SEPARATOR]:
        raise LinkError('会话.md 受管表头损坏')
    rows, keys = [], set()
    for line in lines[2:]:
        if not line.startswith('| ') or not line.endswith(' |'):
            raise LinkError('会话.md 受管表格行无法解析')
        encoded = line[2:-2].split(' | ')
        values = [html.unescape(v) for v in encoded]
        if len(values) != 7 or any(cell(v) != v0 for v, v0 in zip(values, encoded)):
            raise LinkError('会话.md 受管表格字段无法解析')
        first, host, plat, sid, agent, reasons, start_dir = values
        try:
            datetime.fromisoformat(first)
            tags = json.loads(reasons)
            if not isinstance(tags, list) or not tags or any(not isinstance(t, str) or not t.strip() for t in tags) or len(set(tags)) != len(tags):
                raise ValueError('invalid reason tags')
        except ValueError as exc:
            raise LinkError('会话.md 时间或关联依据损坏') from exc
        row = identity(plat, sid, agent, host, start_dir)
        row.update(first=first, reasons=tags)
        if key(row) in keys:
            raise LinkError('会话.md 有重复会话身份；请人工检查')
        keys.add(key(row))
        rows.append(row)
    return content[:start], content[end+len(END):], rows, '\r\n' if '\r\n' in content[start:end] else '\n'


def render(prefix, suffix, rows, newline):
    lines = [BEGIN, HEADER, SEPARATOR]
    for row in rows:
        values = [row['first'], row['host'], row['platform'], row['session'], row['agent'],
                  json.dumps(sorted(row['reasons']), ensure_ascii=False), row['start_dir']]
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    lines.append(END)
    return (prefix + newline.join(lines) + suffix).encode('utf-8')


def link_sessions(home, task, entries, *, dry_run=False):
    """Each entry is (identity, reason tags); no claim or thread lock is held here."""
    task_key, real = L.task_dir_key(task)
    lock_home = Path(home) / 'session-links' / hashlib.sha256(task_key.encode('utf-8')).hexdigest()
    path = Path(real) / '会话.md'
    with L.mutex(lock_home):
        if path.is_symlink():
            raise LinkError('会话.md 是符号链接；拒绝改写')
        try:
            original = path.read_bytes()
        except FileNotFoundError:
            original = None
        if original is None:
            prefix, suffix, rows, newline = '# 会话\n\npstate 维护下方受管区；人工说明请写在区外。\n\n', '\n', [], '\n'
        else:
            prefix, suffix, rows, newline = parse_document(original)
        by_key = {key(row): row for row in rows}
        for who, tags in entries:
            who = identity(who['platform'], who['session'], who.get('agent', ''), who.get('host'), who.get('start_dir', ''))
            if not isinstance(tags, (list, tuple, set)) or not tags:
                raise LinkError('关联依据必须是非空标签集合')
            tags = [text(t, '关联依据') for t in tags]
            prior = by_key.get(key(who))
            if prior is None:
                # 本机时区、精确到秒：这张表是给人在 Obsidian 里看的。
                prior = {**who, 'first': datetime.now().astimezone().isoformat(timespec='seconds'), 'reasons': []}
                rows.append(prior)
                by_key[key(who)] = prior
            prior['reasons'] = sorted(set(prior['reasons']) | set(tags))
            # Never overwrite an existing directory, even if another source disagrees.
            if not prior['start_dir'] and who['start_dir']:
                prior['start_dir'] = who['start_dir']
        updated = render(prefix, suffix, rows, newline)
        changed = updated != original
        if changed and not dry_run:
            tmp = path.with_name('.pstate-sessions-' + uuid.uuid4().hex + '.tmp')
            try:
                with tmp.open('xb') as stream:
                    stream.write(updated)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    current = path.read_bytes()
                except FileNotFoundError:
                    current = None
                if path.is_symlink() or current != original:
                    raise LinkError('会话.md 在更新期间被修改；保留人工内容，请重读后重试')
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        return {'path': str(path), 'sessions': len(rows), 'changed': changed, 'dry_run': dry_run}


def repair_command(task, who, reasons):
    args = [sys.executable, str(Path(__file__).with_name('pstate.py')), 'link', str(task),
            '--platform', who['platform'], '--session', who['session']]
    if who.get('agent'):
        args += ['--agent', who['agent']]
    for reason in reasons:
        args += ['--reason', reason]
    # PowerShell display command; quote every argument so tags remain literal.
    return '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in args)


def link_claim(home, task, sid, reason):
    who = identity('claude', sid)
    try:
        result = link_sessions(home, task, [(who, [reason])])
    except (OSError, ValueError, TimeoutError) as exc:
        print(f'认领已成功，关联会话失败：{exc}。补记命令：{repair_command(task, who, [reason])}', file=sys.stderr)
        return 1
    print('已关联会话：' + result['path'])
    return 0


def link_transfer(home, item, *, dry_run=False):
    """Best effort per source: successful links survive another source's failure."""
    task = item['transferred_to']
    pending, failures, linked = {}, [], []

    def collect(who, tags):
        prior = pending.get(key(who))
        if prior is None:
            pending[key(who)] = (who, set(tags))
        else:
            prior[1].update(tags)
            if not prior[0]['start_dir'] and who['start_dir']:
                prior[0]['start_dir'] = who['start_dir']

    for src in item['sources']:
        tags = ['立项前来源', '事项:' + item['id'], '锚点:' + src['anchor']]
        try:
            who = identity(src['platform'], src['session'], src.get('agentId', ''), src.get('host'), src.get('start_dir', ''))
            collect(who, tags)
        except ValueError as exc:
            failures.append({'source': src, 'message': str(exc)})
    try:
        collect(current_identity(), ['转交执行', '事项:' + item['id']])
    except ValueError as exc:
        failures.append({'message': str(exc), 'suggestion': '用 link --platform ... --session ... --reason 转交执行 补记执行会话'})
    for who, tag_set in pending.values():
        tags = sorted(tag_set)
        try:
            link_sessions(home, task, [(who, tags)], dry_run=dry_run)
            linked.append({'identity': who, 'reasons': tags})
        except (OSError, ValueError, TimeoutError) as exc:
            failures.append({'identity': who, 'message': str(exc), 'repair_command': repair_command(task, who, tags)})
    return {'ok': not failures, 'linked': linked, 'failures': failures}

