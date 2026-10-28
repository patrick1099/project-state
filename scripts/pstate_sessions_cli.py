"""CLI boundary for explicit task/session links."""
import json
import sys

import pstate_lock as L
import pstate_sessions as S


def main(argv, home):
    from pstate_threads.cli import Parser, envelope, machine_args
    before = argv[:argv.index('--')] if '--' in argv else argv
    if '--ai-help' in before:
        print("""---
name: pstate-link
description: Explicit task/session associations, independent of hub and task claims.
ai_help_version: 1
---

## Quick Reference
pstate link TASK --platform codex --session ID --json
pstate link TASK --dry-run --format json
Use subprocess.run([python_path, script_path, "link", task, "--platform", "codex", "--session", session_id]) with list argv.

## When to Use
Register current, historical or delegated sessions explicitly against an existing .../task/Tnn-name directory.

## Command Reference
link TASK [--platform claude|codex --session ID [--agent ID]] [--reason TAG]
--platform and --session must be supplied together and override the environment.
Otherwise exactly one of CLAUDE_CODE_SESSION_ID / CODEX_THREAD_ID must be present.
--reason is repeatable; --reason - reads UTF-8 stdin for free text.
--json and --format json return the ok/data/error/meta envelope. --dry-run previews the same write path.

## Side Effects & Safety
Writes only the managed region of TASK/会话.md under a short lock in PSTATE_HOME/session-links.
Preserves manual content outside that region. Malformed regions fail without overwriting.
Identity is host + platform + native session ID + optional agentId. Tags merge; first association time is preserved.
No claim, live process, transcript or hub installation required. release never deletes links.
The command does not infer task links or session starting directories from cwd.

## Exit Codes
0 success; 1 storage or operational failure; 2 invalid usage or ambiguous/missing session identity.

## Errors & Recovery
E_VALIDATION: supply explicit --platform and --session, or correct arguments/task directory.
E_IO/E_PERMISSION: inspect the file and repair malformed managed regions manually; never rebuild over existing content.
""")
        return 0
    ap = Parser(prog='pstate link', epilog="LLMs/agents: run 'pstate link --ai-help' for usage guidance.")
    ap.add_argument('task')
    ap.add_argument('--platform', choices=('claude', 'codex'))
    ap.add_argument('--session')
    ap.add_argument('--agent')
    ap.add_argument('--reason', action='append', default=[])
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--format', choices=('json',))
    ap.add_argument('--dry-run', action='store_true')
    try:
        args = ap.parse_args(argv)
        from pstate_threads.core import ThreadError
        try:
            who = S.current_identity(args.platform, args.session, args.agent)
            reasons = []
            for reason in args.reason:
                if reason == '-':
                    if sys.stdin.isatty():
                        raise S.LinkError('--reason - 需要 UTF-8 stdin')
                    reason = sys.stdin.read()
                reasons.append(S.text(reason, '--reason'))
            L.task_dir_key(args.task)
        except ValueError as exc:
            raise ThreadError(str(exc)) from exc
        result = S.link_sessions(home, args.task, [(who, ['link', *reasons])], dry_run=args.dry_run)
        print(json.dumps(envelope(result), ensure_ascii=False) if machine_args(argv) else ('预演关联：' if args.dry_run else '已关联会话：') + result['path'])
        return 0
    except Exception as exc:
        from pstate_threads.core import ThreadError
        if isinstance(exc, ThreadError):
            status, code = exc.status, exc.code
        else:
            status = 1
            code = ('E_PERMISSION' if isinstance(exc, PermissionError) else
                    'E_IO' if isinstance(exc, (OSError, ValueError, TimeoutError)) else 'E_INTERNAL')
        error = {'code': code, 'message': str(exc), 'details': {}, 'retryable': False,
                 'suggestion': 'Inspect the error; malformed 会话.md must be repaired manually.'}
        print(json.dumps(envelope(error=error), ensure_ascii=False) if machine_args(argv) else str(exc), file=sys.stderr)
        return status
