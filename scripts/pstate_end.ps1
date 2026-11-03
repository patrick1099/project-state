$ErrorActionPreference = 'Stop'
try {
    $data = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if (-not ($data.session_id -is [string]) -or -not $data.session_id) { throw 'missing session_id' }
    $sid = 'codex:' + $data.session_id
    $stateRoot = $env:PSTATE_HOME
    if (-not $stateRoot) { $stateRoot = Join-Path $env:LOCALAPPDATA 'ai-project-state' }
    $ended = Join-Path $stateRoot 'ended'
    [IO.Directory]::CreateDirectory($ended) | Out-Null
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $key = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($sid))).Replace('-', '').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $target = Join-Path $ended $key
    $temp = $target + '.' + $PID + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, (@{session_id=$sid} | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding $false))
        try { [IO.File]::Move($temp, $target) }
        catch { if (-not [IO.File]::Exists($target)) { throw } }
    }
    finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
    exit 0
}
catch { [Console]::Error.WriteLine('[pstate] SessionEnd receipt failed: ' + $_.Exception.Message); exit 1 }
