"""Thread storage and CLI contracts exercised against temporary state only."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from pstate_threads import store
from pstate_threads.core import ThreadError


def source(session='session-1', anchor='message-1', excerpt='请继续做这件事'):
    return {'platform': 'claude', 'session': session, 'anchor': anchor,
            'time': '2026-10-22T00:00:00Z', 'role': 'user', 'excerpt': excerpt,
            'excerpt_hash': hashlib.sha256(excerpt.encode('utf-8')).hexdigest()}


def payload(title='待续事项', **values):
    return {'title': title, 'sources': [source()], **values}


def decision(text='请继续做这件事'):
    return {'said': text, 'source': source(excerpt=text)}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'threads'

    def add(self, **kw):
        return store.mutate(self.directory, 'add', payload(**kw))['item']

    def test_replay_distinct_into_preserves_closed_state(self):
        item = self.add()
        again = store.mutate(self.directory, 'add', payload())
        self.assertEqual(again['id'], item['id'])
        self.assertFalse(again['committed'])
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'add', payload('另一件事'))
        second = store.mutate(self.directory, 'add', payload('另一件事'), distinct=True)
        self.assertNotEqual(second['id'], item['id'])
        closed = store.mutate(self.directory, 'drop', decision(), ident=item['id'], rev=1)['item']
        more = {'sources': [source(session='session-2')]}
        updated = store.mutate(self.directory, 'add', more, into=item['id'], rev=closed['rev'])['item']
        self.assertEqual(updated['state'], 'dropped')
        self.assertEqual(store.mutate(self.directory, 'add', payload())['id'], item['id'])
        self.assertTrue(store.mutate(self.directory, 'add', more, into=item['id'], rev=1)['replayed'])
        self.assertEqual(len(store.read(self.directory)['items']), 2)

    def test_initial_add_replays_after_rename(self):
        item = self.add()
        store.mutate(self.directory, 'set', {'title': '新的显示名'}, ident=item['id'], rev=1)
        result = store.mutate(self.directory, 'add', payload())
        self.assertTrue(result['replayed'])
        self.assertEqual(result['id'], item['id'])
        self.assertEqual(result['item']['title'], '新的显示名')
        self.assertEqual(result['item']['rev'], 2)

    def test_rev_and_dry_run_use_same_write_path(self):
        result = store.mutate(self.directory, 'add', payload(), dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertFalse((self.directory / 'threads.json').exists())
        item = self.add(paths=['C:/one', 'D:/two'])
        before = (self.directory / 'threads.json').read_bytes()
        preview = store.mutate(self.directory, 'set', {'stopped_at': '等待联调'}, ident=item['id'], rev=1, dry_run=True)
        self.assertEqual(preview['item']['rev'], 2)
        self.assertEqual((self.directory / 'threads.json').read_bytes(), before)
        store.mutate(self.directory, 'set', {'stopped_at': '等待联调'}, ident=item['id'], rev=1)
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'set', {'title': '错改'}, ident=item['id'], rev=1)
        self.assertEqual(store.read(self.directory)['items'][item['id']]['title'], '待续事项')

    def test_corruption_schema_and_missing_primary_fail_closed(self):
        self.add()
        path = self.directory / 'threads.json'
        for contents in ('{bad', '{"schema":99,"items":{},"seen":{}}',
                         '{"schema":1,"items":[],"seen":{}}'):
            path.write_text(contents, encoding='utf-8')
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'add', payload())
            self.assertEqual(path.read_text(encoding='utf-8'), contents)
        path.unlink()
        (self.directory / 'bak.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ThreadError, 'backup'):
            store.read(self.directory)
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'add', payload())
        self.assertFalse(path.exists())

    def test_backup_is_previous_valid_version(self):
        item = self.add()
        original = store.read(self.directory)
        store.mutate(self.directory, 'set', {'title': '新标题'}, ident=item['id'], rev=1)
        backup = json.loads((self.directory / 'bak.json').read_text(encoding='utf-8'))
        self.assertEqual(backup, original)

    def test_post_replace_readback_error_reports_replaced(self):
        item = self.add()
        real_load = store.load
        count = 0
        def faulty(directory):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('injected readback fault')
            return real_load(directory)
        with patch.object(store, 'load', side_effect=faulty):
            with self.assertRaises(ThreadError) as raised:
                store.mutate(self.directory, 'set', {'title': '已经替换'}, ident=item['id'], rev=1)
        self.assertEqual(raised.exception.details['commit_status'], 'replaced_unverified')
        self.assertEqual(store.read(self.directory)['items'][item['id']]['title'], '已经替换')

    def test_pre_replace_failure_preserves_primary(self):
        item = self.add()
        original = store.read(self.directory)
        with patch.object(store.os, 'replace', side_effect=OSError('disk problem')):
            with self.assertRaises(ThreadError) as raised:
                store.mutate(self.directory, 'set', {'title': '不会替换'}, ident=item['id'], rev=1)
        self.assertEqual(raised.exception.details['commit_status'], 'not_replaced')
        self.assertEqual(store.read(self.directory), original)

    def test_mutex_timeout_and_schema_validation(self):
        with patch.object(store.pstate_lock, 'mutex', side_effect=TimeoutError('busy')):
            with self.assertRaises(TimeoutError):
                store.mutate(self.directory, 'add', payload())
        invalid = payload()
        invalid['sources'][0]['excerpt_hash'] = 'wrong'
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'add', invalid)
        self.assertFalse((self.directory / 'threads.json').exists())

    def test_done_requires_evidence_without_claiming_user_confirmation(self):
        item = self.add()
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'done', {}, ident=item['id'], rev=1)
        result = store.mutate(self.directory, 'done', {'evidence': ['test: 12 tests passed; not installed']}, ident=item['id'], rev=1)
        event = result['item']['log'][-1]
        self.assertEqual(event['actor'], 'assistant')
        self.assertFalse(event['confirmed'])
        self.assertNotIn('said', event)
        self.assertFalse(result['confirmed'])

    def test_transfer_needs_verified_do_and_closes_until_reopen(self):
        item = self.add()
        ident = item['id']
        target = {'to': 'C:/vault/P1/task/T01-x', 'evidence': ['写入 方案.md 并回读']}
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'transfer', target, ident=ident, rev=1)
        item = store.mutate(self.directory, 'do', decision(), ident=ident, rev=1)['item']
        with self.assertRaises(ThreadError, msg='do 的原话没核验通过，不能转交'):
            store.mutate(self.directory, 'transfer', target, ident=ident, rev=item['rev'])
        verified = {'verified': True, 'reason': 'test'}
        item = store.mutate(self.directory, 'do', decision(), ident=ident, rev=item['rev'], verified=verified)['item']
        for bad in ({'to': 'C:/x'}, {'to': 'C:/x', 'evidence': []}, {**target, 'said': 'x'}):
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'transfer', bad, ident=ident, rev=item['rev'])
        item = store.mutate(self.directory, 'transfer', target, ident=ident, rev=item['rev'])['item']
        self.assertEqual((item['state'], item['transferred_to']), ('transferred', target['to']))
        event = item['log'][-1]
        self.assertEqual((event['action'], event['actor'], event['to'], event['evidence']),
                         ('transfer', 'assistant', target['to'], target['evidence']))
        self.assertEqual(store.counts(store.read(self.directory))['transferred'], 1)
        self.assertEqual(store.card(item)['transferred_facts'], {'path': target['to'], 'exists': False})
        for op, value in (('do', decision()), ('later', decision()), ('drop', decision()), ('done', {'evidence': ['x']})):
            with self.assertRaises(ThreadError, msg=op):
                store.mutate(self.directory, op, value, ident=ident, rev=item['rev'])
        with self.assertRaises(ThreadError, msg='transferred_to 只能由 transfer 写'):
            store.mutate(self.directory, 'set', {'transferred_to': 'C:/y'}, ident=ident, rev=item['rev'])
        item = store.mutate(self.directory, 'reopen', decision('重新开始'), ident=ident, rev=item['rev'])['item']
        self.assertEqual(item['state'], 'open')
        self.assertNotIn('transferred_to', item)
        self.assertIn('transfer', [e['action'] for e in item['log']], '历史转交目标留在 log')

    def test_transferred_state_must_carry_target(self):
        item = self.add()
        path = self.directory / 'threads.json'
        broken = store.read(self.directory)
        broken['items'][item['id']]['state'] = 'transferred'
        path.write_text(json.dumps(broken), encoding='utf-8')
        with self.assertRaises(ThreadError):
            store.read(self.directory)

    def test_stat_failure_never_treats_database_as_empty(self):
        self.add()
        original = (self.directory / 'threads.json').read_bytes()
        real_stat = Path.stat
        def failing(path, *args, **kwargs):
            if path.name == 'threads.json':
                raise PermissionError('injected inaccessible database')
            return real_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', failing):
            with self.assertRaises(ThreadError):
                store.read(self.directory)
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'add', payload())
        self.assertEqual((self.directory / 'threads.json').read_bytes(), original)

    def test_malformed_optional_fields_are_rejected_without_write(self):
        item = self.add()
        original = store.read(self.directory)
        path = self.directory / 'threads.json'
        for key, value in [('handoff', 123), ('paths', 'abc'), ('next_suggestion', {'text': 'x', 'by': 'human'}),
                           ('confirmed', [{'said': 'x'}]), ('questions', [None])]:
            invalid = copy.deepcopy(original)
            invalid['items'][item['id']][key] = value
            path.write_text(json.dumps(invalid), encoding='utf-8')
            before = path.read_bytes()
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'add', payload('new'), distinct=True)
            self.assertEqual(path.read_bytes(), before)

    def test_stdin_tty_is_rejected_for_explicit_dash(self):
        from pstate_threads.cli import read_input
        with patch('sys.stdin.isatty', return_value=True):
            with self.assertRaises(ThreadError):
                read_input('-')

    def test_handoff_facts_do_not_infer_completion(self):
        handoff = Path(self.tmp.name) / 'HANDOFF.md'
        handoff.write_text('现场记录', encoding='utf-8')
        item = self.add(handoff=[str(handoff), str(handoff.with_name('missing.md'))])
        card = store.card(item)
        self.assertTrue(card['handoff_facts'][0]['exists'])
        self.assertFalse(card['handoff_facts'][1]['exists'])
        self.assertEqual(card['state'], 'open')


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'state'
        self.env = {**os.environ, 'PSTATE_HOME': str(self.home), 'PYTHONDONTWRITEBYTECODE': '1', 'USERPROFILE': self.tmp.name}
        # 接手登记会按会话环境变量去家目录找日志；测试里不能碰到跑测试的真实会话。
        for name in ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID'):
            self.env.pop(name, None)
        self.base = [sys.executable, '-B', str(ROOT / 'scripts/pstate.py')]

    def run_cli(self, args, value=None, code=0, raw=None):
        data = raw if raw is not None else json.dumps(value, ensure_ascii=False) if value is not None else ''
        result = subprocess.run([*self.base, 'thread', *args], input=data, text=True, encoding='utf-8',
                                capture_output=True, cwd=self.tmp.name, env=self.env, timeout=20)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        if code:
            self.assertEqual(result.stdout, '')
            return json.loads(result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)['data']

    def test_complete_no_repo_workflow(self):
        item = self.run_cli(['add', '--json'], payload(paths=['C:/one', 'D:/two']))['item']
        ident = item['id']
        shown = self.run_cli(['show', ident, '--json'])['item']
        self.assertEqual(shown['paths'], ['C:/one', 'D:/two'])
        resumed = self.run_cli(['choose', ident, 'continue', '--json'])
        self.assertFalse(resumed['committed'])
        self.assertEqual(resumed['item']['rev'], 1)
        self.assertEqual(resumed['session_linked'], {'linked': False, 'reason': 'no_session_env'})
        for choice, expected in [('do', 'decided'), ('later', 'parked'), ('drop', 'dropped')]:
            item = self.run_cli(['choose', ident, choice, '--rev', str(item['rev']), '--json'], decision())['item']
            self.assertEqual(item['state'], expected)
            self.assertFalse(item['log'][-1]['confirmed'])
        listed = self.run_cli(['list', '--format', 'json'])
        self.assertEqual(listed['items'], [])
        self.assertEqual(listed['counts']['dropped'], 1)
        item = self.run_cli(['reopen', ident, '--rev', str(item['rev']), '--json'], decision('重新开始'))['item']
        self.assertEqual(item['state'], 'open')
        item = self.run_cli(['done', ident, '--rev', str(item['rev']), '--json'], {'evidence': ['commit: abc; test: passed']})['item']
        self.assertEqual(item['state'], 'done')
        self.assertFalse(item['log'][-1]['confirmed'])
        self.assertEqual(len(self.run_cli(['list', '--all', '--json'])['items']), 1)

    def test_transfer_validates_task_directory(self):
        ident = self.run_cli(['add', '--json'], payload())['item']['id']
        item = self.run_cli(['choose', ident, 'do', '--rev', '1', '--json'], decision())['item']
        rev = str(item['rev'])
        task = Path(self.tmp.name) / 'vault' / 'P2601-x' / 'task' / 'T01-plan'
        task.mkdir(parents=True)
        plain = Path(self.tmp.name) / 'vault' / 'notes'
        plain.mkdir()
        for to in (str(task / 'missing'), str(plain), 'relative/task/T01-x'):
            bad = self.run_cli(['transfer', ident, '--rev', rev, '--json'], {'to': to, 'evidence': ['x']}, code=2)
            self.assertEqual(bad['error']['code'], 'E_VALIDATION')
        refused = self.run_cli(['transfer', ident, '--rev', rev, '--json'], {'to': str(task), 'evidence': ['x']}, code=1)
        self.assertIn('verified', refused['error']['message'], '本地找不到原话，do 未核验，不能转交')
        self.assertEqual(self.run_cli(['show', ident, '--json'])['item']['state'], 'decided')

    def test_validation_help_terminator_and_empty_stdout(self):
        bad = self.run_cli(['set', 'missing', '--json'], {}, code=2)
        self.assertEqual(bad['error']['code'], 'E_VALIDATION')
        self.run_cli(['add', '--format=json'], raw='broken', code=2)
        self.run_cli(['list', '--bad', '--json'], code=2)
        self.run_cli(['--json', 'show', '--', '--ai-help'], code=1)
        result = subprocess.run([*self.base, 'thread', 'nonsense', '--ai-help', '--json'],
                                capture_output=True, text=True, encoding='utf-8', env=self.env, cwd=self.tmp.name)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('---\nname:'))
        self.assertIn('## Quick Reference', result.stdout)
        self.assertFalse(self.home.exists())

    def test_input_path_error_classification(self):
        result = self.run_cli(['add', '--input-file', str(Path(self.tmp.name) / 'missing.json'), '--json'], code=1)
        self.assertEqual(result['error']['code'], 'E_NOT_FOUND')
        result = self.run_cli(['add', '--input-file', self.tmp.name, '--json'], code=2)
        self.assertEqual(result['error']['code'], 'E_VALIDATION')
        invalid = Path(self.tmp.name) / 'bad.json'
        invalid.write_bytes(b'\xff\xff')
        result = self.run_cli(['add', '--input-file', str(invalid), '--json'], code=2)
        self.assertEqual(result['error']['code'], 'E_VALIDATION')

    def test_seen_explicit_and_dry_run(self):
        value = {'platform': 'codex', 'session': 's1', 'version': 'v1'}
        self.run_cli(['seen', '--dry-run', '--json'], value)
        self.assertFalse((self.home / 'threads/threads.json').exists())
        self.run_cli(['seen', '--json'], value)
        self.assertEqual(store.read(self.home / 'threads')['seen']['codex:s1']['version'], 'v1')

    def test_discover_seen_and_native_source_verification(self):
        self.env['USERPROFILE'] = self.tmp.name
        self.env.pop('CLAUDE_CODE_SESSION_ID', None)
        self.env.pop('CODEX_THREAD_ID', None)
        directory = Path(self.tmp.name) / '.claude/projects/fixture'
        directory.mkdir(parents=True)
        log = directory / 'native-session.jsonl'
        row = {'sessionId': 'native-session', 'uuid': 'user-message', 'type': 'user',
               'timestamp': '2026-10-22T00:00:00Z', 'origin': {'kind': 'human'}, 'entrypoint': 'cli',
               'message': {'role': 'user', 'content': '决定继续这件事'}}
        log.write_text(json.dumps(row, ensure_ascii=False) + '\n', encoding='utf-8')
        options = {'files': {'claude': [str(log)]}, 'roots': {}}
        discovered = self.run_cli(['discover', '--input-file', '-', '--json'], options)
        self.assertEqual(len(discovered['sessions']), 1)
        self.assertFalse(self.home.exists())
        session = discovered['sessions'][0]
        src = session['sources'][0]
        item = self.run_cli(['add', '--json'], {'title': '本地来源事项', 'sources': [src],
                                                  'confirmed': [{'said': '决定继续这件事', 'source': src}]})['item']
        self.assertTrue(item['confirmed'][0]['verified'])
        chosen = self.run_cli(['choose', item['id'], 'do', '--rev', '1', '--json'],
                              {'said': '决定继续这件事', 'source': src})
        self.assertTrue(chosen['confirmed'], chosen)
        self.run_cli(['seen', '--json'], {key: session[key] for key in ('platform', 'session', 'version')})
        self.assertEqual(self.run_cli(['discover', '--input-file', '-', '--json'], options)['sessions'], [])
        row['message']['content'] += '，补充新的决定'
        log.write_text(json.dumps(row, ensure_ascii=False) + '\n', encoding='utf-8')
        self.assertEqual(len(self.run_cli(['discover', '--input-file', '-', '--json'], options)['sessions']), 1)
        self.env['CLAUDE_CODE_SESSION_ID'] = 'native-session'
        self.assertEqual(self.run_cli(['discover', '--input-file', '-', '--json'], options)['sessions'], [])

    def test_hook_outside_git_only_counts(self):
        self.run_cli(['add', '--json'], payload(title='do not inject this title'))
        result = subprocess.run([*self.base, 'hook'], input=json.dumps({'cwd': self.tmp.name}),
                                capture_output=True, text=True, encoding='utf-8', env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('thread list', context)
        self.assertNotIn('do not inject this title', context)

    def native_log(self, sid='native-session'):
        self.env.pop('CLAUDE_CODE_SESSION_ID', None)
        self.env.pop('CODEX_THREAD_ID', None)
        directory = Path(self.tmp.name) / '.claude/projects/fixture'
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (sid + '.jsonl')
        self.append_human(log, sid, 'user-1', '这件事记一下', '2026-10-22T00:00:00Z')
        options = {'files': {'claude': [str(log)]}, 'roots': {}}
        src = self.run_cli(['discover', '--input-file', '-', '--json'], options)['sessions'][0]['sources'][0]
        return log, src

    def append_human(self, log, sid, uuid, text, stamp):
        row = {'sessionId': sid, 'uuid': uuid, 'type': 'user', 'timestamp': stamp,
               'origin': {'kind': 'human'}, 'entrypoint': 'cli', 'message': {'role': 'user', 'content': text}}
        with log.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    def test_list_flags_source_session_that_kept_going(self):
        log, src = self.native_log()
        item = self.run_cli(['add', '--json'], {'title': '来源会话后来接着干', 'sources': [src]})['item']
        listed = self.run_cli(['list', '--json'])['items'][0]
        self.assertFalse(listed['possibly_stale'])
        self.assertEqual(listed['followups'][0]['user_messages'], 0)
        later = datetime.now(timezone.utc).isoformat()
        self.append_human(log, 'native-session', 'user-2', '那就1，2', later)
        for shown in (self.run_cli(['list', '--json'])['items'][0], self.run_cli(['show', item['id'], '--json'])['item']):
            self.assertTrue(shown['possibly_stale'])
            self.assertEqual(shown['followups'][0]['user_messages'], 1)
            self.assertEqual(shown['followups'][0]['last_time'], later)
        item = self.run_cli(['set', item['id'], '--rev', '1', '--json'], {'stopped_at': '已按 1、2 改完'})['item']
        self.assertFalse(self.run_cli(['list', '--json'])['items'][0]['possibly_stale'], '卡片更新后不再提示')
        self.append_human(log, 'native-session', 'user-3', '再补一句', '2099-01-01T00:00:00Z')
        self.run_cli(['done', item['id'], '--rev', '2', '--json'], {'evidence': ['配置已写入并实测']})
        closed = self.run_cli(['list', '--all', '--json'])['items'][0]
        self.assertEqual(closed['followups'], [], '已关闭的卡不再追查来源')
        self.assertFalse(closed['possibly_stale'])

    def test_missing_source_log_is_not_called_stale(self):
        _, src = self.native_log()
        self.run_cli(['add', '--json'], {'title': '日志已删', 'sources': [{**src, 'path': src['path'] + '.gone'}]})
        shown = self.run_cli(['list', '--json'])['items'][0]
        self.assertFalse(shown['possibly_stale'])
        self.assertFalse(shown['followups'][0]['readable'])

    def prompt_hook(self, sid):
        result = subprocess.run([*self.base, 'hook-prompt'], input=json.dumps({'cwd': self.tmp.name, 'session_id': sid,
                                                                               'hook_event_name': 'UserPromptSubmit'}),
                                capture_output=True, text=True, encoding='utf-8', env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout else ''

    def test_prompt_hook_reminds_only_the_source_session(self):
        item = self.run_cli(['add', '--json'], payload(title='本会话记下的卡'))['item']
        self.run_cli(['add', '--json'], payload(title='别的会话的卡', sources=[source(session='other')]))
        context = self.prompt_hook('session-1')
        self.assertIn('本会话记下的卡', context)
        self.assertIn(item['id'], context)
        self.assertIn('thread done', context)
        self.assertIn('thread set', context)
        self.assertNotIn('别的会话的卡', context)
        self.assertEqual(self.prompt_hook('unrelated'), '')
        self.assertEqual(self.prompt_hook(''), '')
        self.run_cli(['done', item['id'], '--rev', '1', '--json'], {'evidence': ['已完成']})
        self.assertEqual(self.prompt_hook('session-1'), '')

    def pickup_log(self, sid, text='继续做那件事', stamp='2026-10-23T00:00:00Z'):
        directory = Path(self.tmp.name) / '.claude/projects/other'
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / (sid + '.jsonl')
        self.append_human(log, sid, 'pickup-1', text, stamp)
        return log

    def test_continue_registers_the_session_that_picks_the_card_up(self):
        _, src = self.native_log('session-a')
        item = self.run_cli(['add', '--json'], {'title': 'A 会话记下、B 会话接手', 'sources': [src]})['item']
        log = self.pickup_log('session-b')
        self.env['CLAUDE_CODE_SESSION_ID'] = 'session-b'
        resumed = self.run_cli(['choose', item['id'], 'continue', '--json'])
        self.assertTrue(resumed['committed'])
        self.assertEqual(resumed['session_linked'], {'linked': True, 'reason': 'picked_up'})
        self.assertEqual((resumed['item']['rev'], resumed['item']['state']), (2, 'open'))
        added = resumed['item']['sources'][-1]
        self.assertEqual((added['session'], added['excerpt']), ('session-b', '继续做那件事'))
        self.assertEqual(resumed['item']['log'][-1]['action'], 'append_sources')
        self.assertIn('A 会话记下、B 会话接手', self.prompt_hook('session-b'), '接手后 B 每轮也收到提醒')
        again = self.run_cli(['choose', item['id'], 'continue', '--json'])
        self.assertEqual(again['session_linked'], {'linked': False, 'reason': 'already_source'})
        self.assertEqual((again['committed'], again['item']['rev']), (False, 2))
        self.append_human(log, 'session-b', 'pickup-2', '做完了吗', datetime.now(timezone.utc).isoformat())
        listed = self.run_cli(['list', '--json'])['items'][0]
        self.assertTrue(listed['possibly_stale'], '回顾时也查 B 的日志')
        self.assertEqual({f['session']: f['user_messages'] for f in listed['followups']}, {'session-a': 0, 'session-b': 1})

    def test_pickup_never_blocks_continue_or_set(self):
        ident = self.run_cli(['add', '--json'], payload(title='接手登记不影响命令本身'))['item']['id']
        base = dict(self.env)
        cases = [({}, 'no_session_env'),
                 ({'CLAUDE_CODE_SESSION_ID': 'b', 'CODEX_THREAD_ID': 'c'}, 'ambiguous_session_env'),
                 ({'CLAUDE_CODE_SESSION_ID': 'no-log'}, 'log_not_found')]
        for extra, reason in cases:
            self.env = {**base, **extra}
            resumed = self.run_cli(['choose', ident, 'continue', '--json'])
            self.assertEqual(resumed['session_linked'], {'linked': False, 'reason': reason})
            self.assertEqual((resumed['committed'], resumed['item']['rev']), (False, 1))
        self.env = {**base, 'CLAUDE_CODE_SESSION_ID': 'session-b'}
        self.pickup_log('session-b')
        preview = self.run_cli(['choose', ident, 'continue', '--dry-run', '--json'])
        self.assertEqual((preview['session_linked']['linked'], preview['committed']), (True, False))
        preview = self.run_cli(['set', ident, '--rev', '1', '--dry-run', '--json'], {'stopped_at': '预演'})
        self.assertEqual((preview['item']['rev'], preview['item']['stopped_at'], preview['committed']), (3, '预演', False))
        self.assertEqual(self.run_cli(['show', ident, '--json'])['item']['rev'], 1)
        changed = self.run_cli(['set', ident, '--rev', '1', '--json'], {'stopped_at': 'B 会话接手推进'})
        self.assertEqual(changed['session_linked'], {'linked': True, 'reason': 'picked_up'})
        self.assertEqual((changed['item']['rev'], changed['item']['stopped_at']), (3, 'B 会话接手推进'))
        self.assertIn('session-b', [s['session'] for s in changed['item']['sources']])
        self.run_cli(['choose', ident, 'later', '--rev', '3', '--json'], decision())
        self.env = {**base, 'CLAUDE_CODE_SESSION_ID': 'session-c'}
        self.pickup_log('session-c')
        parked = self.run_cli(['choose', ident, 'continue', '--json'])
        self.assertEqual(parked['session_linked'], {'linked': False, 'reason': 'not_active'})
        self.assertEqual((parked['item']['state'], parked['item']['rev']), ('parked', 4))

    def test_codex_session_picks_up_from_rollout_log(self):
        ident = self.run_cli(['add', '--json'], payload(title='Codex 接手'))['item']['id']
        sid = '019a0000-0000-7000-8000-000000000001'
        directory = Path(self.tmp.name) / '.codex/sessions/2026/10/29'
        directory.mkdir(parents=True)
        rows = [{'type': 'session_meta', 'timestamp': '2026-10-29T00:00:00Z',
                 'payload': {'id': sid, 'source': 'cli', 'thread_source': 'user'}},
                {'type': 'event_msg', 'timestamp': '2026-10-29T00:00:01Z',
                 'payload': {'type': 'item_completed', 'item': {'type': 'UserMessage', 'id': 'u1',
                                                                'content': [{'type': 'text', 'text': '接着做 Codex 那件事'}]}}}]
        log = directory / f'rollout-2026-10-29T00-00-00-{sid}.jsonl'
        log.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        self.env['CODEX_THREAD_ID'] = sid
        resumed = self.run_cli(['choose', ident, 'continue', '--json'])
        self.assertTrue(resumed['session_linked']['linked'], resumed['session_linked'])
        added = resumed['item']['sources'][-1]
        self.assertEqual((added['platform'], added['session'], added['excerpt']), ('codex', sid, '接着做 Codex 那件事'))

    def test_parallel_writers_do_not_lose_items(self):
        processes = []
        for i in range(8):
            item = payload(title='parallel-' + str(i), sources=[source(session='s-' + str(i))])
            input_file = Path(self.tmp.name) / f'input-{i}.json'
            input_file.write_text(json.dumps(item), encoding='utf-8')
            processes.append(subprocess.Popen([*self.base, 'thread', 'add', '--json', '--input-file', str(input_file)],
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              text=True, encoding='utf-8', env=self.env, cwd=self.tmp.name))
        identifiers = set()
        for process in processes:
            out, err = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, out + err)
            identifiers.add(json.loads(out)['data']['id'])
        self.assertEqual(len(identifiers), 8)
        self.assertEqual(len(self.run_cli(['list', '--json'])['items']), 8)


if __name__ == '__main__':
    unittest.main()
