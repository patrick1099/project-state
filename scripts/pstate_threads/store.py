"""Locked, strict, atomic storage. No historical scan occurs here."""
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pstate_lock
from .core import STATES, ThreadError, evolve, get_item, validate_state


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


def mutate(directory, op, payload, *, ident=None, rev=None, into=None, distinct=False,
           verified=None, dry_run=False, preview_state=None):
    directory = Path(directory)
    with pstate_lock.mutex(directory):
        if preview_state is not None and not dry_run:
            raise ThreadError('preview_state requires dry_run')
        previous = validate_state(copy.deepcopy(preview_state)) if preview_state is not None else load(directory)
        updated = copy.deepcopy(previous)
        result = evolve(updated, op, payload, datetime.now(timezone.utc).isoformat(),
                        ident or uuid.uuid4().hex, rev, into, distinct, verified)
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
        except Exception as exc:
            message = ('database was replaced; commit verification failed, inspect before retrying: '
                       if replaced else 'database not replaced: ')
            raise ThreadError(message + str(exc), 'E_IO', 1,
                              {'commit_status': 'replaced_unverified' if replaced else 'not_replaced'}) from exc
        finally:
            for pending in (tmp, backup_tmp):
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass
        result['committed'] = True
        if 'id' in result:
            result['item'] = copy.deepcopy(get_item(actual, result['id']))
        if 'seen_key' in result:
            result['seen'] = actual['seen'][result['seen_key']]
        return result


def card(item):
    result = copy.deepcopy(item)
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


def session_reminder(home, session, cmd):
    """Cards whose sources include this session, so the AI updates them as the work moves on."""
    if not session:
        return ''
    mine = [item for item in read(Path(home) / 'threads')['items'].values()
            if item['state'] in ('open', 'decided') and any(s['session'] == session for s in item['sources'])]
    if not mine:
        return ''
    lines = ['[pstate 卡片] 本会话是下列待续卡片的来源：']
    lines += [f"- {item['title']}（{item['id']}，rev {item['rev']}，{item['state']}，"
              f"卡片最后更新 {item['log'][-1]['at'][:16]} UTC）" for item in mine]
    lines.append(f"这轮做完后对照卡片自己判断，不用等用户开口：已做完 → {cmd} thread done <ID> --rev <N>"
                 "（evidence 写实际提交、产物和验证范围）；有实质进展或结论变了 → thread set 更新 "
                 "stopped_at / next_suggestion / questions；这轮没碰它就不用动。改了卡片就在回复里告诉用户改了什么。")
    return '\n'.join(lines)
