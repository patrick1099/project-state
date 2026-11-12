"""User-edited Obsidian cards, isolated persistence and joint reminders."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from pstate_threads import store
from pstate_threads.core import ThreadError
from pstate_sweep import core, migration, runtime
from sweep_helpers import NOW, FakeGate, FakeJudge, make_cfg


def source(text='记住这个想法'):
    return {'platform': 'claude', 'session': 'card-fixture', 'anchor': 'message-1',
            'time': '2026-11-01T00:00:00Z', 'role': 'user', 'excerpt': text,
            'excerpt_hash': hashlib.sha256(text.encode()).hexdigest()}


class ThreadCardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        self.directory = self.home / 'threads'
        self.out = self.root / 'output'
        self.env = patch.dict(os.environ, {'PSTATE_HOME': str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cfg = make_cfg(self.root)
        migration.switch(self.cfg, self.home, self.out)

    def add(self, **changes):
        value = {'title': '独立事项', 'motivation': '原始动机',
                 'stopped_at': '还在讨论', 'sources': [source()]}
        value.update(changes)
        return store.mutate(self.directory, 'add', value)['item']

    def current(self, item):
        return store.read(self.directory)['items'][item['id']]

    def card(self, item):
        result = store.card(self.current(item), self.directory)['card']
        if result['body'] is not None:
            result['body'] = result['body'].replace('\r\n', '\n')
        return result

    def card_path(self, item):
        return Path(self.card(item)['path'])

    def append(self, item, text='\n## 人工补充\n用户自己的详细想法。\n'):
        path = self.card_path(item)
        body = path.read_text(encoding='utf-8') + text
        path.write_text(body, encoding='utf-8')
        return self.card(item)['body']

    def business_bytes(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()
                and path.suffix in ('.md', '.json')
                and 'locks' not in path.name}

    def test_remember_creates_one_card_with_stable_identity(self):
        item = self.add()
        path = self.card_path(item)
        self.assertTrue(path.is_file())
        result = self.card(item)
        self.assertEqual(Path(result['path']), path)
        self.assertIn('原始动机', result['body'])
        self.assertEqual(result['sha256'], hashlib.sha256(store.card(item, self.directory)['card']['body'].encode()).hexdigest())
        again = store.mutate(self.directory, 'add', {
            'title': '独立事项', 'motivation': '原始动机',
            'stopped_at': '还在讨论', 'sources': [source()]})
        self.assertEqual(again['id'], item['id'])
        self.assertEqual(list(path.parent.glob('*.md')), [path])
        self.assertIn(item['id'], path.name)
        self.assertIn('独立事项', path.name)

    def test_manual_body_and_title_are_read_from_obsidian(self):
        item = self.add()
        self.append(item)
        path = self.card_path(item)
        text = path.read_text(encoding='utf-8').replace('# 独立事项', '# 用户改过标题')
        path.write_text(text, encoding='utf-8')
        body = self.card(item)['body']
        shown = self.card(item)
        self.assertEqual(shown['body'], body)
        self.assertIn('用户改过标题', shown['body'])
        snapshot = store.snapshot(self.directory)
        found = snapshot['items'][item['id']]
        self.assertEqual(found['card']['body'].replace('\r\n', '\n'), body)

    def test_structured_updates_preserve_user_added_body(self):
        item = self.add()
        body = self.append(item)
        store.mutate(self.directory, 'set', {'title': '新结构标题', 'stopped_at': '新停点'},
                     ident=item['id'], rev=item['rev'])
        actual = self.card(item)['body']
        self.assertIn('用户自己的详细想法。', actual)
        self.assertEqual(self.current(item)['stopped_at'], '新停点')
        self.assertIn('新停点', actual)
        self.assertIn('新结构标题', actual)

    def test_append_sources_preserves_manual_body(self):
        item = self.add()
        body = self.append(item)
        more = source('补充讨论')
        more['anchor'] = 'message-2'
        store.mutate(self.directory, 'add', {'sources': [more]},
                     into=item['id'], rev=item['rev'])
        self.assertEqual(self.card(item)['body'], body)
        self.assertEqual(len(self.current(item)['sources']), 2)

    def test_exact_edit_changes_body_and_revision(self):
        item = self.add()
        before = self.card(item)
        result = store.edit_card(self.directory, item['id'], item['rev'], {
            'expected_sha256': before['sha256'],
            'edits': [{'old': '原始动机', 'new': '经用户补充的动机', 'count': 1}]})
        self.assertTrue(result['committed'])
        self.assertGreater(result['item']['rev'], item['rev'])
        self.assertIn('经用户补充的动机', self.card(item)['body'])

    def test_stale_hash_rejects_after_human_edit_without_losing_text(self):
        item = self.add()
        stale = self.card(item)
        body = self.append(item)
        before = (self.directory / 'threads.json').read_bytes()
        with self.assertRaises(ThreadError):
            store.edit_card(self.directory, item['id'], item['rev'], {
                'expected_sha256': stale['sha256'],
                'edits': [{'old': '原始动机', 'new': '丢掉人工编辑'}]})
        self.assertEqual(self.card(item)['body'], body)
        self.assertEqual((self.directory / 'threads.json').read_bytes(), before)

    def test_second_editor_with_old_revision_cannot_overwrite_first(self):
        item = self.add()
        original = self.card(item)
        store.edit_card(self.directory, item['id'], item['rev'], {
            'expected_sha256': original['sha256'],
            'edits': [{'old': '原始动机', 'new': '编辑者甲'}]})
        body = self.card_path(item).read_bytes()
        with self.assertRaises(ThreadError):
            store.edit_card(self.directory, item['id'], item['rev'], {
                'expected_sha256': original['sha256'],
                'edits': [{'old': '原始动机', 'new': '编辑者乙'}]})
        self.assertEqual(self.card_path(item).read_bytes(), body)

    def test_exact_edit_mismatch_preserves_database_and_body(self):
        item = self.add()
        original = self.business_bytes()
        with self.assertRaises(ThreadError):
            store.edit_card(self.directory, item['id'], item['rev'], {
                'expected_sha256': self.card(item)['sha256'],
                'edits': [{'old': '不存在的片段', 'new': '替换'}]})
        self.assertEqual(self.business_bytes(), original)

    def test_state_and_transfer_keep_manual_body_and_link_task(self):
        item = self.add()
        body = self.append(item)
        decision = {'said': '决定做', 'source': source('决定做')}
        item = store.mutate(self.directory, 'do', decision, ident=item['id'], rev=item['rev'],
                            verified={'verified': True, 'reason': 'fixture'})['item']
        task = self.root / 'vault' / 'P2601-x' / 'task' / 'T01-真实任务'
        task.mkdir(parents=True)
        item = store.mutate(self.directory, 'transfer',
            {'to': str(task), 'evidence': ['任务主文档已回读']},
            ident=item['id'], rev=item['rev'])['item']
        self.assertEqual(item['state'], 'transferred')
        self.assertEqual(self.card(item)['body'], body)
        shown = store.card(item, self.directory)
        self.assertEqual(shown['transferred_facts']['path'], str(task))
        self.assertTrue(shown['transferred_facts']['exists'])
        card_text = self.card_path(item).read_text(encoding='utf-8')
        self.assertIn('已转交', card_text)
        self.assertIn('T01-真实任务', card_text)
        self.assertTrue('[[' in card_text or '](' in card_text)

    def test_done_and_reopen_preserve_manual_body(self):
        item = self.add()
        body = self.append(item)
        item = store.mutate(self.directory, 'done', {'evidence': ['临时产物已验证']},
                            ident=item['id'], rev=item['rev'])['item']
        item = store.mutate(self.directory, 'reopen',
            {'said': '重新讨论', 'source': source('重新讨论')},
            ident=item['id'], rev=item['rev'])['item']
        self.assertEqual(item['state'], 'open')
        self.assertEqual(self.card(item)['body'], body)

    def test_sync_is_idempotent_and_never_replaces_user_body(self):
        item = self.add()
        body = self.append(item)
        store.sync_cards(self.directory)
        first = self.business_bytes()
        store.sync_cards(self.directory)
        self.assertEqual(self.business_bytes(), first)
        self.assertEqual(self.card(item)['body'], body)

    def test_missing_bound_card_is_not_silently_recreated(self):
        item = self.add()
        self.card_path(item).unlink()
        before = (self.directory / 'threads.json').read_bytes()
        shown = self.card(item)
        self.assertEqual(shown['sync_status'], 'missing')
        self.assertIsNone(shown['body'])
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'set', {'title': '不能覆盖'},
                         ident=item['id'], rev=item['rev'])
        self.assertFalse(self.card_path(item).exists())
        self.assertEqual((self.directory / 'threads.json').read_bytes(), before)

    def test_dry_run_edit_sync_and_mutate_write_no_business_files(self):
        item = self.add()
        original = self.business_bytes()
        store.edit_card(self.directory, item['id'], item['rev'], {
            'expected_sha256': self.card(item)['sha256'],
            'edits': [{'old': '原始动机', 'new': '预演内容'}]}, dry_run=True)
        store.sync_cards(self.directory, dry_run=True)
        store.mutate(self.directory, 'set', {'title': '预演标题'},
                     ident=item['id'], rev=item['rev'], dry_run=True)
        store.mutate(self.directory, 'add', {'title': '预演新卡', 'sources': [source('另一件事')]},
                     distinct=True, dry_run=True)
        self.assertEqual(self.business_bytes(), original)

    def test_manual_body_change_gets_new_reminder_key(self):
        item = self.add()
        old = store.card(item, self.directory)
        key = core.thread_closure_key(old)
        self.append(item)
        new = store.card(item, self.directory)
        self.assertNotEqual(core.thread_closure_key(new), key)
        self.assertEqual(new['rev'], old['rev'])
        self.assertIn(new['card']['sha256'], core.thread_closure_key(new))

    def test_checked_reminder_does_not_hide_later_human_edit(self):
        item = self.add()
        def run():
            return runtime.run_sweep(self.cfg, FakeJudge(), lambda: FakeGate(),
                self.out, self.home / 'sweep', now=NOW, threads_dir=self.directory, no_judge=True)
        run()
        page = self.out / self.cfg['page_name']
        text = page.read_text(encoding='utf-8')
        self.assertIn('%%pt:', text)
        text = '\n'.join(line.replace('[ ]', '[x]', 1) if '%%pt:' in line else line
                         for line in text.splitlines()) + '\n'
        page.write_text(text, encoding='utf-8')
        run()
        self.assertNotIn('%%pt:', page.read_text(encoding='utf-8'))
        self.append(item, '\n用户勾选之后的新正文。\n')
        run()
        current = page.read_text(encoding='utf-8')
        self.assertIn('%%pt:', current)
        self.assertIn('用户勾选之后的新正文', current)

    def test_no_output_mapping_does_not_write_to_real_vault(self):
        home = self.root / 'unconfigured-home'
        with patch.dict(os.environ, {'PSTATE_HOME': str(home)}):
            directory = home / 'threads'
            result = store.mutate(directory, 'add', {'title': '无配置', 'sources': [source()]})
        self.assertEqual(result['card_sync']['status'], 'unconfigured')
        self.assertFalse((home / 'sweep' / 'paths.json').exists())
        self.assertFalse((home / '事项卡片').exists())


    def test_structured_set_refuses_to_overwrite_user_rewritten_target(self):
        item = self.add()
        path = self.card_path(item)
        body = path.read_text(encoding='utf-8').replace('原始动机', '用户重新组织的动机')
        path.write_text(body, encoding='utf-8')
        before = (self.directory / 'threads.json').read_bytes()
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'set', {'motivation': 'AI后来给出的版本'},
                         ident=item['id'], rev=item['rev'])
        self.assertEqual(path.read_text(encoding='utf-8'), body)
        self.assertEqual((self.directory / 'threads.json').read_bytes(), before)

    def test_structured_set_refuses_ambiguous_duplicated_target(self):
        item = self.add()
        body = self.append(item, '\n## 动机\n\n原始动机\n')
        before = (self.directory / 'threads.json').read_bytes()
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'set', {'motivation': 'AI新动机'},
                         ident=item['id'], rev=item['rev'])
        self.assertEqual(self.card(item)['body'], body)
        self.assertEqual((self.directory / 'threads.json').read_bytes(), before)

    def test_freely_reordered_body_survives_precise_structured_set(self):
        item = self.add()
        path = self.card_path(item)
        body = path.read_text(encoding='utf-8')
        sections = body.split('\n## ')
        edited = sections[0] + '\n## ' + '\n## '.join(reversed(sections[1:]))
        path.write_text(edited, encoding='utf-8')
        expected = self.card(item)['body'].replace('原始动机', '有证据的新动机')
        store.mutate(self.directory, 'set', {'motivation': '有证据的新动机'},
                     ident=item['id'], rev=item['rev'])
        self.assertEqual(self.card(item)['body'], expected)

    def test_failed_write_can_retry_after_restoring_exact_card(self):
        item = self.add()
        path = self.card_path(item)
        self.append(item)
        body = path.read_text(encoding='utf-8')
        path.unlink()
        with self.assertRaises(ThreadError):
            store.mutate(self.directory, 'set', {'motivation': '恢复后可继续'},
                         ident=item['id'], rev=item['rev'])
        path.write_text(body, encoding='utf-8')
        result = store.mutate(self.directory, 'set', {'motivation': '恢复后可继续'},
                              ident=item['id'], rev=item['rev'])
        self.assertTrue(result['committed'])
        self.assertIn('恢复后可继续', path.read_text(encoding='utf-8'))
        self.assertIn('用户自己的详细想法。', path.read_text(encoding='utf-8'))

    def test_two_process_editors_do_not_lose_successful_edit(self):
        import subprocess
        import time
        item = self.add()
        sha = self.card(item)['sha256']
        script = (
            'import json,sys,time; '
            'sys.path.insert(0,sys.argv[1]); '
            'from pstate_threads import store; '
            'time.sleep(max(0,float(sys.argv[5])-time.time())); '
            'r=store.edit_card(sys.argv[2],sys.argv[3],1,json.loads(sys.argv[4])); '
            'print(json.dumps(r))'
        )
        deadline = time.time() + 1
        procs = []
        for text in ('进程甲的唯一结果', '进程乙的唯一结果'):
            payload = {'expected_sha256': sha, 'edits': [{'old': '原始动机', 'new': text}]}
            procs.append(subprocess.Popen([
                sys.executable, '-B', '-c', script, str(ROOT / 'scripts'),
                str(self.directory), item['id'], json.dumps(payload, ensure_ascii=False), str(deadline)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8'))
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        outputs = [p.communicate(timeout=20) for p in procs]
        self.assertEqual(sorted(p.returncode == 0 for p in procs), [False, True], outputs)
        body = self.card(item)['body']
        self.assertEqual(sum(text in body for text in ('进程甲的唯一结果', '进程乙的唯一结果')), 1)
        self.assertEqual(self.current(item)['rev'], 2)

    def test_sweep_holding_output_lease_allows_thread_mutation_without_deadlock(self):
        item = self.add()
        outer = self
        class MutatingJudge(FakeJudge):
            def judge(self, request):
                extra = source('后台新事项')
                extra['session'] = 'during-sweep'
                outer.add(title='后台判断期间收录的新事项', sources=[extra])
                return super().judge(request)
        from sweep_helpers import DAY, human, sid, write_transcript
        stamp = NOW - 5 * DAY
        write_transcript(self.cfg['projects_dir'], sid(9), [human('尚未决定的工具', stamp, cwd=str(self.root))])
        report = runtime.run_sweep(self.cfg, MutatingJudge(), lambda: FakeGate(),
            self.out, self.home / 'sweep', now=NOW, threads_dir=self.directory)
        self.assertGreaterEqual(report['calls'], 1, report)
        self.assertEqual(len(store.read(self.directory)['items']), 2)
        self.assertTrue(self.card_path(item).is_file())

    def test_sync_reports_bound_missing_and_conflict_as_failures(self):
        item = self.add()
        path = self.card_path(item)
        original = path.read_bytes()
        db_before = (self.directory / 'threads.json').read_bytes()
        path.unlink()
        with self.assertRaises(ThreadError) as missing:
            store.sync_cards(self.directory)
        self.assertEqual(missing.exception.code, 'E_PARTIAL_FAILURE')
        self.assertFalse(path.exists())
        self.assertEqual((self.directory / 'threads.json').read_bytes(), db_before)
        path.write_bytes(original.replace(b'pstate-card', b'changed-card', 1))
        conflict_before = path.read_bytes()
        with self.assertRaises(ThreadError) as conflict:
            store.sync_cards(self.directory)
        self.assertEqual(conflict.exception.code, 'E_PARTIAL_FAILURE')
        self.assertEqual(path.read_bytes(), conflict_before)
        self.assertEqual((self.directory / 'threads.json').read_bytes(), db_before)

    def test_preview_add_then_into_does_not_require_unwritten_card(self):
        before = self.business_bytes()
        preview = store.read(self.directory)
        first = store.mutate(self.directory, 'add',
            {'title': '预演第一张卡', 'sources': [source()]},
            dry_run=True, preview_state=preview)
        extra = source('后续另一个锚点')
        extra['anchor'] = 'message-2'
        second = store.mutate(self.directory, 'add', {'sources': [extra]},
            into=first['id'], rev=first['item']['rev'], dry_run=True, preview_state=preview)
        self.assertEqual(len(second['item']['sources']), 2)
        self.assertFalse(second['committed'])
        self.assertEqual(self.business_bytes(), before)
        self.assertFalse((self.out / '事项卡片').exists())

    def test_committed_add_recovers_original_request_after_card_write_failure(self):
        from pstate_threads import cards
        value = {'title': '原请求重复安全', 'sources': [source()]}
        with patch.object(cards, 'finish', side_effect=OSError('injected card disk failure')):
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'add', value)
        persisted = store.read(self.directory)
        self.assertEqual(len(persisted['items']), 1)
        item = next(iter(persisted['items'].values()))
        self.assertIn('pending', item['card_ref'])
        result = store.mutate(self.directory, 'add', value)
        self.assertEqual(result['id'], item['id'])
        self.assertEqual(len(store.read(self.directory)['items']), 1)
        self.assertTrue(self.card_path(item).is_file())
        self.assertNotIn('pending', self.current(item)['card_ref'])

    def test_committed_set_recovers_old_revision_without_duplicate_change(self):
        from pstate_threads import cards
        item = self.add()
        self.append(item)
        with patch.object(cards, 'finish', side_effect=OSError('injected card disk failure')):
            with self.assertRaises(ThreadError):
                store.mutate(self.directory, 'set', {'motivation': '只应用一次的修改'},
                             ident=item['id'], rev=item['rev'])
        self.assertEqual(self.current(item)['rev'], item['rev'] + 1)
        result = store.mutate(self.directory, 'set', {'motivation': '只应用一次的修改'},
                              ident=item['id'], rev=item['rev'])
        self.assertEqual(result['item']['rev'], item['rev'] + 1)
        body = self.card(item)['body']
        self.assertEqual(body.count('只应用一次的修改'), 1)
        self.assertIn('用户自己的详细想法。', body)

    def test_committed_edit_recovers_when_card_replaced_before_receipt_failure(self):
        from pstate_threads import cards
        item = self.add()
        self.append(item)
        value = {'expected_sha256': self.card(item)['sha256'],
                 'edits': [{'old': '原始动机', 'new': '卡已写成但回执失败'}]}
        real = cards.finish
        def finish_then_fail(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('injected failure after card replacement')
        with patch.object(cards, 'finish', side_effect=finish_then_fail):
            with self.assertRaises(ThreadError):
                store.edit_card(self.directory, item['id'], item['rev'], value)
        self.assertIn('卡已写成但回执失败', self.card(item)['body'])
        result = store.edit_card(self.directory, item['id'], item['rev'], value)
        self.assertEqual(result['item']['rev'], item['rev'] + 1)
        self.assertEqual(self.card(item)['body'].count('卡已写成但回执失败'), 1)
        self.assertIn('用户自己的详细想法。', self.card(item)['body'])

    def test_confirmed_quotes_show_verification_without_implying_decision(self):
        unverified = source('未经核验的摘录')
        unverified['anchor'] = 'quote-unverified'
        verified = source('已找到原文的摘录')
        verified['anchor'] = 'quote-verified'
        result = store.mutate(self.directory, 'add', {
            'title': '原话核验区别', 'sources': [unverified, verified],
            'confirmed': [
                {'said': unverified['excerpt'], 'source': unverified},
                {'said': verified['excerpt'], 'source': verified}]},
            verified={'confirmed_entries': [
                {'verified': False, 'reason': '原始锚点不可核验'},
                {'verified': True, 'reason': '合成原文核验通过'}]})
        item = result['item']
        body = self.card(item)['body']
        lines = body.splitlines()
        unverified_line = next(line for line in lines if '未经核验的摘录' in line)
        verified_line = next(line for line in lines if '已找到原文的摘录' in line)
        self.assertIn('未核验', unverified_line)
        self.assertIn('已核验', verified_line)
        self.assertIn('原始锚点不可核验', body)
        self.assertIn('核验只证明出处', body)
        self.assertEqual(item['state'], 'open')
        self.assertFalse(item['confirmed'][0]['verified'])
        self.assertTrue(item['confirmed'][1]['verified'])
        self.assertNotIn('transferred_to', item)

if __name__ == '__main__':
    unittest.main()