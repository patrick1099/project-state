"""Source sessions that kept going after a thread was last updated; read-only."""
from pathlib import Path

from pstate_history.common import parse_ts

ACTIVE = ('open', 'decided')


def last_touched(item):
    return item['log'][-1]['at'] if item['log'] else ''


def source_logs(item):
    logs = {}
    for src in item['sources']:
        key = (src['platform'], src['session'])
        if not logs.get(key):
            logs[key] = src.get('path')
    return logs


def readable_path(path):
    if not path:
        return None
    for candidate in (Path(path), Path(path + '.zst')):
        if candidate.is_file():
            return candidate
    return None


def followups(item, load_sessions):
    """Count native user messages in each source session newer than the item's last log event."""
    if item['state'] not in ACTIVE:
        return []
    since = parse_ts(last_touched(item)) or 0
    result = []
    for (platform, session), path in source_logs(item).items():
        entry = {'platform': platform, 'session': session, 'path': path,
                 'readable': False, 'user_messages': 0, 'last_time': None}
        found = readable_path(path)
        if found and found.stat().st_mtime <= since:
            entry['readable'] = True
        elif found:
            loaded = load_sessions({'files': {platform: [str(found)]}, 'roots': {}})
            later = [m for s in loaded['sessions'] if s['session'] == session
                     for m in s['messages'] if m['role'] == 'user' and (parse_ts(m['time']) or 0) > since]
            coverage = loaded['coverage']
            entry.update(readable=coverage['files'] > 0 and not coverage['unreadable'], user_messages=len(later),
                         last_time=max(later, key=lambda m: parse_ts(m['time']) or 0)['time'] if later else None)
        result.append(entry)
    return result


def annotate(card, load_sessions):
    card['last_touched'] = last_touched(card)
    card['followups'] = followups(card, load_sessions)
    card['possibly_stale'] = any(f['user_messages'] for f in card['followups'])
    return card
