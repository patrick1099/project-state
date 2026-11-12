"""Locked, strict, atomic storage. No historical scan occurs here."""
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pstate_lock
from .core import STATES, ThreadError, evolve, get_item, last_touched, validate_state
from . import cards


def path_exists(path):
    try:
        path.stat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ThreadError('cannot inspect threads storage: ' + str(exc), 'E_IO', 1) from exc


def load(directory):
    path = directory / 'threads.json'
    if not path_exists(path):
        if path_exists(directory / 'bak.json'):
            raise ThreadError('primary database is missing but backup exists; recovery required', 'E_IO', 1)
        return {'schema': 1, 'items': {}, 'seen': {}}
    try:
        return validate_state(json.loads(path.read_text(encoding='utf-8')))
    except (ValueError, OSError) as exc:
        raise ThreadError('cannot read threads database; left untouched: ' + str(exc), 'E_IO', 1) from exc


def read(directory):
    directory = Path(directory)
    if not path_exists(directory):
        return {'schema': 1, 'items': {}, 'seen': {}}
    with pstate_lock.mutex(directory):
        return copy.deepcopy(load(directory))


def write_json(path, value):
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _legacy_mutate(directory, op, payload, *, ident=None, rev=None, into=None, distinct=False,
                   verified=None, dry_run=False, preview_state=None):
    directory = Path(directory)
    with pstate_lock.mutex(directory):
        if preview_state is not None and not dry_run:
            raise ThreadError('preview_state requires dry_run')
        previous = validate_state(copy.deepcopy(preview_state)) if preview_state is not None else load(directory)
        updated = copy.deepcopy(previous)
        result = evolve(updated, op, payload, datetime.now(timezone.utc).isoformat(),
                        ident or uuid.uuid4().hex, rev, into, distinct, verified)
        if 'id' in result and updated['items'][result['id']].get('card_ref'):
            raise ThreadError('bound editable card has no output mapping; restore it before mutation', 'E_CARD_CONFLICT', 1)
        validate_state(updated)
        result['dry_run'] = bool(dry_run)
        result['committed'] = False
        if dry_run or updated == previous:
            if dry_run and preview_state is not None:
                preview_state.clear()
                preview_state.update(copy.deepcopy(updated))
            if 'id' in result:
                result['item'] = copy.deepcopy(updated['items'][result['id']])
            return result
        actual = _persist(directory, previous, updated)
        result['committed'] = True
        if 'id' in result:
            result['item'] = copy.deepcopy(get_item(actual, result['id']))
        if 'seen_key' in result:
            result['seen'] = actual['seen'][result['seen_key']]
        return result


def card(item, directory=None):
    result = copy.deepcopy(item)
    if directory is not None:
        result['card'] = cards.inspect(directory, item)
        reference = item.get('card_ref')
        if reference:
            result['card']['link'] = reference['path'].removesuffix('.md')
        result['content_authority'] = ('card.body' if result['card'].get('body') is not None
                                       else 'bound_unavailable' if reference else 'legacy_snapshot')
        if result['card'].get('body'):
            import re
            title = re.search(r'^#\s+(.+)$', result['card']['body'], re.M)
            if title:
                result['display_title'] = title.group(1).strip()
    result['handoff_facts'] = []
    for name in item.get('handoff', []):
        path = Path(name)
        fact = {'path': name}
        try:
            stat = path.stat()
            fact.update(exists=True, is_file=path.is_file(),
                        mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat())
        except FileNotFoundError:
            fact['exists'] = False
        except OSError as exc:
            fact['error'] = str(exc)
        result['handoff_facts'].append(fact)
    if 'transferred_to' in item:
        result['transferred_facts'] = {'path': item['transferred_to'], 'exists': Path(item['transferred_to']).is_dir()}
    return result


def counts(state):
    return {name: sum(item['state'] == name for item in state['items'].values())
            for name in STATES}


def hook_hint(home, cmd):
    active = counts(read(Path(home) / 'threads'))
    if not active['open'] and not active['decided']:
        return ''
    return (f"[pstate 待续] 全局待回顾 {active['open']} 件，已决定推进 {active['decided']} 件。"
            f"需要回顾时运行：{cmd} thread list；不自动扫描历史。")


def session_reminder(home, session, cmd, limit=None):
    """Cards whose sources include this session, so the AI updates them as the work moves on; at most limit chars."""
    if not session:
        return ''
    mine = [item for item in read(Path(home) / 'threads')['items'].values()
            if item['state'] in ('open', 'decided') and any(s['session'] == session for s in item['sources'])]
    if not mine:
        return ''
    head = '[pstate 卡片] 本会话是下列待续卡片的来源：'
    advice = (f"这轮做完后对照卡片自己判断，不用等用户开口：已做完 → {cmd} thread done <ID> --rev <N>"
              "（evidence 写实际提交、产物和验证范围）；先 thread show 读当前权威正文及 hash，再用 edit-card 精确局部修改或 thread set 更新 "
              "stopped_at / next_suggestion / questions；这轮没碰它就不用动。改了卡片就在回复里告诉用户改了什么。")
    def more(hidden):
        return f'另有 {hidden} 张，{cmd} thread list 查看。'
    budget = limit - len(head) - len(advice) - len(more(len(mine))) - 3 if limit else None
    shown = []
    for item in mine:
        line = (f"- {item['title']}（{item['id']}，rev {item['rev']}，{item['state']}，"
                f"卡片最后更新 {last_touched(item)[:16]} UTC）")
        if budget is not None and sum(len(x) + 1 for x in shown) + len(line) + 1 > budget:
            break
        shown.append(line)
    hidden = len(mine) - len(shown)
    return '\n'.join([head, *shown, *([more(hidden)] if hidden else []), advice])

def _persist(directory, previous, updated):
    path, tmp = directory / 'threads.json', directory / 'tmp.json'
    backup_tmp = directory / 'bak.tmp.json'
    replaced = False
    try:
        write_json(tmp, updated)
        if path_exists(path):
            write_json(backup_tmp, previous)
            os.replace(backup_tmp, directory / 'bak.json')
        os.replace(tmp, path)
        replaced = True
        actual = load(directory)
        if actual != updated:
            raise ThreadError('readback differs from proposed database', 'E_IO', 1)
        return actual
    except Exception as exc:
        raise ThreadError('database commit verification failed: ' + str(exc), 'E_IO', 1,
                          {'commit_status': 'replaced_unverified' if replaced else 'not_replaced'}) from exc
    finally:
        for pending in (tmp, backup_tmp):
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass


def _request(op, payload, ident, rev, into, distinct):
    return json.dumps({'op': op, 'payload': payload, 'id': ident, 'rev': rev,
                       'into': into, 'distinct': distinct}, ensure_ascii=False, sort_keys=True)


def _preview_card(item, directory):
    result = card(item, directory)
    pending = item.get('card_ref', {}).get('pending')
    if pending:
        _, body = cards.split_text(pending['text'], item['id'])
        result['card'].update(body=body, sha256=cards.digest(body), sync_status='preview')
        result['content_authority'] = 'card.body'
    return result


def _finish_committed(directory, previous, updated, result, dry_run):
    item = updated['items'][result['id']]
    if not item.get('card_ref'):
        result.update(item=card(item, directory), card_sync={'status': 'unconfigured', 'synced': False}, committed=False)
        return result
    if dry_run:
        result['card_sync'] = cards.finish(directory, copy.deepcopy(item), dry_run=True)
        result['item'] = _preview_card(item, directory)
        result['committed'] = False
        return result
    committed_state = _persist(directory, previous, updated)
    result['committed'] = True
    try:
        result['card_sync'] = cards.finish(directory, item)
        if item != committed_state['items'][result['id']]:
            _persist(directory, committed_state, updated)
        result['item'] = card(item, directory)
        return result
    except Exception as exc:
        details = {'committed': True, 'database_committed': True, 'card_synced': False,
                   'id': result['id'], 'card_sync': {'status': 'pending', 'synced': False},
                   'item': card(committed_state['items'][result['id']], directory),
                   'recovery': 'Inspect show, then repeat the identical command or run thread sync-cards.'}
        if isinstance(exc, ThreadError):
            details['cause'] = {'code': exc.code, 'message': str(exc), 'details': exc.details}
        else:
            details['cause'] = str(exc)
        raise ThreadError('database committed but editable card synchronization did not finish',
                          'E_PARTIAL_FAILURE', 1, details) from exc


def _pending_replay(directory, previous, item, request, dry_run):
    pending = item.get('card_ref', {}).get('pending')
    if not pending:
        return None
    if pending.get('request') != request:
        raise ThreadError('unfinished card update requires thread sync-cards before another mutation',
                          'E_CARD_CONFLICT', 1, {'id': item['id']})
    updated = copy.deepcopy(previous)
    restored = updated['items'][item['id']]
    synchronization = cards.finish(directory, restored, dry_run=dry_run)
    if not dry_run:
        _persist(directory, previous, updated)
    return {'id': item['id'], 'item': _preview_card(item, directory) if dry_run else card(restored, directory),
            'replayed': True, 'committed': not dry_run, 'dry_run': dry_run, 'card_sync': synchronization}


def mutate(directory, op, payload, *, ident=None, rev=None, into=None, distinct=False,
           verified=None, dry_run=False, preview_state=None):
    directory = Path(directory)
    request = _request(op, payload, ident, rev, into, distinct)
    if cards.output_root(directory) is None:
        result = _legacy_mutate(directory, op, payload, ident=ident, rev=rev, into=into,
                                distinct=distinct, verified=verified, dry_run=dry_run, preview_state=preview_state)
        result['card_sync'] = {'status': 'unconfigured', 'synced': False}
        if 'item' in result:
            result['item'] = card(result['item'], directory)
        return result
    with cards.lease(directory):
        with pstate_lock.mutex(directory):
            if preview_state is not None and not dry_run:
                raise ThreadError('preview_state requires dry_run')
            previous = validate_state(copy.deepcopy(preview_state)) if preview_state is not None else load(directory)
            candidate = previous['items'].get(ident or into)
            if candidate:
                recovered = _pending_replay(directory, previous, candidate, request, dry_run)
                if recovered is not None:
                    return recovered
            updated = copy.deepcopy(previous)
            actual_payload = copy.deepcopy(payload)
            expected_body = actual_payload.pop('expected_sha256', None) if op == 'set' else None
            result = evolve(updated, op, actual_payload, datetime.now(timezone.utc).isoformat(),
                            ident or uuid.uuid4().hex, rev, into, distinct, verified)
            result.update(dry_run=bool(dry_run), committed=False)
            if 'id' not in result:
                if not dry_run and updated != previous:
                    _persist(directory, previous, updated)
                    result['committed'] = True
                elif dry_run and preview_state is not None:
                    preview_state.clear()
                    preview_state.update(copy.deepcopy(updated))
                return result
            item = updated['items'][result['id']]
            old_item = previous['items'].get(result['id'])
            recovered = _pending_replay(directory, previous, old_item, request, dry_run) if old_item else None
            if recovered is not None:
                return recovered
            if expected_body is not None:
                shown = cards.inspect(directory, old_item or item)
                if shown.get('sha256') != expected_body:
                    raise ThreadError('editable body hash changed; read the card again', 'E_CARD_CONFLICT', 1)
            changed = tuple(actual_payload) if op == 'set' else ()
            if updated == previous and item.get('card_ref'):
                status = cards.inspect(directory, item)['sync_status']
                result.update(item=card(item, directory), card_sync={'status': status, 'synced': status == 'synced'})
                return result
            cards.prepare(directory, old_item, item, changed)
            if 'pending' in item.get('card_ref', {}):
                item['card_ref']['pending']['request'] = request
            validate_state(updated)
            if dry_run and preview_state is not None:
                preview_state.clear()
                simulated = copy.deepcopy(updated)
                if old_item is None or not old_item.get('card_ref'):
                    simulated['items'][result['id']].pop('card_ref', None)
                else:
                    simulated['items'][result['id']]['card_ref'] = copy.deepcopy(old_item['card_ref'])
                preview_state.update(simulated)
            return _finish_committed(directory, previous, updated, result, dry_run)


def snapshot(directory):
    state = read(directory)
    state['items'] = {ident: card(item, directory) for ident, item in state['items'].items()}
    return state


def edit_card(directory, ident, rev, payload, dry_run=False):
    directory = Path(directory)
    request = _request('edit_card', payload, ident, rev, None, False)
    with cards.lease(directory):
        with pstate_lock.mutex(directory):
            previous = load(directory)
            old_item = get_item(previous, ident)
            recovered = _pending_replay(directory, previous, old_item, request, dry_run)
            if recovered is not None:
                return recovered
            if not old_item.get('card_ref'):
                raise ThreadError('thread has no editable card; configure output and run sync-cards first', 'E_CARD_CONFLICT', 1)
            updated = copy.deepcopy(previous)
            evolve(updated, 'set', {}, datetime.now(timezone.utc).isoformat(), ident, rev)
            item = updated['items'][ident]
            item['log'][-1]['action'] = 'edit_card'
            cards.prepare(directory, old_item, item, body_payload=payload)
            item['card_ref']['pending']['request'] = request
            validate_state(updated)
            return _finish_committed(directory, previous, updated,
                                     {'id': ident, 'dry_run': bool(dry_run), 'committed': False}, dry_run)


def sync_cards(directory, dry_run=False):
    directory = Path(directory)
    results = []
    with cards.lease(directory):
        with pstate_lock.mutex(directory):
            state = load(directory)
            for ident in list(state['items']):
                previous, updated = copy.deepcopy(state), copy.deepcopy(state)
                item = updated['items'][ident]
                try:
                    if 'pending' not in item.get('card_ref', {}):
                        if item.get('card_ref'):
                            shown = cards.inspect(directory, item)
                            if shown['sync_status'] != 'synced':
                                raise ThreadError('bound editable card requires recovery; left untouched', 'E_CARD_CONFLICT', 1, shown)
                            results.append({'id': ident, 'item': card(item, directory),
                                            'card_sync': {'status': 'synced', 'synced': True}, 'committed': False})
                            continue
                        cards.prepare(directory, None, item)
                    validate_state(updated)
                    result = _finish_committed(directory, previous, updated,
                                               {'id': ident, 'dry_run': bool(dry_run), 'committed': False}, dry_run)
                    results.append(result)
                    if not dry_run:
                        state = load(directory)
                except Exception as exc:
                    details = {'items': results, 'failed_id': ident, 'dry_run': dry_run,
                               'cause': str(exc), 'recovery': 'Inspect the affected card; repeat sync-cards after resolving the conflict.'}
                    if isinstance(exc, ThreadError):
                        details['error'] = {'code': exc.code, 'details': exc.details}
                    raise ThreadError('editable card synchronization stopped at a conflict', 'E_PARTIAL_FAILURE', 1, details) from exc
    return {'items': results, 'dry_run': bool(dry_run), 'committed': any(item['committed'] for item in results)}
