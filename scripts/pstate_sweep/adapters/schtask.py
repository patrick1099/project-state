"""Windows 计划任务的注册/注销（走 PowerShell 的 ScheduledTasks 命令，不落 XML 文件）。

本机 Esafenet 下 pythonw.exe 读不了加密的 .py，所以任务入口是明文的 run_hidden.pyw，
由它以无窗口方式拉起白名单绝对路径 python.exe 跑 pstate.py sweep。
"""
import subprocess
import sys
from pathlib import Path


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def install_script(task_name, interval_hours, launcher, pythonw):
    launcher = validate_launcher(launcher)
    if Path(pythonw).resolve() != Path(default_pythonw()).resolve():
        raise ValueError("task interpreter must be the fixed absolute pythonw")
    return "$ErrorActionPreference = 'Stop'; " + "; ".join([
        f"$a = New-ScheduledTaskAction -Execute {_ps_quote(pythonw)} -Argument {_ps_quote(chr(34) + str(launcher) + chr(34) + ' run')}",
        "$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
        f"$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours {int(interval_hours)})",
        "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -StartWhenAvailable"
        " -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        f"Register-ScheduledTask -TaskName {_ps_quote(task_name)} -Action $a -Trigger @($t1, $t2) -Settings $s -Force | Out-Null",
        f"Write-Output ('registered ' + {_ps_quote(task_name)})",
    ])


def uninstall_script(task_name):
    return f"$ErrorActionPreference = 'Stop'; Unregister-ScheduledTask -TaskName {_ps_quote(task_name)} -Confirm:$false; Write-Output ('removed ' + {_ps_quote(task_name)})"


def default_pythonw():
    return "C:/Users/huawei/AppData/Local/Programs/Python/Python314/pythonw.exe"


def validate_launcher(launcher):
    supplied = Path(launcher).absolute()
    resolved = supplied.resolve()
    for candidate in (supplied, resolved):
        parts = [p.casefold() for p in candidate.parts]
        if any(parts[i:i + 2] == ["plugins", "cache"] for i in range(len(parts) - 1)):
            raise ValueError("cannot register a versioned plugin cache launcher; use the stable live source")
        for ancestor in candidate.parents:
            marker = ancestor / ".git"
            if marker.is_file():
                reference = marker.read_text(encoding="utf-8").strip()
                if not reference.startswith("gitdir:"):
                    raise ValueError("invalid repository marker")
                gitdir = (ancestor / reference[7:].strip()).resolve()
                if "worktrees" in {p.casefold() for p in gitdir.parts}:
                    raise ValueError("cannot register a worktree launcher; use the stable live source")
    if resolved.name != "run_hidden.pyw" or not resolved.is_file() or not (resolved.parent / "scripts" / "pstate.py").is_file():
        raise ValueError("launcher must be the stable project-state run_hidden.pyw")
    return resolved


def run_powershell(script):
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout + proc.stderr).strip()