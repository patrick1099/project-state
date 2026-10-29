"""Pure thread validation and state transitions."""
import copy
import hashlib
import json

STATES = ('open', 'decided', 'parked', 'dropped', 'done', 'transferred')
CLOSED = ('dropped', 'done', 'transferred')
DECISIONS = ('do', 'later', 'drop', 'done', 'reopen', 'transfer')
FIELDS = {'title', 'motivation', 'confirmed', 'questions', 'stopped_at', 'next_suggestion', 'paths', 'handoff'}


class ThreadError(Exception):
    def __init__(self, message, code='E_VALIDATION', status=2, details=None):
        super().__init__(message)
        self.code, self.status, self.details = code, status, details or {}


def required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ThreadError(f'{name} must be nonempty text')
    return value


def source_key(source):
    parts = [source['platform'], source['session'], source['anchor']]
    if source.get('host') or source.get('agentId'):
        parts += [source.get('host', ''), source.get('agentId', '')]
    return json.dumps(parts, ensure_ascii=False)


def source(value):
    if not isinstance(value, dict):
        raise ThreadError('source must be an object')
    needed = {'platform', 'session', 'anchor', 'time', 'role', 'excerpt', 'excerpt_hash'}
    if set(value) - (needed | {'path', 'host', 'agentId', 'start_dir'}) or not needed <= set(value):
        raise ThreadError('source requires platform/session/anchor/time/role/excerpt/excerpt_hash; optional path/host/agentId/start_dir')
    out = {}
    for key, val in value.items():
        if key in ('agentId', 'start_dir') and val == '':
            out[key] = val
        else:
            out[key] = required_text(val, 'source.' + key)
    if out['role'] not in ('user', 'assistant'):
        raise ThreadError('source role must be user or assistant')
    digest = hashlib.sha256(out['excerpt'].encode('utf-8')).hexdigest()
    if out['excerpt_hash'] != digest:
        raise ThreadError('source excerpt_hash does not match excerpt')
    return out


def fields(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ThreadError('unknown item fields')
    out = copy.deepcopy(payload)
    for key, val in out.items():
        if key in ('paths', 'questions', 'handoff'):
            if not isinstance(val, list) or any(not isinstance(x, str) or not x.strip() for x in val):
                raise ThreadError(key + ' must be a list of nonempty strings')
            out[key] = list(dict.fromkeys(val))
        elif key == 'confirmed':
            if not isinstance(val, list):
                raise ThreadError('confirmed must be a list of {said, source}')
            for entry in val:
                if not isinstance(entry, dict) or set(entry) != {'said', 'source'}:
                    raise ThreadError('confirmed entries require said and source')
                statement(entry)
        else:
            required_text(val, key)
    if 'next_suggestion' in out:
        out['next_suggestion'] = {'text': out['next_suggestion'], 'by': 'AI'}
    return out


def statement(payload):
    said = required_text(payload.get('said'), 'said')
    src = source(payload.get('source'))
    if src['role'] != 'user' or said not in src['excerpt']:
        raise ThreadError('said must occur verbatim in a user source excerpt')
    return said, src


def annotate_confirmed(values, verification):
    checked = verification.get('confirmed_entries', [])
    for index, entry in enumerate(values.get('confirmed', [])):
        result = checked[index] if index < len(checked) else {'verified': False, 'reason': 'original message not verified'}
        entry['verified'] = result.get('verified') is True
        entry['verification_note'] = str(result.get('reason', ''))


def normalized(text):
    return ' '.join(text.casefold().split())


def fingerprint(title, sources):
    return normalized(title), sorted((source_key(s), normalized(s['excerpt'])) for s in sources)


def get_item(state, ident):
    if ident not in state['items']:
        raise ThreadError('thread not found: ' + ident, 'E_NOT_FOUND', 1)
    return state['items'][ident]


def check_rev(item, rev):
    if type(rev) is not int or rev < 1:
        raise ThreadError('--rev must be a positive integer')
    if item['rev'] != rev:
        raise ThreadError('revision conflict; read thread again', 'E_VALIDATION', 1,
                          {'expected': rev, 'actual': item['rev']})


def add_sources(item, sources):
    keys = {(source_key(s), s['excerpt_hash']) for s in item['sources']}
    for src in sources:
        key = (source_key(src), src['excerpt_hash'])
        if key not in keys:
            item['sources'].append(copy.deepcopy(src))
            keys.add(key)


def evolve(state, op, payload, now, ident, rev=None, into=None, distinct=False, verified=None):
    """Return receipt; mutate only the passed state, with no filesystem work."""
    if not isinstance(payload, dict):
        raise ThreadError('input must be a JSON object')
    verification = verified or {'verified': False, 'reason': 'original message not verified'}
    if op == 'seen':
        if set(payload) != {'platform', 'session', 'version'}:
            raise ThreadError('seen requires platform, session, version')
        for k, v in payload.items():
            required_text(v, k)
        key = payload['platform'] + ':' + payload['session']
        state['seen'][key] = {'version': payload['version'], 'reviewed_at': now}
        return {'seen_key': key, 'seen': copy.deepcopy(state['seen'][key])}
    if op == 'add':
        allowed = FIELDS | {'sources'}
        if set(payload) - allowed:
            raise ThreadError('unknown add fields')
        srcs = payload.get('sources', [])
        if not isinstance(srcs, list):
            raise ThreadError('sources must be a list')
        srcs = [source(s) for s in srcs]
        values = fields({k: v for k, v in payload.items() if k in FIELDS})
        if into:
            if values:
                raise ThreadError('add --into only appends sources; use set --rev for fields')
            if not srcs:
                raise ThreadError('add --into requires sources')
            item = get_item(state, into)
            before = copy.deepcopy(item['sources'])
            add_sources(item, srcs)
            if before == item['sources']:
                return {'id': into, 'replayed': True}
            check_rev(item, rev)
            item['rev'] += 1
            item['log'].append({'at': now, 'action': 'append_sources'})
            return {'id': into}
        required_text(values.get('title'), 'title')
        if not srcs:
            raise ThreadError('add requires at least one source')
        for old in state['items'].values():
            old_title = old['capture_identity']['title']
            old_sources = [tuple(pair) for pair in old['capture_identity']['sources']]
            new_title, new_sources = fingerprint(values['title'], srcs)
            if old_title == new_title and set(new_sources) <= set(old_sources):
                return {'id': old['id'], 'replayed': True}
        collisions = [old['id'] for old in state['items'].values()
                      if {source_key(s) for s in old['sources']} & {source_key(s) for s in srcs}]
        if collisions and not distinct:
            raise ThreadError('same anchor already used; choose --distinct or --into', details={'items': collisions})
        similar = [old['id'] for old in state['items'].values() if normalized(old['title']) == normalized(values['title'])]
        annotate_confirmed(values, verification)
        capture_title, capture_sources = fingerprint(values['title'], srcs)
        item = {'id': ident, 'rev': 1, 'state': 'open', **values, 'sources': [],
                'capture_identity': {'title': capture_title, 'sources': [list(pair) for pair in capture_sources]},
                'log': [{'at': now, 'action': 'add'}]}
        add_sources(item, srcs)
        state['items'][ident] = item
        return {'id': ident, 'similar': similar}
    if op == 'pickup':
        return pickup(get_item(state, ident), payload, now)
    item = get_item(state, ident)
    check_rev(item, rev)
    if op == 'transfer':
        event = transfer(item, payload, now)
        if event is None:
            return {'id': ident, 'replayed': True}
    elif op == 'set':
        values = fields(payload)
        annotate_confirmed(values, verification)
        item.update(values)
        event = {'at': now, 'action': op, 'fields': sorted(values)}
    else:
        if set(payload) - {'said', 'source', 'evidence'}:
            raise ThreadError('decision accepts said/source/evidence only')
        has_statement = 'said' in payload or 'source' in payload
        said, src = statement(payload) if op != 'done' or has_statement else (None, None)
        if op == 'done':
            evidence = payload.get('evidence')
            if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
                raise ThreadError('done requires a nonempty evidence list')
        elif 'evidence' in payload:
            raise ThreadError('evidence is only accepted by done')
        if op in ('do', 'later', 'drop') and item['state'] in CLOSED:
            raise ThreadError('closed thread requires explicit reopen first', status=1)
        if op == 'done' and item['state'] == 'transferred':
            raise ThreadError('transferred thread is tracked by its task; reopen before done', status=1)
        if op == 'reopen' and item['state'] not in ('parked',) + CLOSED:
            raise ThreadError('only parked, dropped, done or transferred threads can reopen', status=1)
        target = {'do': 'decided', 'later': 'parked', 'drop': 'dropped', 'done': 'done', 'reopen': 'open'}[op]
        item['state'] = target
        if op == 'reopen':
            item.pop('transferred_to', None)
        event = {'at': now, 'action': op, 'actor': 'user' if src else 'assistant',
                 'confirmed': bool(src) and verification.get('verified') is True, 'verification': verification}
        if src:
            add_sources(item, [src])
            event.update(said=said, source=src)
        if op == 'done':
            event['evidence'] = payload['evidence']
    item['rev'] += 1
    item['log'].append(event)
    return {'id': ident, **({'confirmed': event['confirmed'], 'verification': verification} if 'confirmed' in event else {})}


def pickup(item, payload, now):
    """Internal: no external rev, because state is re-read under the same lock as the write."""
    if set(payload) != {'source'}:
        raise ThreadError('pickup requires source only')
    src = source(payload['source'])
    if item['state'] not in ('open', 'decided'):
        return {'id': item['id'], 'linked': False, 'reason': 'not_active'}
    if any((s['platform'], s['session']) == (src['platform'], src['session']) for s in item['sources']):
        return {'id': item['id'], 'linked': False, 'reason': 'already_source'}
    add_sources(item, [src])
    item['rev'] += 1
    item['log'].append({'at': now, 'action': 'append_sources', 'via': 'pickup'})
    return {'id': item['id'], 'linked': True, 'reason': 'picked_up'}


def last_decision(item):
    return next((e for e in reversed(item['log']) if e.get('action') in DECISIONS), None)


def transfer(item, payload, now):
    if set(payload) != {'to', 'evidence'}:
        raise ThreadError('transfer requires to and evidence only')
    target = required_text(payload['to'], 'to')
    evidence = payload['evidence']
    if not isinstance(evidence, list) or not evidence or any(not isinstance(e, str) or not e.strip() for e in evidence):
        raise ThreadError('transfer requires a nonempty evidence list')
    if item['state'] == 'transferred':
        if item['transferred_to'] != target:
            raise ThreadError('thread already transferred to a different task', status=1)
        return None
    decision = last_decision(item)
    if item['state'] != 'decided' or not decision or decision.get('action') != 'do' or decision.get('confirmed') is not True:
        raise ThreadError('transfer requires a decided thread whose do decision was verified', status=1)
    item['state'] = 'transferred'
    item['transferred_to'] = target
    return {'at': now, 'action': 'transfer', 'actor': 'assistant', 'to': target, 'evidence': evidence}


def validate_state(state):
    def fail():
        raise ThreadError('invalid or unsupported threads database; left untouched', 'E_IO', 1)
    if not isinstance(state, dict) or set(state) != {'schema', 'items', 'seen'} or type(state['schema']) is not int or state['schema'] != 1:
        fail()
    if not isinstance(state['items'], dict) or not isinstance(state['seen'], dict):
        fail()
    for ident, item in state['items'].items():
        if not isinstance(item, dict) or item.get('id') != ident or not isinstance(ident, str):
            fail()
        if item.get('state') not in STATES or type(item.get('rev')) is not int or item['rev'] < 1:
            fail()
        if not isinstance(item.get('title'), str) or not item['title'].strip():
            fail()
        identity = item.get('capture_identity')
        if not isinstance(identity, dict) or set(identity) != {'title', 'sources'}:
            fail()
        if not isinstance(identity['title'], str) or not identity['title'] or not isinstance(identity['sources'], list) or not identity['sources']:
            fail()
        if any(not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(v, str) for v in pair) for pair in identity['sources']):
            fail()
        optional = {key: val for key, val in item.items() if key in FIELDS and key not in ('title', 'confirmed', 'next_suggestion')}
        if set(item) - (FIELDS | {'id', 'rev', 'state', 'sources', 'log', 'capture_identity', 'transferred_to'}):
            fail()
        if (item['state'] == 'transferred') != ('transferred_to' in item):
            fail()
        if 'transferred_to' in item and (not isinstance(item['transferred_to'], str) or not item['transferred_to'].strip()):
            fail()
        try:
            fields(optional)
            if 'next_suggestion' in item:
                suggestion = item['next_suggestion']
                if not isinstance(suggestion, dict) or set(suggestion) != {'text', 'by'} or suggestion['by'] != 'AI':
                    fail()
                required_text(suggestion['text'], 'next_suggestion.text')
            if 'confirmed' in item:
                if not isinstance(item['confirmed'], list):
                    fail()
                for entry in item['confirmed']:
                    if not isinstance(entry, dict) or set(entry) != {'said', 'source', 'verified', 'verification_note'}:
                        fail()
                    if type(entry['verified']) is not bool or not isinstance(entry['verification_note'], str):
                        fail()
                    statement(entry)
        except ThreadError:
            fail()
        if not isinstance(item.get('sources'), list) or not isinstance(item.get('log'), list):
            fail()
        try:
            for src in item['sources']:
                source(src)
        except ThreadError:
            fail()
        for event in item['log']:
            if not isinstance(event, dict) or not isinstance(event.get('at'), str) or not isinstance(event.get('action'), str):
                fail()
    for key, val in state['seen'].items():
        if not isinstance(key, str) or not isinstance(val, dict) or set(val) != {'version', 'reviewed_at'}:
            fail()
        if any(not isinstance(v, str) or not v for v in val.values()):
            fail()
    return state
