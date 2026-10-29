"""Pick-up: the session that resumes a card becomes one of its sources. Never fails the command."""
import os

from pstate_history.common import parse_ts

from . import store
from .core import ThreadError
from .followups import user_messages


def current_session(platforms, environ=None):
    env = os.environ if environ is None else environ
    found = [(name, env[adapter['session_env']]) for name, adapter in platforms.items() if env.get(adapter['session_env'])]
    if len(found) != 1:
        return None, 'ambiguous_session_env' if found else 'no_session_env'
    return found[0], None


def latest_user(history, platform, session):
    """The source only says who works on the card, never confirms a decision, so Codex legacy messages count."""
    paths = history.session_logs(platform, session)
    if not paths:
        return None, 'log_not_found'
    loaded = history.load_sessions({'files': {platform: [str(p) for p in paths]}, 'roots': {}})
    users = sorted(user_messages(loaded, session), key=lambda pair: parse_ts(pair[1]['time']) or 0)
    if not users:
        return None, 'no_user_message'
    return history.source_for(*users[-1]), None


def pick_up(directory, item, history, dry_run):
    """Return (session_linked, updated item or None, committed); the checks repeat under the write lock."""
    try:
        adapters = history()
        who, reason = current_session(adapters.PLATFORMS)
        if who is None:
            return {'linked': False, 'reason': reason}, None, False
        if item['state'] not in ('open', 'decided'):
            return {'linked': False, 'reason': 'not_active'}, None, False
        if any((s['platform'], s['session']) == who for s in item['sources']):
            return {'linked': False, 'reason': 'already_source'}, None, False
        src, reason = latest_user(adapters, *who)
        if src is None:
            return {'linked': False, 'reason': reason}, None, False
        result = store.mutate(directory, 'pickup', {'source': src}, ident=item['id'], dry_run=dry_run)
    except (ThreadError, OSError, ValueError) as exc:
        status = exc.details.get('commit_status') if isinstance(exc, ThreadError) else None
        unknown = status == 'replaced_unverified'
        linked = {'linked': None if unknown else False, 'reason': 'failed: ' + str(exc)}
        if status:
            linked['commit_status'] = status
        return linked, None, None if unknown else False
    return {'linked': result['linked'], 'reason': result['reason']}, result['item'], result['committed']

