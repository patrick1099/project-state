"""Source sessions that kept going after a thread's content last changed; read-only."""
import json
from pathlib import Path

from pstate_history.common import clip, parse_ts, redact

from .core import last_touched

ACTIVE = ('open', 'decided')
SHOWN_MESSAGES = 5
SHOWN_CHARS = 400


def user_messages(loaded, session):
    """User messages of one session, Codex legacy format included; the parsers already drop tool and injected rows."""
    return [(found, m) for found in loaded['sessions'] if found['session'] == session
            for m in found['messages'] if m['role'] == 'user']


def cached(read):
    """One list/show call reads each log set once, however many cards share it."""
    memo = {}

    def once(*args):
        key = json.dumps(args, sort_keys=True)
        if key not in memo:
            memo[key] = read(*args)
        return memo[key]
    return once


def source_logs(item):
    """Per source session: every recorded log path and the time of its latest source on the card."""
    logs = {}
    for src in item['sources']:
        entry = logs.setdefault((src['platform'], src['session']), {'paths': [], 'latest': 0})
        if src.get('path') and src['path'] not in entry['paths']:
            entry['paths'].append(src['path'])
        entry['latest'] = max(entry['latest'], parse_ts(src['time']) or 0)
    return logs


def readable_paths(paths, platform, session, locate):
    """Recorded logs (or their .zst archive); when all are gone, look the session up by ID (Codex archives move it)."""
    found = []
    for path in paths:
        found += [c for c in (Path(path), Path(path + '.zst')) if c.is_file()][:1]
    return found or [Path(p) for p in locate(platform, session)]


def followups(item, load, locate, with_text=False):
    """Per source session, user messages newer than both the last content change and that session's latest source."""
    if item['state'] not in ACTIVE:
        return []
    touched = parse_ts(last_touched(item)) or 0
    result = []
    for (platform, session), logs in source_logs(item).items():
        since = max(touched, logs['latest'])
        entry = {'platform': platform, 'session': session, 'path': logs['paths'][0] if logs['paths'] else None,
                 'readable': False, 'user_messages': 0, 'last_time': None}
        if with_text:
            entry['messages'] = []
        try:
            found = readable_paths(logs['paths'], platform, session, locate)
            if found:
                entry['path'] = str(found[0])
            if found and all(p.stat().st_mtime <= since for p in found):
                entry['readable'] = True
            elif found:
                loaded = load({'files': {platform: sorted(str(p) for p in found)}, 'roots': {}})
                later = sorted((m for _, m in user_messages(loaded, session) if (parse_ts(m['time']) or 0) > since),
                               key=lambda m: parse_ts(m['time']) or 0)
                coverage = loaded['coverage']
                entry.update(readable=coverage['files'] > 0 and not coverage['unreadable'], user_messages=len(later),
                             last_time=later[-1]['time'] if later else None)
                if with_text:
                    entry['messages'] = [{'time': m['time'], 'text': clip(redact(m['text']), SHOWN_CHARS)}
                                         for m in later[-SHOWN_MESSAGES:]]
        except OSError:
            entry['readable'] = False
        result.append(entry)
    return result


def annotate(card, load, locate, with_text=False):
    card['last_touched'] = last_touched(card)
    card['followups'] = followups(card, load, locate, with_text)
    card['possibly_stale'] = any(f['user_messages'] for f in card['followups'])
    return card

