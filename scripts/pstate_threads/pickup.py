"""Pick-up: the session that resumes a card becomes one of its sources. Never fails the command."""
import os

from . import store
from .core import ThreadError


def current_session(platforms, environ=None):
    env = os.environ if environ is None else environ
    found = [(name, env[adapter['session_env']]) for name, adapter in platforms.items() if env.get(adapter['session_env'])]
    if len(found) != 1:
        return None, 'ambiguous_session_env' if found else 'no_session_env'
    return found[0], None


def latest_human(history, platform, session):
    path = history.session_log(platform, session)
    if path is None:
        return None, 'log_not_found'
    loaded = history.load_sessions({'files': {platform: [str(path)]}, 'roots': {}})
    humans = [(found, m) for found in loaded['sessions'] if found['session'] == session
              for m in found['messages'] if m['role'] == 'user' and m['native_human']]
    if not humans:
        return None, 'no_native_user_message'
    return history.source_for(*humans[-1]), None


def pick_up(directory, item, history, dry_run, preview=None):
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
        src, reason = latest_human(adapters, *who)
        if src is None:
            return {'linked': False, 'reason': reason}, None, False
        result = store.mutate(directory, 'pickup', {'source': src}, ident=item['id'],
                              dry_run=dry_run, preview_state=preview)
    except (ThreadError, OSError, ValueError) as exc:
        return {'linked': False, 'reason': 'failed: ' + str(exc)}, None, False
    return {'linked': result['linked'], 'reason': result['reason']}, result['item'], result['committed']
