"""Shared host adapter for atomic file reservations and bounded shell checks."""
import contextlib
import copy
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

import pstate_lock as L
import pstate_tools as T


def correlation(data, host):
    raw=data.get('tool_use_id')
    if not raw:raise ValueError('hook payload lacks tool_use_id')
    return hashlib.sha256(json.dumps([host,data.get('session_id'),data.get('turn_id'),raw]).encode()).hexdigest()


def marker_path(home, data, host):
    return Path(home)/'inflight'/correlation(data,host)


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def diagnostic(home, event, exc):
    record={'at':time.time(),'event':event,'error':type(exc).__name__,'message':str(exc)}
    try:write_json(Path(home)/'hook-diagnostics'/(str(time.time_ns())+'.json'),record)
    except OSError:pass
    return f'[pstate] {event} 检查未完成：{type(exc).__name__}: {exc}'


def emit_deny(reason):
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny',
                                         'permissionDecisionReason':reason}},ensure_ascii=False))


def overlaps(a,b):
    a,b=a.rstrip('/'),b.rstrip('/')
    return a in ('','.','/') or b in ('','.','/') or a==b or a.startswith(b+'/') or b.startswith(a+'/')


def foreign_claim(st, me, chain, rel=None):
    for key,c in st['claims'].items():
        if rel is not None and not overlaps(rel,key):continue
        holder=st['sessions'].get(c.get('holder'))
        if holder is None or c.get('holder')==me['sid'] or L.in_family(holder,chain):continue
        if not c.get('inflight') and L.released(c,key):continue
        return {'kind':'claim','rel':key,'holder':c['holder'],'claim':c}
    return None


def begin(data, home, state_dir_for, command, host='claude', dry_run=False):
    intent=T.analyze(data,host=host)
    if intent.readonly:return True
    targets={}
    for path,is_write in [(p,True) for p in intent.paths]+[(p,False) for p in intent.directories]:
        probe=os.path.join(path,".pstate-scope") if os.path.isdir(path) else path
        loc=L.locate(probe)
        if not loc:continue
        top,common=loc;sdir=state_dir_for(common)
        initial=L.load_state(sdir)
        if not L.is_personal(initial['enabled'],L.has_blueprint(top,probe)):continue
        key=os.path.normcase(str(sdir))
        entry=targets.setdefault(key,{'top':top,'sdir':sdir,'paths':set()})
        if is_write:entry['paths'].add(L.rel_key(top,path))
    if not targets:return True
    if intent.ambiguous_cwd:
        emit_deny('Codex 原生 shell hook 未传递实际 workdir，无法安全定位相对写路径。请使用绝对写路径、显式绝对 cd/--root，或 pstate run --write <绝对路径>；本次未登记或执行写入。')
        return False
    if L.E.is_ended(home,L.session_key(host,data.get('session_id') or '')):
        emit_deny('当前会话的结束回执尚未清理；请恢复会话，让 SessionStart 完成清理后再写入。')
        return False
    backend=L.process_backend()
    me,chain=L.identify(backend,data.get('session_id'),data.get('transcript_path') or '',host=host)
    if not me:raise RuntimeError('认不出当前宿主会话，拒绝个人项目写入')
    tool_id=correlation(data,host);now=time.time();deny=None;pending=[]
    with contextlib.ExitStack() as stack:
        for _,e in sorted(targets.items()):stack.enter_context(L.mutex(e['sdir']))
        for _,e in sorted(targets.items()):
            st=L.load_state(e['sdir']);L.prune(st,backend.alive);work=copy.deepcopy(st)
            e.update(original=st,state=work)
            if intent.opaque:
                info=foreign_claim(work,me,chain)
                if info:
                    deny=(e,info,'命令写入集合无法完整确定；本仓有其他活会话占用。请用 pstate run --write <路径> -- <命令> 显式声明写入集合，或改用可识别的编辑工具。');break
            for rel in sorted(e['paths']):
                info=foreign_claim(work,me,chain,rel)
                if info:deny=(e,info,'');break
                decision,info=L.evaluate_write(work,rel,me,chain,tool_id,e['top'],now,lambda c:L.released(c,rel))
                if decision=='deny':deny=(e,info,'');break
            if deny:
                # Preserve one-time takeover notices and presence without partial path reservations.
                if deny[1]['kind']=='taken':
                    st['taken'].pop(me['sid'],None);L.upsert_session(st,me,now)
                    if not dry_run:L.save_state(e['sdir'],st)
                break
            if e['paths']:pending.append(e)
        if deny:
            e,info,extra=deny
            # The denied session stays visible; it owns no part of a denied batch.
            st=e['original'];L.upsert_session(st,me,now)
            if not dry_run:L.save_state(e['sdir'],st)
            reason=L.deny_text(info,st,now,L.log_age,f'{command} --cwd "{e["top"]}"',L.claims_snapshot(Path(home)/'claims'))
            emit_deny(reason+('\n'+extra if extra else ''));return False
        if not pending or dry_run:return True
        marker=marker_path(home,data,host)
        record={'version':1,'session_id':me['sid'],'native_session_id':data.get('session_id'),'host':host,
                'turn_id':data.get('turn_id'),'tool_id':tool_id,'raw_tool_id':data.get('tool_use_id'),
                'state_dirs':[str(e['sdir']) for e in pending]}
        # A durable settlement handle precedes reservation commits; retrying settlement is idempotent.
        write_json(marker,record)
        committed=[]
        try:
            for e in pending:L.save_state(e['sdir'],e['state']);committed.append(e)
        except BaseException:
            for e in committed:L.save_state(e['sdir'],e['original'])
            marker.unlink(missing_ok=True)
            raise
    return True


def response_failed(data):
    response=data.get('tool_response')
    if isinstance(response,dict):
        if response.get('is_error') or response.get('isError'):return True
        metadata=response.get('metadata') or {}
        code=response.get('exit_code',metadata.get('exit_code'))
        if code is not None:return code!=0
        response=response.get('output',response.get('stdout',''))
    if isinstance(response,str):
        if response.lstrip().startswith('{'):
            try:return response_failed({'tool_response':json.loads(response)})
            except ValueError:pass
        codes=re.findall(r'(?:^|\n)(?:Exit code:|Process exited with code)\s*(-?\d+)',response)
        return any(int(x)!=0 for x in codes)
    return False


def read_marker(path):
    text=path.read_text(encoding='utf-8')
    try:
        record=json.loads(text)
    except ValueError:
        lines=text.splitlines()
        if len(lines)==2 and lines[0] and lines[1]:
            return {'version':0,'host':'claude','session_id':lines[1],
                    'raw_tool_id':path.name,'tool_id':path.name,'state_dirs':[lines[0]]}
        raise
    if not isinstance(record,dict) or record.get('version')!=1:
        raise ValueError('unsupported inflight marker')
    return record


def finish(data, home, failed=False, host='claude'):
    if not data.get('tool_use_id'):return 0
    marker=marker_path(home,data,host)
    # Compatibility with old Claude hooks that did not send session or turn in test/legacy receipts.
    if not marker.exists() and host=='claude':
        legacy=Path(home)/'inflight'/re.sub(r'[^\w.\-]','_',data['tool_use_id'])[:120]
        if legacy.exists() and read_marker(legacy).get('version')==0:marker=legacy
    if not marker.exists() and not data.get('session_id'):
        candidates=[]
        for p in (Path(home)/'inflight').glob('*'):
            try:r=read_marker(p)
            except (OSError,ValueError):continue
            if r.get('raw_tool_id')==data['tool_use_id'] and r.get('host')==host:candidates.append(p)
        if len(candidates)==1:marker=candidates[0]
    if not marker.exists():return 0
    record=read_marker(marker)
    if record.get('host')!=host:raise ValueError('inflight host mismatch')
    for sdir in record['state_dirs']:
        with L.mutex(sdir):
            st=L.load_state(sdir);L.finish_tool(st,record['tool_id'],failed or response_failed(data));L.save_state(sdir,st)
    marker.unlink(missing_ok=True);return 0


def end(data, home, host='claude'):
    raw=data.get('session_id')
    if not raw:return 0
    sid=L.session_key(host,raw);errors=[]
    for path in Path(home).glob('*/'+L.LOCK_FILE):
        try:
            with L.mutex(path.parent):
                st=L.load_state(path.parent,include_ended=True)
                if sid in st['sessions']:L.drop_session(st,sid);L.save_state(path.parent,st)
        except Exception as exc:errors.append(exc)
    claims_home=Path(home)/'claims'
    try:
        if (claims_home/L.CLAIMS_FILE).exists():
            with L.mutex(claims_home):
                claims=L.load_claims(claims_home,include_ended=True)
                if L.release_claims(claims,sid):L.save_claims(claims_home,claims)
    except Exception as exc:errors.append(exc)
    for marker in (Path(home)/'inflight').glob('*'):
        try:
            record=read_marker(marker)
            if record.get('session_id')==sid:marker.unlink()
        except Exception as exc:errors.append(exc)
    for exc in errors:diagnostic(home,'SessionEnd',exc)
    if errors:raise RuntimeError(f'{len(errors)} 项会话清理失败；原状态保留，见 hook-diagnostics')
    L.E.receipt_path(home,sid).unlink(missing_ok=True)
    return 0
