"""文档探测：直接加载已安装 vibe-flow 的 hooks/doc_gate.py，用它的判定，不复制一份。"""
import importlib.util
import json
from pathlib import Path


def locate(installed_plugins_json, override=None):
    """返回 doc_gate.py 路径；找不到抛 FileNotFoundError。"""
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"配置的 doc_gate 路径不存在：{path}")
        return path
    data = json.loads(Path(installed_plugins_json).expanduser().read_text(encoding="utf-8"))
    for key, installs in (data.get("plugins") or {}).items():
        if key.split("@", 1)[0] != "vibe-flow":
            continue
        for inst in installs or []:
            path = Path(inst.get("installPath", "")) / "hooks" / "doc_gate.py"
            if path.is_file():
                return path
    raise FileNotFoundError("没有找到已安装的 vibe-flow 插件的 doc_gate.py")


def load(path):
    spec = importlib.util.spec_from_file_location("vibe_flow_doc_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def newest_per_dir(paths, mtime=None):
    """每个目录只留最近变动的一个文件。

    evaluate() 对每个路径都往上逐层扫目录，几百个路径在 Esafenet 下要近一分钟；它对同一项目只取
    代码的最晚变动时间，所以同目录留最新一个结果相同。例外：同目录几个标准级脚本各带旁挂文档时，
    只会检查其中最新的那个——本来就是「疑似」提示，接受这点漏报。
    """
    mtime = mtime or (lambda p: Path(p).stat().st_mtime)
    best = {}
    for p in dict.fromkeys(paths):
        try:
            at = mtime(p)
        except OSError:
            at = float("inf")  # 已删除的文件让 evaluate 按目录时间算，保留
        key = str(Path(p).parent).lower()
        if key not in best or at > best[key][0]:
            best[key] = (at, p)
    return [p for _, p in best.values()]


def probe(gate, workspaces):
    """workspaces: {cwd: [会话里写过的文件]}。每个工作区再并上当前 git 改动，交给 evaluate()。

    返回 [{"root", "text"}]，按项目根去重。
    """
    issues = {}
    for cwd, written in workspaces.items():
        if not cwd or not Path(cwd).is_dir():
            continue
        paths = newest_per_dir(list(written) + gate.changed_since(cwd, 0))
        if not paths:
            continue
        for kind, proj, missing in gate.evaluate(paths):
            root = str(proj["root"])
            if kind == "missing":
                text = f"缺 {' / '.join(missing)}"
            else:
                text = "BLUEPRINT 和 CHANGELOG 都比代码旧"
            issues[root] = text
    return [{"root": r, "text": t} for r, t in sorted(issues.items())]