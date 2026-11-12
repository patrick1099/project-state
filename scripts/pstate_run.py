"""Explicit write-set command execution with the same reservation transaction as hooks."""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
import pstate_hooks as H
import pstate_disk as D
import pstate_lock as L

HELP = """---
name: pstate-run
description: Reserve explicit file paths before executing a local command.
ai_help_version: 1
---
## Quick Reference
- `pstate run --write app.py --dry-run --json -- python build.py`
- `pstate run --write app.py --command-file argv.json --json`
## When to Use
Use for shell or Python writes whose complete write set cannot be inferred by hooks.
## Command Reference
--write PATH is repeatable and required. Declare every modified path, including moves/deletions.
--command-file FILE accepts a UTF-8 JSON string array (or - for piped stdin); alternatively use -- followed by argv.
--cwd on pstate selects the child working directory. --json and --format json are equivalent.
--dry-run validates and evaluates the same reservation path without reserving or executing the child.
## Side Effects & Safety
The real command reserves all declared personal-repository paths before starting; lock files may be created during dry-run.
This is a cooperative write-set declaration, not an OS sandbox: it does not prove a program obeys its declared paths.
The child runs without an extra shell and can have all the side effects of the supplied command.
On Windows, list concrete files (no directories/globs). PowerShell observes each file header before/after execution.
An encrypted-to-plaintext change returns E_VERIFICATION_FAILED and keeps the reservation; detection cannot undo the write.
Failed commands roll back only new, never-written reservations. Existing successful claims remain.
## Exit Codes
0 success; 1 denied or execution/storage failure; 2 invalid arguments.
## Errors & Recovery
E_PERMISSION: stop on another session's reservation; ask the user to wait or explicitly take over.
E_VALIDATION: correct paths or command argv. E_EXTERNAL_TOOL: inspect the child exit code/output before retrying.
E_IO: preserve the state files and inspect diagnostics. --help/--ai-help print documentation rather than envelopes.
"""

class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError(message)


def result(ok, data=None, code=None, message='', details=None):
    return {'ok':ok,'data':data if ok else None,'error':None if ok else {
        'code':code,'message':message,'details':details or {},'retryable':False,'suggestion':'检查当前状态和错误后再重试。'},'meta':{}}


def fingerprints(paths):
    result={}
    for path in paths:
        try:
            st=Path(path).stat();result[path]=(st.st_size,st.st_mtime_ns)
        except FileNotFoundError:result[path]=None
    return result


def main(argv,cwd,home,state_dir_for,command):
    before=argv[:argv.index('--')] if '--' in argv else argv
    if '--ai-help' in before:print(HELP);return 0
    json_mode='--json' in before or '--format' in before
    try:
        parser=Parser(prog='pstate run',epilog="LLMs/agents: run 'pstate run --ai-help' for usage guidance.")
        parser.add_argument('--write',action='append',required=True)
        parser.add_argument('--command-file')
        parser.add_argument('--json',action='store_true')
        parser.add_argument('--format',choices=('json',))
        parser.add_argument('--dry-run',action='store_true')
        parser.add_argument('command',nargs=argparse.REMAINDER)
        args=parser.parse_args(argv)
        child=args.command[1:] if args.command[:1]==['--'] else args.command
        if args.command_file:
            if child:raise ValueError('--command-file 与 -- 后的命令不能同时提供')
            if args.command_file=='-':
                if sys.stdin.isatty():raise ValueError('--command-file - 需要管道输入')
                child=json.load(sys.stdin)
            else:child=json.loads(Path(args.command_file).read_text(encoding='utf-8'))
        if not isinstance(child,list) or not child or any(not isinstance(x,str) or not x for x in child):
            raise ValueError('command 必须是非空字符串数组')
        host,sid=L.host_session()
        if not host:raise RuntimeError('需要唯一的 Claude/Codex/DSH 会话环境')
        data={'session_id':sid,'turn_id':'explicit-run','tool_use_id':uuid.uuid4().hex,'cwd':cwd,
              'tool_name':'MultiEdit','tool_input':{'edits':[{'file_path':p} for p in args.write]}}
        output=io.StringIO()
        with contextlib.redirect_stdout(output):allowed=H.begin(data,home,state_dir_for,command,host,dry_run=args.dry_run)
        if not allowed:
            raise PermissionError(json.loads(output.getvalue())['hookSpecificOutput']['permissionDecisionReason'])
        targets=[os.path.realpath(os.path.join(cwd,p)) for p in args.write]
        try:
            before=D.snapshot(targets)
            initial=fingerprints(targets)
        except BaseException:
            H.finish(data,home,True,host)
            raise
        payload={'dry_run':args.dry_run,'paths':targets,'command':child,
                 'encryption_check':{'supported':before['supported'],'decrypted':[]}}
        if not args.dry_run:
            try:
                proc=subprocess.run(child,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace')
            except KeyboardInterrupt:
                H.finish(data,home,False,host)
                raise
            except OSError as exc:
                H.finish(data,home,True,host)
                envelope=result(False,code='E_EXTERNAL_TOOL',message=str(exc),details={'command':child})
                print(json.dumps(envelope,ensure_ascii=False) if json_mode else str(exc),file=sys.stderr)
                return 1
            payload.update(exit_code=proc.returncode,stdout=proc.stdout,stderr=proc.stderr)
            try:
                decrypted=D.verify(before)
                changed=fingerprints(targets)!=initial
            except Exception:
                H.finish(data,home,False,host)
                raise
            H.finish(data,home,proc.returncode!=0 and not changed and not decrypted,host)
            payload['encryption_check']['decrypted']=decrypted
            if decrypted:
                envelope=result(False,code='E_VERIFICATION_FAILED',message='声明文件发生加密转明文；写入已发生，未自动修复，保留占用。',details=payload)
                print(json.dumps(envelope,ensure_ascii=False) if json_mode else envelope['error']['message']+'\n'+'\n'.join(decrypted),file=sys.stderr)
                return 1
            if proc.returncode:
                envelope=result(False,code='E_EXTERNAL_TOOL',message='命令执行失败',details=payload)
                print(json.dumps(envelope,ensure_ascii=False) if json_mode else proc.stderr or '命令执行失败',file=sys.stderr);return 1
            if not json_mode and proc.stderr:sys.stderr.write(proc.stderr)
        print(json.dumps(result(True,payload),ensure_ascii=False) if json_mode else ('预演通过' if args.dry_run else payload.get('stdout','')),end='\n')
        return 0
    except KeyboardInterrupt:
        print(json.dumps(result(False,code='E_INTERRUPTED',message='执行被中断；已发生的写入占用保留。'),ensure_ascii=False) if json_mode else '执行被中断',file=sys.stderr)
        return 1
    except Exception as exc:
        if isinstance(exc,(ValueError,TypeError)):code,rc='E_VALIDATION',2
        elif isinstance(exc,PermissionError):code,rc='E_PERMISSION',1
        elif isinstance(exc,FileNotFoundError):code,rc='E_NOT_FOUND',1
        elif isinstance(exc,OSError):code,rc='E_IO',1
        elif isinstance(exc,RuntimeError):code,rc='E_PLATFORM',1
        else:code,rc='E_INTERNAL',1
        print(json.dumps(result(False,code=code,message=str(exc)),ensure_ascii=False) if json_mode else str(exc),file=sys.stderr)
        return rc
