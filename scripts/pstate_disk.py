"""Observe on-disk Esafenet headers through a non-whitelisted PowerShell process."""
import json
import os
from pathlib import Path
import subprocess

MAGIC = (224,168,145,231,216,242,5,172)
PROBE = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
  $paths = ConvertFrom-Json ([Console]::In.ReadToEnd())
  $result = @()
  foreach ($path in $paths) {
    $exists = $true; $encrypted = $false
    try {
      $stream = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
      try {
        $bytes = New-Object byte[] 8
        $count = $stream.Read($bytes, 0, 8)
        $encrypted = $count -eq 8 -and [System.BitConverter]::ToString($bytes) -eq 'E0-A8-91-E7-D8-F2-05-AC'
      } finally { $stream.Dispose() }
    } catch [System.IO.FileNotFoundException] { $exists = $false
    } catch [System.IO.DirectoryNotFoundException] { $exists = $false }
    $result += [pscustomobject]@{ path = $path; exists = $exists; encrypted = $encrypted }
  }
  ConvertTo-Json -InputObject @($result) -Compress
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
"""


def probe(paths):
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not executable.is_file():raise OSError('PowerShell 不可用，无法验证磁盘加密态')
    result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command',PROBE],
                          input=json.dumps(paths,ensure_ascii=True),capture_output=True,text=True,
                          encoding='utf-8',errors='replace',timeout=30)
    if result.returncode:raise OSError('磁盘加密态探测失败：'+result.stderr.strip())
    try:rows=json.loads(result.stdout.lstrip('\ufeff'))
    except ValueError as exc:raise OSError('磁盘加密态探测返回无效JSON') from exc
    if not isinstance(rows,list) or len(rows)!=len(paths):raise OSError('磁盘加密态探测覆盖不完整')
    expected=set(paths);out={}
    for row in rows:
        if (not isinstance(row,dict) or row.get('path') not in expected
                or not isinstance(row.get('exists'),bool) or not isinstance(row.get('encrypted'),bool)
                or row['path'] in out):raise OSError('磁盘加密态探测记录无效')
        out[row['path']]={'exists':row['exists'],'encrypted':row['encrypted']}
    return out


def snapshot(paths, *, platform_name=None, observer=None):
    platform_name=os.name if platform_name is None else platform_name
    if platform_name!='nt':return {'supported':False,'files':{}}
    normalized=[]
    for raw in paths:
        if any(x in str(raw) for x in '*?[]'):raise ValueError('加密态检查不接受通配符，请声明具体文件')
        p=Path(raw).resolve()
        if p.is_dir():raise ValueError('加密态检查不递归目录，请声明全部具体文件')
        normalized.append(str(p))
    normalized=list(dict.fromkeys(normalized))
    return {'supported':True,'files':(observer or probe)(normalized)}


def verify(before, *, observer=None):
    if not before['supported']:return []
    after=(observer or probe)(list(before['files']))
    return [path for path,old in before['files'].items()
            if old['exists'] and old['encrypted'] and after[path]['exists'] and not after[path]['encrypted']]
