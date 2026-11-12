"""Editable Obsidian matter bodies and guarded, recoverable local patches."""
import copy
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import FIELDS, ThreadError

FOLDER = '事项卡片'
PREFIX = '<!-- pstate-card '
SUFFIX = ' -->'
LABELS = {'motivation': '动机', 'confirmed': '已有结论', 'questions': '未定问题',
          'stopped_at': '历史停点', 'next_suggestion': 'AI 建议',
          'paths': '关联工程', 'handoff': '进度现场'}
ORDER = ('title', 'motivation', 'confirmed', 'questions', 'stopped_at',
         'next_suggestion', 'paths', 'handoff')


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def conflict(message, **details):
    raise ThreadError(message, 'E_CARD_CONFLICT', 1, details)


def output_root(directory):
    mapping = Path(directory).parent / 'sweep' / 'paths.json'
    if not mapping.exists():
        return None
    try:
        value = json.loads(mapping.read_text(encoding='utf-8'))
        root = value.get('output_root')
        if value.get('version') != 1 or not isinstance(root, str) or not Path(root).is_absolute():
            raise ValueError('invalid output_root mapping')
        return Path(root).resolve()
    except (ValueError, OSError, AttributeError) as exc:
        raise ThreadError('cannot resolve card output mapping: ' + str(exc), 'E_IO', 1) from exc


@contextmanager
def lease(directory):
    root = output_root(directory)
    if root is None:
        yield None
        return
    from pstate_sweep.adapters import output
    current = output._CURRENT.get()
    state_dir = Path(directory).parent / 'sweep'
    if current is not None:
        if output.canonical(current.root) != output.canonical(root) or output.canonical(current.state_dir) != output.canonical(state_dir):
            conflict('active output lease belongs to another state home')
        output.validate_owner(state_dir, root)
        yield root
    else:
        with output.OutputLease(root, state_dir):
            yield root


def ref_path(root, reference):
    relative = Path(reference['path'])
    if relative.is_absolute() or '..' in relative.parts or len(relative.parts) != 2 or relative.parts[0] != FOLDER:
        conflict('invalid card reference path')
    path = root / relative
    resolved = path.resolve()
    if resolved.parent != (root / FOLDER).resolve():
        conflict('card path escapes its directory')
    current = root
    for part in relative.parts:
        current = current / part
        try:
            stat = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(stat, 'st_file_attributes', 0) & 0x400:
            conflict('card path contains a symlink or junction')
    return path


def split_text(text, ident):
    first, separator, body = text.partition('\n')
    first = first.rstrip('\r')
    if not separator or not first.startswith(PREFIX) or not first.endswith(SUFFIX):
        conflict('card identity metadata is missing or changed', id=ident)
    try:
        metadata = json.loads(first[len(PREFIX):-len(SUFFIX)])
    except ValueError as exc:
        conflict('card identity metadata is invalid', id=ident)
    if not isinstance(metadata, dict) or metadata.get('id') != ident:
        conflict('card identity does not match the thread', id=ident)
    marker = '<!-- /pstate-card -->'
    if marker not in body:
        conflict('card managed metadata end marker is missing', id=ident)
    managed, separator, body = body.partition(marker)
    if body.startswith('\r\n'):
        body = body[2:]
    elif body.startswith('\n'):
        body = body[1:]
    else:
        conflict('card metadata boundary is invalid', id=ident)
    return metadata, body


def read_text(path, ident):
    try:
        text = path.read_bytes().decode('utf-8')
    except (OSError, UnicodeError) as exc:
        raise ThreadError('cannot read editable card: ' + str(exc), 'E_IO', 1) from exc
    metadata, body = split_text(text, ident)
    return text, metadata, body


def header(item, operation):
    value = {'id': item['id'], 'state': item['state'], 'rev': item['rev'], 'operation': operation}
    if 'transferred_to' in item:
        value['transferred_to'] = item['transferred_to']
    labels = {'open': '待续', 'decided': '已决定推进', 'parked': '暂放', 'dropped': '已放弃', 'done': '已完成', 'transferred': '已转交'}
    visible = '> pstate 状态：' + labels[item['state']] + '\r\n'
    if 'transferred_to' in item:
        task = Path(item['transferred_to'])
        visible += '> 转交任务：[[' + task.name + ']] · [打开任务目录](<' + task.as_uri() + '/>)\r\n'
    return (PREFIX + json.dumps(value, ensure_ascii=False, separators=(',', ':')) + SUFFIX + '\r\n'
            + visible + '<!-- /pstate-card -->\r\n')


def fragment(field, value):
    if field == 'title':
        return '# ' + str(value) + '\r\n'
    if value is None:
        return ''
    if field == 'confirmed':
        lines = []
        for entry in value:
            verified = entry.get('verified') is True
            status = '已核验' if verified else '未核验'
            lines.append('- ' + entry['said'] + '（原话核验：' + status + '；核验只证明出处）')
            reason = ' '.join(str(entry.get('verification_note', '')).split())[:200]
            if not verified and reason:
                lines.append('  - 未核验原因：' + reason)
        text = '\r\n'.join(lines)
    elif isinstance(value, list):
        text = '\r\n'.join('- ' + str(entry) for entry in value)
    elif isinstance(value, dict):
        text = str(value.get('text', ''))
    else:
        text = str(value)
    text = text.replace('\r\n', '\n').replace('\n', '\r\n')
    return '## ' + LABELS[field] + '\r\n\r\n' + text + '\r\n'


def initial(item):
    sections = {name: fragment(name, item[name]) for name in ORDER if name in item}
    body = '\r\n'.join(sections.values())
    if item['sources']:
        body += '\r\n## 来源\r\n\r\n' + '\r\n'.join(
            '- ' + source['platform'] + ' · ' + source['session'] + ' · ' + source['anchor']
            for source in item['sources']) + '\r\n'
    return body, sections


def filename(item):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', item['title']).strip(' .')[:80] or '事项'
    return FOLDER + '/' + title + '-' + item['id'] + '.md'


def exact_edits(body, edits):
    if not isinstance(edits, list) or not edits:
        raise ThreadError('edits must be a nonempty list')
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - {'old', 'new', 'count'} or not {'old', 'new'} <= set(edit):
            raise ThreadError('each edit requires old/new and optional count')
        old, new, count = edit['old'], edit['new'], edit.get('count', 1)
        if not isinstance(old, str) or not old or not isinstance(new, str) or type(count) is not int or count < 1:
            raise ThreadError('old/new/count must be nonempty text, text and positive integer')
        actual = body.count(old)
        if actual != count:
            conflict('editable body replacement does not match uniquely; read the card again', expected=count, actual=actual)
        body = body.replace(old, new, count)
    return body


def inspect(directory, item):
    reference = item.get('card_ref')
    root = output_root(directory)
    if reference is None:
        return {'path': None, 'body': None, 'sha256': None, 'sync_status': 'unbound' if root else 'unconfigured'}
    if root is None:
        return {'path': None, 'body': None, 'sha256': None, 'sync_status': 'mapping_missing'}
    path = ref_path(root, reference)
    if not path.exists():
        return {'path': str(path), 'body': None, 'sha256': None, 'sync_status': 'missing'}
    try:
        _, metadata, body = read_text(path, item['id'])
    except ThreadError as exc:
        return {'path': str(path), 'body': None, 'sha256': None, 'sync_status': 'conflict', 'error': str(exc)}
    return {'path': str(path), 'body': body, 'sha256': digest(body),
            'sync_status': 'pending' if 'pending' in reference else 'synced',
            'authority': 'body', 'metadata': metadata}


def prepare(directory, previous, item, changed=(), body_payload=None):
    root = output_root(directory)
    reference = previous.get('card_ref') if previous else None
    if root is None:
        if reference:
            conflict('bound card output mapping is missing; restore mapping before writing')
        return {'status': 'unconfigured', 'synced': False}
    from pstate_sweep.adapters import output
    operation = uuid.uuid4().hex
    if reference is None:
        body, sections = initial(item)
        reference = {'version': 1, 'path': filename(item), 'sections': sections}
        path = ref_path(root, reference)
        output.guard(path, dry_run=True)
        if path.exists():
            conflict('refusing to overwrite an existing unbound card', path=str(path))
        expected, edits = None, []
    else:
        reference = copy.deepcopy(reference)
        if 'pending' in reference:
            conflict('card has an unfinished write; run thread sync-cards before another change')
        path = ref_path(root, reference)
        output.guard(path, dry_run=True)
        if not path.is_file():
            conflict('bound editable card is missing; restore it, do not recreate it from JSON', path=str(path))
        text, _, body = read_text(path, item['id'])
        expected, edits = digest(text), []
        newline = '\r\n' if '\r\n' in body else '\n'
        if body_payload is not None:
            if set(body_payload) != {'expected_sha256', 'edits'}:
                raise ThreadError('edit-card requires expected_sha256 and edits')
            if body_payload['expected_sha256'] != digest(body):
                conflict('editable body hash changed; read the card again', actual=digest(body))
            edits = copy.deepcopy(body_payload['edits'])
            body = exact_edits(body, edits)
        else:
            for field in changed:
                if field not in FIELDS:
                    continue
                new = fragment(field, item.get(field)).replace('\r\n', '\n').replace('\n', newline)
                old = reference['sections'].get(field)
                if old is not None:
                    old = old.replace('\r\n', '\n').replace('\n', newline)
                if old is None:
                    if re.search(r'^## ' + re.escape(LABELS.get(field, '')) + r'\s*$', body, re.M):
                        conflict('a user-added field section already exists; use edit-card', field=field)
                    separator = '' if not body or body.endswith(newline + newline) else newline
                    body += separator + new
                    edits.append({'old': '', 'new': separator + new, 'append': True})
                else:
                    patch = {'old': old, 'new': new, 'count': 1}
                    body = exact_edits(body, [patch])
                    edits.append(patch)
                reference['sections'][field] = new
    reference['pending'] = {'operation': operation, 'expected_sha256': expected,
                            'text': header(item, operation) + body, 'edits': edits}
    item['card_ref'] = reference
    return {'status': 'prepared', 'synced': False, 'path': str(path)}


def finish(directory, item, dry_run=False):
    reference = item.get('card_ref')
    if reference is None:
        return {'status': 'unconfigured', 'synced': False}
    pending = reference.get('pending')
    if pending is None:
        status = inspect(directory, item)['sync_status']
        return {'status': status, 'synced': status == 'synced'}
    root = output_root(directory)
    if root is None:
        conflict('card output mapping is missing during recovery')
    from pstate_sweep.adapters import output
    path = ref_path(root, reference)
    output.guard(path, dry_run=dry_run)
    expected = pending['expected_sha256']
    proposed = pending['text']
    exists = path.exists()
    if exists:
        current, metadata, body = read_text(path, item['id'])
        if metadata.get('operation') == pending['operation']:
            reference.pop('pending')
            return {'status': 'synced', 'synced': True, 'path': str(path), 'recovered': True}
        if expected is None:
            conflict('a new editable card appeared before creation; left untouched', path=str(path))
        if digest(current) != expected:
            for edit in pending['edits']:
                if edit.get('append'):
                    body += edit['new']
                else:
                    body = exact_edits(body, [edit])
            proposed = header(item, pending['operation']) + body
        expected = digest(current)
    elif expected is not None:
        conflict('bound editable card disappeared during a pending operation', path=str(path))
    if dry_run:
        reference.pop('pending')
        return {'status': 'preview', 'synced': False, 'path': str(path), 'sha256': digest(split_text(proposed, item['id'])[1])}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.pstate-card-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(proposed.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        output.guard(path)
        if path.exists() != exists or (exists and digest(path.read_bytes().decode('utf-8')) != expected):
            conflict('editable card changed immediately before write; left untouched', path=str(path))
        os.replace(temporary, path)
        actual, metadata, body = read_text(path, item['id'])
        if actual != proposed:
            raise ThreadError('card was replaced but readback differs; inspect before retrying', 'E_IO', 1,
                              {'card_commit_status': 'replaced_unverified', 'path': str(path)})
    finally:
        temporary.unlink(missing_ok=True)
    reference.pop('pending')
    return {'status': 'synced', 'synced': True, 'path': str(path), 'sha256': digest(body)}
