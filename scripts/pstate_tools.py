"""Bounded tool-input analysis; unknown shell code is never labelled read-only."""
import ast
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

NATIVE_WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "exec_command", "shell_command", "shell"}
PATCH_HEADERS = ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")
READ_COMMANDS = {"echo", "printf", "pwd", "ls", "dir", "cat", "type", "head", "tail", "wc", "stat", "test",
                 "get-content", "get-childitem", "get-item", "get-location", "select-object",
                 "select-string", "where-object", "format-list", "format-table", "write-output"}
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "cat-file",
            "grep", "check-ignore", "merge-base"}
META = {";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "2>", "2>>"}

@dataclass(frozen=True)
class Intent:
    paths: tuple = ()
    directories: tuple = ()
    readonly: bool = False
    opaque: bool = False
    ambiguous_cwd: bool = False


def absolute(path, cwd):
    return os.path.realpath(os.path.join(cwd, path))


def unquote(token):
    return token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token


def words(command):
    lex = shlex.shlex(command, posix=False, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    lex.commenters = ""
    return [unquote(x) for x in lex]


def literal_paths(code, cwd):
    """Extract constant Python write targets without evaluating user code."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return [], [], False
    values, paths, scopes = {}, [], []
    relative = False
    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):return node.value
        if isinstance(node, ast.Name):return values.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            a, b = value(node.left), value(node.right)
            return os.path.join(a, b) if a is not None and b is not None else None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("Path", "str") and node.args:
            return value(node.args[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("Path", "joinpath"):
            base = value(node.func.value) if node.func.attr == "joinpath" else ""
            vals = [value(x) for x in node.args]
            return os.path.join(base, *vals) if base is not None and vals and all(v is not None for v in vals) else None
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            val = value(node.value)
            if val is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):values[target.id] = val
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and os.path.isabs(node.value):
            scopes.append(absolute(node.value, cwd))
        if not isinstance(node, ast.Call):continue
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id == "open" and node.args:
            mode = value(node.args[1]) if len(node.args) > 1 else "r"
            for kw in node.keywords:
                if kw.arg == "mode":mode = value(kw.value)
            if mode and any(x in mode for x in "wax+"):
                p = value(node.args[0])
                if p is not None:paths.append(absolute(p, cwd));relative |= not os.path.isabs(p)
        if isinstance(fn, ast.Attribute):
            if fn.attr in ("write_text", "write_bytes", "unlink", "touch", "mkdir", "rmdir", "rename", "replace"):
                p = value(fn.value)
                if p is not None:paths.append(absolute(p, cwd));relative |= not os.path.isabs(p)
                if fn.attr in ("rename", "replace") and node.args:
                    p = value(node.args[0])
                    if p is not None:paths.append(absolute(p, cwd));relative |= not os.path.isabs(p)
            if fn.attr in ("remove", "unlink", "rmdir", "makedirs", "rename", "replace", "copy", "copy2", "copyfile", "move", "rmtree"):
                count = 2 if fn.attr in ("rename", "replace", "move") else 1
                args = node.args[-1:] if fn.attr.startswith("copy") else node.args[:count]
                for arg in args:
                    p = value(arg)
                    if p is not None:paths.append(absolute(p, cwd));relative |= not os.path.isabs(p)
    return paths, scopes, relative


def option(tokens, *names):
    for i, token in enumerate(tokens):
        for name in names:
            if token.lower() == name.lower() and i + 1 < len(tokens):return tokens[i+1]
            if token.lower().startswith(name.lower()+"="):return token.split("=", 1)[1]
    return None


def simple_read(tokens):
    if not tokens:return True
    name = Path(tokens[0]).name.lower().removesuffix(".exe")
    if name in ("rg", "grep"):
        return not any(x.startswith(("--pre", "--hostname-bin")) for x in tokens[1:])
    if name in READ_COMMANDS:
        return not any("{" in x or "}" in x for x in tokens)
    if name != "git":return False
    args = tokens[1:]
    while args and args[0] in ("--no-pager", "-C"):
        args = args[2:] if args[0] == "-C" else args[1:]
    if args and args[0] in ('diff','show','log') and not {'--no-ext-diff','--no-textconv'} <= set(args):return False
    return bool(args and args[0] in GIT_READ and not any(
        x in ("-c", "--ext-diff", "--textconv", "--output", "--open-files-in-pager")
        or x.startswith(("--output=", "--config-env", "--open-files-in-pager=", "-O"))
        or x in ("--filters",) for x in args))


def shell_surface(command):
    quote=None
    out=[]
    opaque=False
    for char in command:
        if char in "\"'":
            if quote==char:quote=None
            elif quote is None:quote=char
        if quote is None and char in "()":opaque=True
        out.append(';' if quote is None and char in '\r\n' else char)
    return ''.join(out),opaque


def shell_intent(command, cwd, cwd_trusted=True):
    paths, scopes = [], [cwd]
    ambiguous = False
    def add_path(raw, base=None, trusted=None):
        nonlocal ambiguous
        base = cwd if base is None else base
        trusted = cwd_trusted if trusted is None else trusted
        ambiguous |= not trusted and not os.path.isabs(raw)
        paths.append(absolute(raw,base))
    def add_paths(items):
        for item in items:add_path(item)
    unsafe_syntax = bool(re.search(r"\$\(|`|\$\{|<\(|>\(", command))
    here = re.fullmatch(r"\s*@(['\"])\r?\n(.*?)\r?\n\1@\s*\|\s*&?\s*([^\r\n]+?)\s+-\s*", command, re.S)
    if here:
        p, s, relative = literal_paths(here.group(2), cwd)
        return Intent(tuple(p), tuple(scopes+s), False, True, relative and not cwd_trusted)
    command,parentheses = shell_surface(command)
    unsafe_syntax = unsafe_syntax or parentheses
    try:tokens = words(command)
    except ValueError:return Intent((), tuple(scopes), False, True)
    segments, current = [], []
    for token in tokens:
        if token in (";", "&&", "||", "|", "&"):
            if current:segments.append(current);current=[]
        else:current.append(token)
    if current:segments.append(current)
    readonly, opaque = not unsafe_syntax, unsafe_syntax
    for part in segments:
        if not part:continue
        name = Path(part[0]).name.lower().removesuffix(".exe").removesuffix(".cmd")
        for token in part:
            if os.path.isabs(token) or (("/" in token or "\\" in token) and not token.startswith("-") and "://" not in token):scopes.append(absolute(token,cwd))
        if name in ("cd", "chdir", "set-location"):
            target = option(part,"-LiteralPath","-Path") or (part[1] if len(part)>1 else None)
            if target and not any(x in target for x in "$*?{}[],"):
                cwd_trusted = cwd_trusted or os.path.isabs(target)
                cwd=absolute(target,cwd);scopes.append(cwd)
            else:readonly=False;opaque=True
            continue
        runner = name == "pstate" or any(Path(x).name == "pstate.py" for x in part[:4])
        if runner and "run" in part:
            declared = part[:part.index("--")] if "--" in part else part
            if "--dry-run" in declared or "--ai-help" in declared or "--help" in declared:
                continue
            writes = [declared[i+1] for i,x in enumerate(declared[:-1]) if x == "--write"]
            if writes:
                add_paths(writes)
                readonly = False
                continue
        if name == "git":
            target=option(part,"-C")
            if target:scopes.append(absolute(target,cwd))
        redirected=False
        for i,token in enumerate(part):
            if token in (">", ">>", "2>", "2>>") and i+1<len(part):
                add_path(part[i+1]);redirected=True
        if redirected:readonly=False
        is_esafe = name in ("esafe-code","esafe_code.py") or any(Path(x).name == "esafe_code.py" for x in part[:3])
        if is_esafe and "--apply" not in part and any(x in part for x in ("read","search","info","files","edit","create")) and not redirected and not unsafe_syntax:
            continue
        if simple_read(part) and not redirected and not unsafe_syntax:continue
        readonly=False
        if name in ("esafe-code","esafe_code.py") or any(Path(x).name == "esafe_code.py" for x in part[:3]):
            indexes=[i for i,x in enumerate(part) if x in ("edit","create")]
            if indexes and "--apply" in part and indexes[0]+1<len(part):
                base=option(part,"--root") or cwd;add_path(part[indexes[0]+1],absolute(base,cwd),os.path.isabs(base) and (option(part,"--root") is not None or cwd_trusted));continue
            if any(x in part for x in ("read","search","info","files","edit","create")) and "--apply" not in part:
                continue
        if name in ("set-content","add-content","out-file","remove-item","new-item"):
            target=option(part,"-LiteralPath","-Path","-FilePath") or (part[1] if len(part)>1 and not part[1].startswith('-') else None)
            if target and not any(x in target for x in "$*?{}[],"):
                add_path(target);continue
        if name in ("rm","del","erase","mkdir","rmdir","touch"):
            args=[x for x in part[1:] if not x.startswith('-')]
            if args and not any(any(y in x for y in "$*?{}[],") for x in args):
                add_paths(args);continue
        if name in ("cp","mv","copy","move","copy-item","move-item"):
            src=option(part,"-LiteralPath","-Path");dst=option(part,"-Destination")
            if src and dst:targets=[dst] if name.startswith(('cp','copy')) else [src,dst]
            else:targets=[x for x in part[1:] if not x.startswith('-')][-2:]
            if (len(targets)==2 or src and dst) and not any(any(ch in x for ch in '$*?{}[],') for x in targets):
                if name in ('cp','copy','copy-item'):targets=targets[-1:]
                add_paths(targets);continue
        if name.startswith("python") or name in ("py","python3"):
            if '-c' in part:
                i=part.index('-c')
                if i+1<len(part):
                    p,s,relative=literal_paths(part[i+1],cwd);paths.extend(p);scopes.extend(s);ambiguous |= relative and not cwd_trusted
        if redirected and name in ("echo","printf","write-output"):continue
        opaque=True
    return Intent(tuple(dict.fromkeys(paths)),tuple(dict.fromkeys(scopes)),readonly,opaque,ambiguous)


def analyze(data, host="claude"):
    tool=str(data.get('tool_name') or '').rsplit('.',1)[-1]
    ti=data.get('tool_input') or {}
    cwd=str(data.get('cwd') or os.getcwd())
    if tool=='apply_patch':
        patch=ti if isinstance(ti,str) else ti.get('command',ti.get('patch',ti.get('input','')))
        if not isinstance(patch,str):raise ValueError('apply_patch input is not text')
        paths=[absolute(line[len(prefix):],cwd) for line in patch.splitlines() for prefix in PATCH_HEADERS if line.startswith(prefix) and line[len(prefix):]]
        if not paths:raise ValueError('apply_patch has no identifiable file paths')
        return Intent(tuple(dict.fromkeys(paths)),(cwd,))
    if not isinstance(ti,dict):raise ValueError('tool_input must be an object')
    if tool in NATIVE_WRITES:
        paths=[]
        for item in [ti]+(ti.get('edits',[]) if isinstance(ti.get('edits'),list) else []):
            if isinstance(item,dict):
                p=item.get('file_path') or item.get('notebook_path')
                if p:paths.append(absolute(p,cwd))
        if not paths:raise ValueError('write tool has no identifiable file paths')
        return Intent(tuple(dict.fromkeys(paths)),(cwd,))
    if tool in SHELL_TOOLS:
        cwd=str(ti.get('workdir') or ti.get('cwd') or cwd)
        command=ti.get('cmd',ti.get('command',''))
        if isinstance(command,list):command=' '.join(shlex.quote(str(x)) for x in command)
        return shell_intent(str(command),cwd,cwd_trusted=host!="codex" or bool(ti.get("workdir") or ti.get("cwd")))
    return Intent(readonly=True)
