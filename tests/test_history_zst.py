"""Compressed native history is read and verified without touching real history."""
import json
import sys
import tempfile
import unittest
from compression import zstd
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pstate_history import core, discover, verify_source
from pstate_history.common import digest
from test_history import meta, human, assistant


class CompressedHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sessions = self.root / '.codex/sessions'
        self.archived = self.root / '.codex/archived_sessions'
        self.sessions.mkdir(parents=True)
        self.archived.mkdir()
        self.options = {'roots': {'claude': [], 'codex': [self.sessions, self.archived]}}

    def write(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        content = ('\ufeff' + '\n'.join(json.dumps(r) for r in rows) + '\n').encode('utf-8')
        path.write_bytes(zstd.compress(content) if path.name.endswith('.zst') else content)
        return path

    def test_discovery_includes_both_formats_in_both_roots(self):
        for index, (root, suffix) in enumerate([(self.sessions, '.jsonl'), (self.sessions, '.jsonl.zst'),
                                               (self.archived, '.jsonl'), (self.archived, '.jsonl.zst')]):
            self.write(root / 'nested' / ('s' + str(index) + suffix), [meta('sid' + str(index)), human()])
        with patch.object(core, 'DEFAULT_ROOTS', self.options['roots']):
            result = discover()
        self.assertEqual(result['coverage']['files'], 4)
        self.assertEqual(len(result['sessions']), 4)
        for session in result['sessions']:
            self.assertTrue(verify_source(session['sources'][0], self.options)['verified'])

    def test_same_id_non_prefix_files_keep_both_unique_messages(self):
        self.write(self.sessions / 's.jsonl.zst', [meta(), human('Only compressed', 'compressed')])
        self.write(self.archived / 's.jsonl', [meta(), human('Only plain', 'plain'), assistant()])
        result = discover(self.options)
        session = result['sessions'][0]
        self.assertEqual(len(session['paths']), 2)
        self.assertTrue({'Only compressed', 'Only plain'} <= {m['text'] for m in session['messages']})
        self.assertEqual(len(result['sessions']), 1)
        for src in session['sources']:
            if src['role'] == 'user':
                self.assertTrue(verify_source(src, self.options)['verified'])

    def test_identical_compressed_copy_does_not_duplicate_messages(self):
        rows = [meta(), human(), assistant()]
        self.write(self.sessions / 's.jsonl', rows)
        self.write(self.archived / 's.jsonl.zst', rows)
        result = discover(self.options)
        self.assertEqual(len(result['sessions']), 1)
        self.assertEqual(len(result['sessions'][0]['messages']), 2)
        self.assertEqual(len(result['sessions'][0]['paths']), 2)

    def test_truncated_zst_has_no_partial_messages_and_cannot_verify(self):
        path = self.write(self.archived / 's.jsonl.zst', [meta(), human()])
        good = discover(self.options)
        src = good['sessions'][0]['sources'][0]
        path.write_bytes(path.read_bytes()[:-1])
        result = discover(self.options)
        self.assertEqual(result['sessions'], [])
        self.assertEqual(result['coverage']['unreadable'], 1)
        self.assertTrue(any(w['kind'] == 'unreadable' for w in result['warnings']))
        self.assertFalse(verify_source(src, self.options)['verified'])

    def test_corrupt_and_malformed_zst_fail_safely(self):
        path = self.archived / 's.jsonl.zst'
        for content in (b'not-zstd', b'', zstd.compress(b'\xff')):
            path.write_bytes(content)
            result = discover(self.options)
            self.assertEqual(result['sessions'], [])
            self.assertGreaterEqual(result['coverage']['unreadable'], 1)
        self.write(path, [meta(), human()])
        src = discover(self.options)['sessions'][0]['sources'][0]
        content = zstd.decompress(path.read_bytes()) + b'{broken\n'
        path.write_bytes(zstd.compress(content))
        self.assertFalse(verify_source(src, self.options)['verified'])
        self.assertGreater(discover(self.options)['coverage']['malformed_lines'], 0)

    def test_zst_verification_retains_original_source_checks(self):
        path = self.write(self.sessions / 's.jsonl.zst', [meta(), human()])
        src = discover(self.options)['sessions'][0]['sources'][0]
        self.assertTrue(verify_source(src, self.options)['verified'])
        for field, value in [('session', 'wrong'), ('anchor', 'wrong'), ('time', 'wrong'), ('role', 'assistant')]:
            self.assertFalse(verify_source({**src, field: value}, self.options)['verified'])
        forged = {**src, 'excerpt': 'forged', 'excerpt_hash': digest('forged')}
        self.assertFalse(verify_source(forged, self.options)['verified'])
        self.assertFalse(verify_source(src, {'roots': {'claude': [], 'codex': [self.root / 'other']}})['verified'])
        self.assertTrue(verify_source(src, {'roots': {'claude': [], 'codex': []}, 'files': {'codex': [path]}})['verified'])
        path.unlink()
        self.assertFalse(verify_source(src, self.options)['verified'])


if __name__ == '__main__':
    unittest.main()
