"""Task/session associations use isolated state, synthetic history and real processes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr, contextmanager
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pstate
import pstate_lock as L
import pstate_sessions as S
from pstate_threads import cli, store
from pstate_threads.core import ThreadError
from test_threads import source, payload, decision


class SessionLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'state'
        self.task = self.root / 'P2601-test' / 'task' / 'T01-test'
        self.task.mkdir(parents=True)
        self.path = self.task / '会话.md'
        self.env = {**os.environ, 'PSTATE_HOME': str(self.home)}
        for name in ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID'):
            self.env.pop(name, None)
        self.who = S.identity('codex', 'test-session', host='local-test')

    def rows(self):
        return S.parse_document(self.path.read_bytes())[2]

    def link(self, who=None, reasons=('link',), **kwargs):
        return S.link_sessions(self.home, str(self.task), [(who or self.who, reasons)], **kwargs)

    def run_cli(self, args, env=None, stdin=None):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/pstate.py'), *args],
                              input=stdin, capture_output=True, text=True, encoding='utf-8',
                              env=self.env if env is None else env, cwd=self.root, timeout=30)

    def test_idempotent_tags_and_first_time(self):
        self.link(reasons=['claim'])
        first = self.rows()[0]['first']
        self.link(reasons=['claim', 'link', 'free | 标签\n<&>'])
        saved = self.path.read_bytes()
        result = self.link(reasons=['link'])
        self.assertFalse(result['changed'])
        self.assertEqual(saved, self.path.read_bytes())
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['first'], first)
        self.assertEqual(set(self.rows()[0]['reasons']), {'claim', 'link', 'free | 标签\n<&>'})
        self.assertEqual(self.rows()[0]['start_dir'], '')

    def test_identity_includes_host_platform_session_and_agent(self):
        self.link()
        for field, value in [('host', 'other-host'), ('platform', 'claude'), ('session', 'other-session'), ('agent', 'agent-1')]:
            self.link(who={**self.who, field: value})
        self.assertEqual(len(self.rows()), 5)

    def test_manual_content_bom_and_crlf_preserved(self):
        self.link()
        region = self.path.read_text(encoding='utf-8').split(S.BEGIN, 1)[1].split(S.END, 1)[0]
        prefix = '\ufeff# 人工标题\r\n\r\n保留  \r\n'
        suffix = '\r\n\r\n## 人工结论\r\n原样保留，无换行'
        self.path.write_bytes((prefix + S.BEGIN + region.replace('\n', '\r\n') + S.END + suffix).encode())
        self.link(reasons=['take'])
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(prefix.encode()))
        self.assertTrue(raw.endswith(suffix.encode()))
        self.assertNotIn(b'\n', raw[len(prefix.encode()):-len(suffix.encode())].replace(b'\r\n', b''))

    def test_malformed_managed_regions_never_overwritten(self):
        self.link()
        valid = self.path.read_bytes()
        samples = [b'# manual only\n', valid.replace(S.END.encode(), b''),
                   valid + S.BEGIN.encode(), valid + S.END.encode(),
                   valid.replace(S.HEADER.encode(), b'| changed |'),
                   valid.replace(S.SEPARATOR.encode(), b'bad separator'),
                   valid.replace(b'| local-test |', b'| local | extra |'),
                   valid.replace(b'| codex |', b'| other |'),
                   valid.replace(b'["link"]', b'not-json'),
                   valid.replace(b'["link"]', b'[]'), b'\xff\xfe',
                   valid.replace(S.BEGIN.encode(), (S.END + '\n' + S.BEGIN).encode())]
        # Duplicate a valid table row: ambiguity must not silently discard content.
        row = next(l for l in valid.splitlines(keepends=True) if b'local-test' in l)
        samples.append(valid.replace(row, row + row))
        for raw in samples:
            with self.subTest(raw=raw[:60]):
                self.path.write_bytes(raw)
                with self.assertRaises((ValueError, UnicodeError)):
                    self.link(reasons=['claim'])
                self.assertEqual(self.path.read_bytes(), raw)

    def test_atomic_failure_and_concurrent_manual_edit(self):
        self.link()
        original = self.path.read_bytes()
        with patch.object(S.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.link(reasons=['claim'])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.task.glob('.pstate-sessions-*')), [])
        real_fsync = S.os.fsync
        def manual_edit(fd):
            real_fsync(fd)
            self.path.write_bytes(original + b'\nmanual edit\n')
        with patch.object(S.os, 'fsync', side_effect=manual_edit):
            with self.assertRaisesRegex(S.LinkError, '修改'):
                self.link(reasons=['take'])
        self.assertEqual(self.path.read_bytes(), original + b'\nmanual edit\n')

    def test_start_directory_only_from_explicit_provenance(self):
        self.link()
        self.link(who={**self.who, 'start_dir': 'C:/original'})
        self.link(who={**self.who, 'start_dir': 'D:/disagrees'})
        self.assertEqual(self.rows()[0]['start_dir'], 'C:/original')

    def test_dry_run_and_real_write_share_validation(self):
        self.link(dry_run=True)
        self.assertFalse(self.path.exists())
        self.path.write_text('manual only', encoding='utf-8')
        for dry_run in (True, False):
            with self.assertRaises(S.LinkError):
                self.link(dry_run=dry_run)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'manual only')

    def test_lock_is_outside_task_and_shared_by_real_path(self):
        self.link()
        locks = list((self.home / 'session-links').glob('*/' + L.MUTEX_FILE))
        self.assertEqual(len(locks), 1)
        self.assertEqual([p.name for p in self.task.iterdir()], ['会话.md'])
        alias = str(self.task / '..' / self.task.name)
        S.link_sessions(self.home, alias, [(self.who, ['take'])])
        self.assertEqual(len(list((self.home / 'session-links').iterdir())), 1)
        self.assertEqual(set(self.rows()[0]['reasons']), {'link', 'take'})

    def test_two_processes_keep_all_links(self):
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pstate_sessions as S
home, task, gate, ready, prefix = sys.argv[2:]
Path(ready).write_text('ready', encoding='utf-8')
end = time.monotonic() + 20
while not Path(gate).exists():
    if time.monotonic() > end: raise TimeoutError('gate')
    time.sleep(.01)
for i in range(8):
    S.link_sessions(home, task, [(S.identity('codex', prefix + str(i)), ['link'])])
"""
        gate = self.root / 'go'
        workers = []
        try:
            for prefix in ('one-', 'two-'):
                ready = self.root / (prefix + 'ready')
                proc = subprocess.Popen([sys.executable, '-B', '-c', code, str(ROOT / 'scripts'), str(self.home),
                                         str(self.task), str(gate), str(ready), prefix], env=self.env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                workers.append((proc, ready))
            deadline = time.monotonic() + 20
            while not all(p.exists() for _, p in workers):
                if time.monotonic() > deadline:
                    self.fail('workers failed to start')
                time.sleep(.01)
            gate.write_text('go', encoding='utf-8')
            for proc, _ in workers:
                out, err = proc.communicate(timeout=30)
                self.assertEqual(proc.returncode, 0, err.decode('utf-8', 'replace'))
        finally:
            for proc, _ in workers:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
        self.assertEqual({row['session'] for row in self.rows()}, {p + str(i) for p in ('one-', 'two-') for i in range(8)})

    def test_link_explicit_ignores_ambiguous_environment_and_live_process(self):
        env = {**self.env, 'CLAUDE_CODE_SESSION_ID': 'parent', 'CODEX_THREAD_ID': 'child'}
        result = self.run_cli(['link', str(self.task), '--platform', 'codex', '--session', 'historical',
                               '--agent', 'branch-1', '--reason', 'manual | reason', '--json'], env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        row = self.rows()[0]
        self.assertEqual((row['session'], row['agent'], row['start_dir']), ('historical', 'branch-1', ''))
        self.assertEqual(set(row['reasons']), {'link', 'manual | reason'})
        self.assertFalse((self.home / 'claims').exists())

    def test_link_environment_defaults(self):
        for variable, plat in [('CLAUDE_CODE_SESSION_ID', 'claude'), ('CODEX_THREAD_ID', 'codex')]:
            result = self.run_cli(['link', str(self.task)], {**self.env, variable: plat + '-native'})
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({r['platform'] for r in self.rows()}, {'claude', 'codex'})

    def test_link_missing_ambiguous_or_partial_identity_rejected(self):
        for extra in ({}, {'CLAUDE_CODE_SESSION_ID': 'a', 'CODEX_THREAD_ID': 'b'}):
            result = self.run_cli(['link', str(self.task), '--json'], {**self.env, **extra})
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertFalse(json.loads(result.stderr)['ok'])
            self.assertFalse(self.path.exists())
        for args in (['--platform', 'codex'], ['--session', 'id'], ['--platform', 'other', '--session', 'id']):
            result = self.run_cli(['link', str(self.task), *args, '--json'])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertEqual(json.loads(result.stderr)['error']['code'], 'E_VALIDATION')

    def test_link_format_help_dry_run_stdin_and_bad_region(self):
        base = ['link', str(self.task), '--platform', 'codex', '--session', 'id']
        result = self.run_cli([*base, '--reason', '-', '--format', 'json', '--dry-run'], stdin='来自 stdin | 标签')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['data']['dry_run'])
        self.assertFalse(self.path.exists())
        result = self.run_cli([*base, '--reason', '-'], stdin='来自 stdin | 标签')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('来自 stdin | 标签', self.rows()[0]['reasons'])
        self.path.write_text('manual only', encoding='utf-8')
        result = self.run_cli([*base, '--json'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'manual only')
        result = self.run_cli(['link', '--ai-help', '--json'])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith('---\nname: pstate-link'))

    def as_claim(self, sid, command, target=None):
        who = {'sid': sid, 'pid': 123, 'ctime': 456}
        backend = unittest.mock.Mock()
        backend.alive.return_value = True
        args = [command] + ([str(target or self.task)] if command in ('claim', 'take') else [])
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(pstate, 'me_from_env', return_value=(who, [])), \
                patch.object(L, 'process_backend', return_value=backend), patch.object(pstate, 'repo_context', side_effect=RuntimeError('not git')), \
                redirect_stdout(out), redirect_stderr(err):
            rc = pstate.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_claim_take_and_release_preserve_associations(self):
        self.assertEqual(self.as_claim('a', 'claim')[0], 0)
        self.assertEqual(self.as_claim('a', 'claim')[0], 0)
        self.assertEqual(self.as_claim('b', 'take')[0], 0)
        self.assertEqual({r['session']: r['reasons'] for r in self.rows()}, {'a': ['claim'], 'b': ['take']})
        original = self.path.read_bytes()
        self.assertEqual(self.as_claim('b', 'release')[0], 0)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(L.load_claims(self.home / 'claims'), {})
        with patch.dict(os.environ, self.env, clear=True), patch.object(pstate, 'read_hook_input', return_value={'session_id': 'a'}):
            self.assertEqual(pstate.cmd_hook_end(None), 0)
        self.assertEqual(self.path.read_bytes(), original)

    def test_claim_and_take_link_failure_reports_committed_claim(self):
        self.path.write_text('broken region', encoding='utf-8')
        rc, out, err = self.as_claim('a', 'claim')
        self.assertEqual(rc, 1)
        self.assertIn('已认领', out)
        self.assertIn('关联会话失败', err)
        self.assertIn("'--reason' 'claim'", err)
        rc, out, err = self.as_claim('b', 'take')
        self.assertEqual(rc, 1)
        self.assertIn('已接手任务', out)
        self.assertIn("'--reason' 'take'", err)
        self.assertEqual(next(iter(L.load_claims(self.home / 'claims').values()))['sid'], 'b')
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'broken region')

    def test_denied_claim_or_take_does_not_link(self):
        self.assertEqual(self.as_claim('a', 'claim')[0], 0)
        original = self.path.read_bytes()
        self.assertEqual(self.as_claim('b', 'claim')[0], 1)
        self.assertEqual(self.as_claim('a', 'take')[0], 1)
        self.assertEqual(self.path.read_bytes(), original)

    def decided(self, sources=None):
        directory = self.home / 'threads'
        item = store.mutate(directory, 'add', payload(sources=sources or [source()]))['item']
        return store.mutate(directory, 'do', decision(), ident=item['id'], rev=item['rev'], verified={'verified': True})['item']

    def transfer(self, item, *, task=None, dry_run=False, env=None):
        data = {'to': str(task or self.task), 'evidence': ['synthetic handoff reviewed']}
        args = cli.parser().parse_args(['transfer', item['id'], '--rev', str(item['rev'])] + (['--dry-run'] if dry_run else []))
        with patch.dict(os.environ, self.env if env is None else env, clear=True), patch.object(cli, 'read_input', return_value=data):
            return cli.dispatch(args, self.home / 'threads')

    def test_transfer_sources_executor_and_same_target_replay(self):
        srcs = [source(session='prior', anchor='one'), source(session='prior', anchor='two'),
                {**source(session='prior'), 'agentId': 'agent-1', 'host': 'remote-test', 'start_dir': 'C:/start'}]
        item = self.decided(srcs)
        env = {**self.env, 'CODEX_THREAD_ID': 'executor'}
        result = self.transfer(item, env=env)
        self.assertTrue(result['transfer_succeeded'])
        rows = self.rows()
        self.assertEqual(len(rows), 4)  # do decision adds its own original source
        prior = next(r for r in rows if r['session'] == 'prior' and not r['agent'])
        self.assertTrue({'立项前来源', '事项:' + item['id'], '锚点:one', '锚点:two'} <= set(prior['reasons']))
        agent = next(r for r in rows if r['agent'])
        self.assertEqual((agent['agent'], agent['host'], agent['start_dir']), ('agent-1', 'remote-test', 'C:/start'))
        executor = next(r for r in rows if r['session'] == 'executor')
        self.assertIn('转交执行', executor['reasons'])
        saved = store.read(self.home / 'threads')
        self.path.unlink()  # emulate missing association output after successful transfer
        replay = self.transfer(result['item'], env=env)
        self.assertTrue(replay['replayed'])
        self.assertFalse(replay['committed'])
        self.assertEqual(store.read(self.home / 'threads'), saved)
        self.assertEqual(len(self.rows()), 4)
        original = self.path.read_bytes()
        self.transfer(result['item'], env=env)
        self.assertEqual(self.path.read_bytes(), original)

    def test_transfer_different_task_refused_and_dry_run_has_no_links(self):
        item = self.decided()
        env = {**self.env, 'CLAUDE_CODE_SESSION_ID': 'executor'}
        preview = self.transfer(item, dry_run=True, env=env)
        self.assertFalse(preview['committed'])
        self.assertFalse(self.path.exists())
        self.assertEqual(store.read(self.home / 'threads')['items'][item['id']]['state'], 'decided')
        result = self.transfer(item, env=env)
        other = self.task.with_name('T02-other')
        other.mkdir()
        with self.assertRaisesRegex(ThreadError, 'different task'):
            self.transfer(result['item'], task=other, env=env)
        self.assertFalse((other / '会话.md').exists())

    def test_transfer_partial_failure_can_repair_without_reopen(self):
        item = self.decided()
        env = {**self.env, 'CODEX_THREAD_ID': 'executor'}
        real_link = S.link_sessions
        def fail_executor(home, task, entries, **kw):
            if entries[0][0]['session'] == 'executor':
                raise OSError('injected write failure')
            return real_link(home, task, entries, **kw)
        with patch.object(S, 'link_sessions', side_effect=fail_executor):
            with self.assertRaises(ThreadError) as caught:
                self.transfer(item, env=env)
        error = caught.exception
        self.assertEqual((error.status, error.code), (1, 'E_PARTIAL_FAILURE'))
        self.assertTrue(error.details['transfer_succeeded'])
        self.assertTrue(error.details['associations']['linked'])
        self.assertIn('link', error.details['associations']['failures'][0]['repair_command'])
        committed = store.read(self.home / 'threads')['items'][item['id']]
        self.assertEqual(committed['state'], 'transferred')
        self.assertNotIn('executor', {r['session'] for r in self.rows()})
        self.transfer(committed, env=env)
        self.assertIn('executor', {r['session'] for r in self.rows()})

    def test_transfer_broken_region_and_ambiguous_executor_are_explicit(self):
        item = self.decided()
        self.path.write_text('manual only', encoding='utf-8')
        with self.assertRaises(ThreadError) as caught:
            self.transfer(item, env={**self.env, 'CODEX_THREAD_ID': 'x', 'CLAUDE_CODE_SESSION_ID': 'y'})
        self.assertTrue(caught.exception.details['transfer_succeeded'])
        self.assertGreaterEqual(len(caught.exception.details['associations']['failures']), 2)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'manual only')
        committed = store.read(self.home / 'threads')['items'][item['id']]
        self.path.unlink()
        self.transfer(committed, env={**self.env, 'CODEX_THREAD_ID': 'executor'})
        self.assertEqual(len(self.rows()), 2)

    def test_transfer_cli_partial_error_is_structured(self):
        item = self.decided()
        data = json.dumps({'to': str(self.task), 'evidence': ['test handoff']})
        result = self.run_cli(['thread', 'transfer', item['id'], '--rev', str(item['rev']), '--json'], stdin=data)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        error = json.loads(result.stderr)['error']
        self.assertEqual(error['code'], 'E_PARTIAL_FAILURE')
        self.assertTrue(error['details']['transfer_succeeded'])
        self.assertEqual(error['details']['item']['state'], 'transferred')

    def test_association_lock_never_nests_with_claim_or_thread_locks(self):
        active = []
        real_mutex = L.mutex
        @contextmanager
        def checked(directory):
            self.assertEqual(active, [], 'short locks must not nest')
            with real_mutex(directory):
                active.append(str(directory))
                try:
                    yield
                finally:
                    active.pop()
        with patch.object(L, 'mutex', checked):
            self.assertEqual(self.as_claim('a', 'claim')[0], 0)
            self.assertEqual(self.as_claim('b', 'take')[0], 0)
            item = self.decided()
            self.transfer(item, env={**self.env, 'CODEX_THREAD_ID': 'executor'})
        self.assertEqual(active, [])

    def test_transfer_dry_run_rejects_bad_region_on_the_same_writer(self):
        item = self.decided()
        self.path.write_text('manual only', encoding='utf-8')
        saved = store.read(self.home / 'threads')
        with self.assertRaises(ThreadError) as caught:
            self.transfer(item, dry_run=True, env={**self.env, 'CODEX_THREAD_ID': 'executor'})
        self.assertFalse(caught.exception.details['transfer_succeeded'])
        self.assertEqual(store.read(self.home / 'threads'), saved)
        self.assertEqual(self.path.read_text(encoding='utf-8'), 'manual only')

    def test_transfer_merges_source_and_executor_before_each_write(self):
        srcs = [source(session='executor', anchor='one'), source(session='executor', anchor='two')]
        item = self.decided(srcs)
        real_link = S.link_sessions
        with patch.object(S, 'link_sessions', wraps=real_link) as writes:
            result = self.transfer(item, env={**self.env, 'CLAUDE_CODE_SESSION_ID': 'executor'})
        self.assertEqual(writes.call_count, 2)  # executor plus the source added by the verified do
        row = next(r for r in self.rows() if r['session'] == 'executor')
        self.assertTrue({'立项前来源', '转交执行', '锚点:one', '锚点:two'} <= set(row['reasons']))
        self.assertEqual(len(result['associations']['linked']), 2)

    def test_repair_command_quotes_free_text_for_powershell(self):
        command = S.repair_command(self.task, self.who, ["tag '$() | &"])
        self.assertTrue(command.startswith("& '"))
        self.assertIn("'--reason' 'tag ''$() | &'", command)

    def test_source_stream_identity_preserved(self):
        from pstate_threads.core import source_key
        a = source()
        b = {**a, 'agentId': 'agent-1', 'host': 'local-test'}
        item = store.mutate(self.home / 'threads', 'add', payload(sources=[a, b]))['item']
        self.assertEqual(len(item['sources']), 2)
        self.assertNotEqual(source_key(a), source_key(b))


if __name__ == '__main__':
    unittest.main()
