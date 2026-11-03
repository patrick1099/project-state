import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pstate_disk as D

class HeaderTests(unittest.TestCase):
    def test_nonwindows_does_not_claim_support(self):
        self.assertEqual(D.snapshot(['anything'],platform_name='posix'),{'supported':False,'files':{}})
    def test_only_previously_encrypted_existing_files_are_reported(self):
        paths=[str(Path(tempfile.gettempdir())/x) for x in ('a','b','c')]
        before={paths[0]:{'exists':True,'encrypted':True},paths[1]:{'exists':True,'encrypted':False},paths[2]:{'exists':False,'encrypted':False}}
        snap=D.snapshot(paths,platform_name='nt',observer=lambda p:before)
        after={p:{'exists':True,'encrypted':False} for p in paths}
        self.assertEqual(D.verify(snap,observer=lambda p:after),[paths[0]])
        after[paths[0]]={'exists':False,'encrypted':False}
        self.assertEqual(D.verify(snap,observer=lambda p:after),[])
    def test_directory_and_glob_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):D.snapshot([td],platform_name='nt')
            with self.assertRaises(ValueError):D.snapshot([str(Path(td)/'*.py')],platform_name='nt')
    def test_probe_failure_is_not_silent(self):
        def fail(paths):raise OSError('unavailable')
        with self.assertRaises(OSError):D.snapshot(['a.py'],platform_name='nt',observer=fail)
if __name__=='__main__':unittest.main()
