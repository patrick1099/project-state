import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import pstate
import pstate_hooks as H
import pstate_lock as L
import pstate_tools as T
import pstate_run as R
from test_pstate import FakeProc


class InputIntentTests(unittest.TestCase):
    def shell(self,command):return T.shell_intent(command,os.getcwd())

    def test_plain_reads_are_not_reserved(self):
        for command in ('git status --short','rg pattern file','Get-Content file','echo hello',
                        'git diff --no-ext-diff --no-textconv --stat'):
            with self.subTest(command=command):self.assertTrue(self.shell(command).readonly)

    def test_whole_command_must_be_read_only(self):
        for command in ('cat a\nrm b','Get-Content a\nSet-Content b x','rg x a; python -c "exec(code)"',
                        'cat a > out','git -c alias.x=!bad x','Write-Output (Set-Content owned x)',
                        'Get-ChildItem (New-Item x)','git grep -Oevil x','git show HEAD',
                        'git diff --no-ext-diff --no-textconv --output=out',
                        'echo $(touch bad)','echo `touch bad`'):
            with self.subTest(command=command):self.assertFalse(self.shell(command).readonly)

    def test_quoted_patterns_are_not_commands(self):
        self.assertTrue(self.shell("rg '(a|b)' file").readonly)

    def test_direct_python_targets_are_extracted(self):
        intent=self.shell('python -c "from pathlib import Path; p=Path(\'a.txt\'); p.write_text(\'x\')"')
        self.assertIn(os.path.realpath('a.txt'),intent.paths)
        self.assertTrue(intent.opaque)

    def test_cd_and_absolute_targets_change_scope(self):
        target=os.path.join(os.path.abspath(os.sep),'other','b.txt')
        intent=self.shell('cd child; Set-Content a.txt x; Set-Content "'+target+'" x')
        self.assertIn(os.path.realpath('child/a.txt'),intent.paths)
        self.assertIn(target,intent.paths)

    def test_patch_moves_reserve_source_and_destination(self):
        intent=T.analyze({'tool_name':'apply_patch','cwd':os.getcwd(),'tool_input':{'command':
            '*** Begin Patch\n*** Update File: a\n*** Move to: b\n@@\n-x\n+y\n*** End Patch'}})
        self.assertEqual(set(intent.paths),{os.path.realpath('a'),os.path.realpath('b')})

    def test_explicit_runner_requires_declared_set(self):
        intent=self.shell('python pstate.py run --write a -- python other.py')
        self.assertFalse(intent.opaque)
        self.assertEqual(intent.paths,(os.path.realpath('a'),))
        self.assertTrue(self.shell('python pstate.py run --write a --dry-run -- python other.py').readonly)


class HookIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='pstate-hooks-tests-')
        cls.base=Path(cls.tmp.name);cls.repo=cls.base/'repo';(cls.repo/'docs').mkdir(parents=True)
        (cls.repo/'docs/BLUEPRINT.md').write_text('# fixture\n',encoding='utf-8')
        (cls.repo/'a.txt').write_text('0\n',encoding='utf-8')
        (cls.repo/'b.txt').write_text('0\n',encoding='utf-8')
        cls.git('init','-q','-b','main');cls.git('add','.');cls.git('commit','-qm','fixture')
        cls.wt=cls.base/'wt';cls.git('worktree','add','-q',str(cls.wt),'-b','second')
        cls.plain=cls.base/'company';cls.plain.mkdir()
        subprocess.run(['git','init','-q'],cwd=cls.plain,check=True,capture_output=True)

    @classmethod
    def git(cls,*args):
        p=subprocess.run(['git','-c','core.hooksPath='+str(cls.base/'no-hooks'),'-c','user.name=Fixture',
                          '-c','user.email=fixture@example.invalid',*args],cwd=cls.repo,capture_output=True,text=True)
        if p.returncode:raise RuntimeError(p.stderr)
        return p.stdout

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def setUp(self):
        self.state=self.base/('state-'+self._testMethodName)
        self.proc=FakeProc();self.proc.chain=[(100,1,'codex.exe'),(9,0,'Code.exe')]
        self.patch=mock.patch.object(L,'process_backend',lambda:self.proc);self.patch.start()
        self.env=mock.patch.dict(os.environ,{'PSTATE_HOME':str(self.state),'CODEX_THREAD_ID':'A','CLAUDE_CODE_SESSION_ID':''});self.env.start()
        for p in (self.repo/'a.txt',self.repo/'b.txt'):p.write_text('0\n',encoding='utf-8')

    def tearDown(self):self.env.stop();self.patch.stop()

    def payload(self,sid,paths=('a.txt',),tool='apply_patch',cwd=None,call='call',command=None):
        text=command or '*** Begin Patch\n'+''.join('*** Update File: '+p+'\n@@\n-0\n+1\n' for p in paths)+'*** End Patch'
        return {'session_id':sid,'turn_id':'turn','tool_use_id':call,'cwd':str(cwd or self.repo),
                'tool_name':tool,'tool_input':{'command':text}}

    def begin(self,data,host='codex',dry_run=False):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):allowed=H.begin(data,self.state,pstate.state_dir_for,pstate.self_cmd(),host,dry_run)
        return allowed,json.loads(output.getvalue()) if output.getvalue() else None

    def state_data(self):
        common,_,_=pstate.repo_context(self.repo)
        return L.load_state(pstate.state_dir_for(common))

    def test_codex_relative_shell_write_is_rejected_without_guessing_workdir(self):
        allowed,out=self.begin(self.payload('A',tool='Bash',command='Set-Content a.txt x'))
        self.assertFalse(allowed)
        self.assertIn('workdir',out['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(self.state_data()['claims'],{})
        command='Set-Content "'+str(self.repo/'a.txt')+'" x'
        self.assertTrue(self.begin(self.payload('A',tool='Bash',command=command))[0])

    def test_codex_explicit_absolute_cd_anchors_relative_write(self):
        command='cd "'+str(self.repo)+'"; Set-Content a.txt x'
        self.assertTrue(self.begin(self.payload('A',tool='Bash',command=command))[0])
        self.assertIn('a.txt',self.state_data()['claims'])

    def test_probe_failure_rolls_back_only_new_reservations(self):
        output,error=io.StringIO(),io.StringIO()
        with mock.patch.object(R.D,'snapshot',side_effect=OSError('probe failed')), contextlib.redirect_stdout(output),contextlib.redirect_stderr(error):
            rc=R.main(['--write','a.txt','--json','--',sys.executable,'-c','print(1)'],str(self.repo),self.state,pstate.state_dir_for,pstate.self_cmd())
        self.assertEqual(rc,1);self.assertEqual(self.state_data()['claims'],{})
        self.assertEqual(json.loads(error.getvalue())['error']['code'],'E_IO')

    def test_decryption_report_keeps_successful_write_reservation(self):
        output,error=io.StringIO(),io.StringIO();path=str(self.repo/'a.txt')
        fake={'supported':True,'files':{path:{'exists':True,'encrypted':True}}}
        with mock.patch.object(R.D,'snapshot',return_value=fake),mock.patch.object(R.D,'verify',return_value=[path]),contextlib.redirect_stdout(output),contextlib.redirect_stderr(error):
            rc=R.main(['--write',path,'--json','--',sys.executable,'-c','print(1)'],str(self.repo),self.state,pstate.state_dir_for,pstate.self_cmd())
        self.assertEqual(rc,1);self.assertTrue(self.state_data()['claims']['a.txt']['written'])
        self.assertEqual(json.loads(error.getvalue())['error']['code'],'E_VERIFICATION_FAILED')

    def test_end_receipt_releases_same_pid_holder_and_persists_on_next_write(self):
        self.begin(self.payload('A'))
        L.E.mark(self.state,'codex:A')
        self.assertEqual(self.state_data()['claims'],{})
        self.assertTrue(self.begin(self.payload('B'))[0])
        path=next(self.state.glob('*/locks.json'))
        raw=json.loads(path.read_text(encoding='utf-8'))
        self.assertNotIn('codex:A',raw['sessions'])
        self.assertEqual(raw['claims']['a.txt']['holder'],'codex:B')
        self.assertTrue(L.E.is_ended(self.state,'codex:A'))

    def test_end_receipt_filters_task_claims_and_resume_cleans_before_reuse(self):
        self.begin(self.payload('A'))
        L.save_claims(self.state/'claims',{'task':{'sid':'codex:A'},'other':{'sid':'codex:B'}})
        L.E.mark(self.state,'codex:A')
        self.assertEqual(set(L.load_claims(self.state/'claims')),{'other'})
        self.assertFalse(self.begin(self.payload('A'))[0])
        H.end({'session_id':'A'},self.state,'codex')
        self.assertFalse(L.E.is_ended(self.state,'codex:A'))
        self.assertEqual(set(L.load_claims(self.state/'claims',include_ended=True)),{'other'})
        self.assertTrue(self.begin(self.payload('A'))[0])
        self.assertFalse(self.begin(self.payload('B'))[0])

    def test_bad_state_keeps_end_receipt_for_retry(self):
        self.begin(self.payload('A'));L.E.mark(self.state,'codex:A')
        path=next(self.state.glob('*/locks.json'));path.write_text('bad',encoding='utf-8')
        with self.assertRaises(RuntimeError):H.end({'session_id':'A'},self.state,'codex')
        self.assertTrue(L.E.is_ended(self.state,'codex:A'))
        self.assertEqual(path.read_text(encoding='utf-8'),'bad')

    def test_same_pid_different_thread_is_denied(self):
        self.assertTrue(self.begin(self.payload('A'))[0])
        allowed,out=self.begin(self.payload('B',cwd=self.wt))
        self.assertFalse(allowed)
        self.assertEqual(out['hookSpecificOutput']['permissionDecision'],'deny')
        self.assertEqual(self.state_data()['claims']['a.txt']['holder'],'codex:A')

    def test_no_partial_reservations_on_multifile_conflict(self):
        self.begin(self.payload('A'))
        self.assertFalse(self.begin(self.payload('B',('b.txt','a.txt')))[0])
        self.assertNotIn('b.txt',self.state_data()['claims'])

    def test_failed_batch_rolls_back_all_new_reservations(self):
        data=self.payload('A',('a.txt','b.txt'));self.begin(data)
        H.finish(data,self.state,True,'codex')
        self.assertEqual(self.state_data()['claims'],{})

    def test_failed_retry_preserves_successful_claim(self):
        first=self.payload('A');self.begin(first);(self.repo/'a.txt').write_text('changed',encoding='utf-8')
        H.finish(first,self.state,False,'codex')
        retry=self.payload('A',('a.txt','b.txt'),call='retry');self.begin(retry);H.finish(retry,self.state,True,'codex')
        self.assertEqual(set(self.state_data()['claims']),{'a.txt'})
        self.assertTrue(self.state_data()['claims']['a.txt']['written'])

    def test_session_end_does_not_release_other_thread(self):
        self.begin(self.payload('A'));self.begin(self.payload('B',('b.txt',)))
        H.end({'session_id':'A'},self.state,'codex')
        self.assertEqual(set(self.state_data()['claims']),{'b.txt'})

    def test_corrupt_state_is_preserved_and_blocks_writes(self):
        self.begin(self.payload('A'));common,_,_=pstate.repo_context(self.repo);p=pstate.state_dir_for(common)/L.LOCK_FILE
        p.write_text('BROKEN',encoding='utf-8')
        with self.assertRaises(L.StateError):self.begin(self.payload('B'))
        self.assertEqual(p.read_text(encoding='utf-8'),'BROKEN')
        self.assertTrue(self.begin(self.payload('B',tool='Bash',command='git status --short'))[0])

    def test_shell_cannot_overwrite_locked_file(self):
        self.begin(self.payload('A'))
        self.assertFalse(self.begin(self.payload('B',tool='Bash',command='Set-Content a.txt changed'))[0])
        self.assertFalse(self.begin(self.payload('B',tool='Bash',command='python -c "exec(code)"'))[0])
        self.assertTrue(self.begin(self.payload('B',tool='Bash',command='git status --short'))[0])

    def test_company_and_unrelated_repository_are_not_blocked(self):
        self.begin(self.payload('A'))
        self.assertTrue(self.begin(self.payload('B',tool='Bash',cwd=self.plain,command='python -c "exec(code)"'))[0])
        self.assertTrue(self.begin(self.payload('B',cwd=self.plain))[0])

    def test_absolute_shell_target_from_company_directory_is_checked(self):
        self.begin(self.payload('A'))
        cmd='Set-Content "'+str(self.repo/'a.txt')+'" changed'
        self.assertFalse(self.begin(self.payload('B',tool='Bash',cwd=self.plain,command=cmd))[0])

    def test_declared_runner_reserves_before_execution(self):
        self.begin(self.payload('A'))
        data=self.payload('B',tool='Bash',command='python pstate.py run --write a.txt -- python code.py')
        self.assertFalse(self.begin(data)[0])

    def test_dry_run_never_registers_paths(self):
        self.assertTrue(self.begin(self.payload('A'),dry_run=True)[0])
        self.assertEqual(self.state_data()['claims'],{})
        self.assertFalse(list((self.state/'inflight').glob('*')))

    def test_failed_receipt_is_detected(self):
        for response in ('Exit code: 1\nError',{'exit_code':1},{'isError':True},
                         {'metadata':{'exit_code':1}},'Process exited with code 2'):
            self.assertTrue(H.response_failed({'tool_response':response}))

    def test_explicit_runner_json_and_nonzero_child(self):
        output,error=io.StringIO(),io.StringIO()
        args=['--write','a.txt','--json','--',sys.executable,'-c','raise SystemExit(7)']
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(error):
            rc=R.main(args,str(self.repo),self.state,pstate.state_dir_for,pstate.self_cmd())
        self.assertEqual(rc,1);self.assertEqual(output.getvalue(),'')
        self.assertEqual(json.loads(error.getvalue())['error']['details']['exit_code'],7)
        self.assertEqual(self.state_data()['claims'],{})

    def test_explicit_runner_dry_run_json(self):
        output=io.StringIO()
        args=['--write','a.txt','--dry-run','--format','json','--',sys.executable,'-c','raise SystemExit(7)']
        with contextlib.redirect_stdout(output):rc=R.main(args,str(self.repo),self.state,pstate.state_dir_for,pstate.self_cmd())
        self.assertEqual(rc,0);self.assertTrue(json.loads(output.getvalue())['data']['dry_run'])

    def test_invalid_runner_usage_is_json(self):
        output,error=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(error):
            rc=R.main(['--json'],str(self.repo),self.state,pstate.state_dir_for,pstate.self_cmd())
        self.assertEqual(rc,2);self.assertEqual(output.getvalue(),'')
        self.assertEqual(json.loads(error.getvalue())['error']['code'],'E_VALIDATION')

if __name__=='__main__':unittest.main()
