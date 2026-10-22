"""Thread CLI: structured input and explicit, reviewable mutations."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import store
from .core import STATES, ThreadError, fields, get_item, statement

HELP = {
    'command': 'pstate thread', 'input': 'UTF-8 JSON --input-file PATH or --input-file - (stdin)',
    'commands': {
        'add': 'title, sources required; optional motivation/confirmed/questions/stopped_at/next_suggestion/paths/handoff; --distinct or --into ID --rev N',
        'list': 'default open/decided; --all or --state parked; always includes counts',
        'show': 'show ID, including sources, decision log and HANDOFF filesystem facts',
        'set': 'set ID --rev N; structured editable fields only, cannot set state',
        'choose': 'choose ID continue|do|later|drop; continue read-only; others require --rev N, said/source JSON',
        'done': 'done ID --rev N; evidence (nonempty string list); optional said/source, otherwise actor=assistant',
        'reopen': 'reopen ID --rev N; said/source; explicit reopening of parked/dropped/done',
        'discover': 'optional JSON roots/cursor/limit/max_chars/since/exclude_sessions/sweep_state; read-only, not auto-seen',
        'seen': 'JSON platform/session/version; explicit review acknowledgement'},
    'source': {'required': ['platform', 'session', 'anchor', 'time', 'role', 'excerpt', 'excerpt_hash'],
               'optional': ['path'], 'excerpt_hash': 'SHA-256 of UTF-8 excerpt'},
    'decisions': 'said is a verbatim user quotation contained in source.excerpt. Verification checks origin, never intent. Unverified origins return confirmed=false.',
    'options': ['--json', '--format json', '--dry-run (mutations)', '--ai-help'],
    'exits': {'0': 'success', '1': 'operational error/conflict', '2': 'invalid usage'},
    'safety': 'No task/file creation by do. New sources never reopen a closed item. No extra model calls.'}


def ai_help():
    commands = '\n'.join('- `' + name + '`: ' + usage for name, usage in HELP['commands'].items())
    return '''---
name: pstate-thread
description: Global resumable matters with explicit user choices and source evidence.
ai_help_version: 1
---

## Quick Reference
- `pstate thread list --json`
- `pstate thread add --input-file item.json --dry-run --json`
- `pstate thread choose ID continue --json`
Use `subprocess.run([python_path, script_path, "thread", "add", "--input-file", input_path, "--json"])` with list argv; never interpolate free text into shell command strings.

## When to Use
Recall unfinished conversations and let the user decide what to continue. Works outside Git.

## Command Reference
''' + commands + '''

Required source fields: platform/session/anchor/time/role/excerpt/excerpt_hash; optional path.
excerpt_hash is SHA-256 of UTF-8 excerpt. An item's sources identify exact original messages.
Complex fields use --input-file PATH or --input-file - (stdin); omitted input reads piped stdin except discover defaults to {}.
set requires --rev; choices other than continue require --rev and said/source. done requires --rev and evidence, with optional said/source.
next_suggestion is text and is stored with an AI marker. confirmed is a list of {said, source}; each quotation is verified against its original message when available.
paths, handoff and questions are lists of strings. title/motivation/stopped_at are text.
All business commands support --json or --format json. list defaults to open/decided; --state or --all changes visibility.
--ai-help and --help print documentation, not business JSON envelopes, including when --json is present.

## Side Effects & Safety
add/set/choose do|later|drop/done/reopen/seen update the global threads database under PSTATE_HOME.
All those mutations support --dry-run through the same transaction path; only a coordination lock directory/file may be created, never business state.
list/show/choose continue read state; show/continue inspect HANDOFF metadata without executing or changing its contents.
discover reads requested local history and optional session-sweep state; it does not call a model, write history or automatically mark seen.
No command here creates an execution task, edits a project or uses the network. do records a decision only.
Source verification checks the message origin, not semantic authorization; failed verification remains explicit confirmed=false.
Historical sources never reopen parked/dropped/done items. Reopening needs an explicit user quotation.

## Exit Codes
0 success; 1 operational failure/conflict; 2 invalid input or usage.

## Errors & Recovery
E_VALIDATION: missing --rev -> show ID and retry with its current rev; malformed JSON -> fix the input file.
E_NOT_FOUND: missing input/item -> inspect the supplied path or thread list --all.
E_IO: corrupt/unknown schema or missing primary with backup -> inspect/recover storage explicitly; never replace it with empty state.
If commit_status is replaced_unverified, the primary was replaced: inspect the item before retrying.
E_PERMISSION: obtain access to the selected path. E_INTERNAL: inspect the unexpected error; do not repeat blindly.
Failures put the error envelope on stderr and leave stdout empty when --json is requested.
'''


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ThreadError(message)


def common(parser):
    parser.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    parser.add_argument('--format', choices=('json', 'text'), default=argparse.SUPPRESS)


def input_option(parser, required=False):
    parser.add_argument('--input-file', required=required, help='UTF-8 JSON file or - for stdin')


def parser():
    root = Parser(prog='pstate thread', description='全局待续事项：回顾与用户选择',
                  epilog="LLMs/agents: run 'pstate thread --ai-help' for usage guidance.")
    common(root)
    sub = root.add_subparsers(dest='command', required=True, parser_class=Parser)
    for name in HELP['commands']:
        command = sub.add_parser(name)
        common(command)
        if name in ('show', 'set', 'choose', 'done', 'reopen'):
            command.add_argument('id')
        if name == 'choose':
            command.add_argument('choice', choices=('continue', 'do', 'later', 'drop'))
        if name in ('add', 'set', 'choose', 'done', 'reopen', 'seen'):
            command.add_argument('--dry-run', action='store_true')
        if name in ('add', 'set', 'choose', 'done', 'reopen'):
            command.add_argument('--rev', type=int, required=name in ('set', 'done', 'reopen'))
        if name in ('add', 'set', 'choose', 'done', 'reopen', 'seen', 'discover'):
            input_option(command)
        if name == 'add':
            group = command.add_mutually_exclusive_group()
            group.add_argument('--into')
            group.add_argument('--distinct', action='store_true')
        if name == 'list':
            group = command.add_mutually_exclusive_group()
            group.add_argument('--all', action='store_true')
            group.add_argument('--state', choices=STATES)
    return root


def read_input(name, optional=False):
    if name is None and optional:
        return {}
    if name in (None, '-') and sys.stdin.isatty():
        raise ThreadError('supply --input-file PATH or --input-file -')
    try:
        if name not in (None, '-'):
            import stat
            if not stat.S_ISREG(Path(name).stat().st_mode):
                raise ThreadError('input path must be a regular file')
        text = sys.stdin.read() if name in (None, '-') else Path(name).read_text(encoding='utf-8-sig')
        value = json.loads(text)
    except (ValueError, UnicodeError) as exc:
        raise ThreadError('invalid UTF-8 JSON input: ' + str(exc)) from exc
    if not isinstance(value, dict):
        raise ThreadError('input must be a JSON object')
    return value


def history():
    try:
        import pstate_history
        return pstate_history
    except ImportError as exc:
        raise ThreadError('history adapter unavailable: ' + str(exc), 'E_PLATFORM', 1) from exc


def verify(payload):
    _, src = statement(payload)
    try:
        result = history().verify_source(src)
    except (ThreadError, OSError, ValueError) as exc:
        return {'verified': False, 'reason': 'original message not verified: ' + str(exc)}
    if not isinstance(result, dict):
        return {'verified': False, 'reason': 'history verifier returned no result'}
    return {'verified': result.get('verified') is True, 'reason': str(result.get('reason', ''))}


def dispatch(args, directory):
    command = args.command
    if command in ('list', 'show'):
        state = store.read(directory)
        if command == 'show':
            return {'item': store.card(get_item(state, args.id))}
        wanted = STATES if args.all else ((args.state,) if args.state else ('open', 'decided'))
        return {'items': [store.card(item) for item in state['items'].values() if item['state'] in wanted],
                'counts': store.counts(state)}
    if command == 'choose' and args.choice == 'continue':
        if args.input_file or args.rev is not None or args.dry_run:
            raise ThreadError('continue is read-only and accepts only the thread ID')
        return {'item': store.card(get_item(store.read(directory), args.id)), 'action': 'continue', 'committed': False}
    payload = read_input(args.input_file, optional=command == 'discover')
    if command == 'discover':
        if 'seen' in payload:
            raise ThreadError('discover seen is supplied from the thread store')
        excluded = payload.get('exclude_sessions', [])
        if not isinstance(excluded, list) or any(not isinstance(s, str) for s in excluded):
            raise ThreadError('exclude_sessions must be a list of session IDs')
        current = [os.environ.get(name) for name in ('CODEX_THREAD_ID', 'CLAUDE_CODE_SESSION_ID')]
        payload['exclude_sessions'] = list(dict.fromkeys(excluded + [sid for sid in current if sid]))
        payload['seen'] = store.read(directory)['seen']
        return history().discover(payload)
    op = args.choice if command == 'choose' else command
    needs_statement = op in ('do', 'later', 'drop', 'reopen') or (op == 'done' and ('said' in payload or 'source' in payload))
    verification = verify(payload) if needs_statement else None
    if op in ('add', 'set') and 'confirmed' in payload:
        quotations = fields({'confirmed': payload['confirmed']})['confirmed']
        verification = {'confirmed_entries': [verify(entry) for entry in quotations]}
    return store.mutate(directory, op, payload, ident=getattr(args, 'id', None), rev=getattr(args, 'rev', None),
                        into=getattr(args, 'into', None), distinct=getattr(args, 'distinct', False),
                        verified=verification, dry_run=args.dry_run)


def envelope(data=None, error=None):
    return {'ok': error is None, 'data': data, 'error': error, 'meta': {}}


def machine_args(argv):
    before = argv[:argv.index('--')] if '--' in argv else argv
    return '--json' in before or any(before[i:i + 2] == ['--format', 'json'] for i in range(len(before))) or '--format=json' in before


def main(argv, home):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    before = argv[:argv.index('--')] if '--' in argv else argv
    json_mode = machine_args(argv)
    if '--ai-help' in before:
        print(ai_help())
        return 0
    try:
        args = parser().parse_args(argv)
        result = dispatch(args, Path(home) / 'threads')
        print(json.dumps(envelope(result) if json_mode else result, ensure_ascii=False, indent=2))
        return 0
    except ThreadError as exc:
        code, status, details, message = exc.code, exc.status, exc.details, str(exc)
    except FileNotFoundError as exc:
        code, status, details, message = 'E_NOT_FOUND', 1, {}, str(exc)
    except PermissionError as exc:
        code, status, details, message = 'E_PERMISSION', 1, {}, str(exc)
    except (OSError, TimeoutError) as exc:
        code, status, details, message = 'E_IO', 1, {}, str(exc)
    except ValueError as exc:
        code, status, details, message = 'E_VALIDATION', 2, {}, str(exc)
    except Exception as exc:
        code, status, details, message = 'E_INTERNAL', 1, {}, 'unexpected failure: ' + str(exc)
    error = {'code': code, 'message': message, 'details': details, 'retryable': False,
             'suggestion': 'Read thread again and inspect the error before retrying.'}
    print(json.dumps(envelope(error=error), ensure_ascii=False) if json_mode else message, file=sys.stderr)
    return status
