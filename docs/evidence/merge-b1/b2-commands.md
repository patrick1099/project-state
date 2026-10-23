# b2：稳定活源发布与正式切换命令

本文件是待执行清单，merge-b1 未运行下列正式操作。b1 独立验收结果为 P200/200、CLI7/7，S47/47且相关源码/测试指纹未变；证据见 [validation.md](validation.md)。用户已直接确认实施、发布与切换，无需重复询问。

## 1. 固定路径与入口核对

在正常用户 PowerShell 中执行。下面路径来自当前配置与本机默认解析，开始 b2 时再次核对 PSTATE_HOME、Git、任务和输出现场；任一执行失败就停在该步，不继续后续写入。

```powershell
$Python = 'C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe'
$Pythonw = 'C:/Users/huawei/AppData/Local/Programs/Python/Python314/pythonw.exe'
$P = 'C:/Users/huawei/treasury-vault/shared/plugins/project-state'
$S = 'C:/Users/huawei/Desktop/MyProjects/my-repos/session-sweep'
$PW = 'C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge'
$SW = 'C:/Users/huawei/Desktop/MyProjects/my-repos/session-sweep-pstate-merge'
$StateRoot = 'C:/Users/huawei/AppData/Local/ai-project-state'
$LegacyRoot = 'C:/Users/huawei/AppData/Local/session-sweep'
$Legacy = Join-Path $LegacyRoot 'state.json'
$Out = 'C:/Users/huawei/Desktop/Obsidian Vault/03-悬着的事'
$TaskName = 'session-sweep'
$Config = Join-Path $P 'sweep.toml'
$CLI = Join-Path $P 'scripts/pstate.py'
$Launcher = Join-Path $P 'run_hidden.pyw'
$env:PSTATE_HOME
Get-Content -LiteralPath $Config
```

当前 PSTATE_HOME 未设置；正式任务使用默认 home。若 b2 现场发现它已设置，先核对其真实目标与任务进程继承环境，不能把另一个 home 当默认正式库。下文显式指定本进程 home；计划任务环境还须通过真实启动日志核实。

## 2. 集成与发布

此步骤由主会话在 b1 ready、全部 worker 停止后执行。先核两仓 diff、个人 Git 身份和允许提交清单；无 AI 署名。P 两份 manifest 当前仍为0.3.1，b2 选择新版本并同步修改描述（后台已合入）及版本，不允许用旧版本刷新。提交前按 esafe-code 核加密态，.pyw须明文。本文件不提供未验证的自动 bump 命令。

```powershell
git -C $PW status --short
git -C $SW status --short
git -C $PW config user.name
git -C $PW config user.email
git -C $SW config user.name
git -C $SW config user.email
git -C $PW diff --check
git -C $SW diff --check
```

按已核对的文件范围暂存并提交两个实施分支。暂存清单须由 b2 的实际 status 决定，不能以通配 add 收进无关文件。P 新版同时覆盖 `.claude-plugin/plugin.json` 与 `.codex-plugin/plugin.json`。下列集成命令要求分支已提交且两活源无无关改动；若不能快进，停下核对，不自动改用覆盖或重写历史。

```powershell
git -C $P status --short
git -C $S status --short
git -C $P branch --show-current
git -C $S branch --show-current
git -C $P merge --ff-only feat/sweep-merge
git -C $S merge --ff-only feat/pstate-merge
git -C $P push origin main
hub sync --refresh
```

以上以两活源均在 main 为前提。P origin 已核为 project-state GitHub 子仓；S 当前 remote 查询为空，不编造 S 推送目标。hub sync --refresh 是既有发布流程；本轮未实际执行，也未核对外部 hub CLI 帮助或版本号。b2 执行时留存其真实退出码/刷新回执，核验 hub 插件指针与两平台新缓存。只刷新插件不等于正式迁移或安装后 hook 已通过。

集成后从稳定活源再核命令和旧入口退役。不能从 PW/SW 或插件版本缓存注册任务。

```powershell
& $Python -B $CLI sweep --ai-help
& $Python -B $CLI sweep --config $Config status --json
& $Python -B (Join-Path $S 'sweep.py') run --help
```

最后一条应明确退役且退出失败，不能再走旧扫描器。另核 S 明文 pyw 与低层退役检查；本轮隔离测试已覆盖可达模块/底层入口，不能承诺任意另存的旧源码也会受约束。

## 3. 禁止旧任务再触发，确认没有在途

```powershell
$OldTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
$OldTask | Select-Object TaskName,State,Actions,Triggers
Get-ScheduledTaskInfo -TaskName $TaskName
Disable-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and
    ($_.CommandLine.Contains($S) -or $_.CommandLine.Contains('session_sweep'))
} | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine
```

若任务仍 Running 或存在旧在途进程，等待其自然完成；查不清就不切换。不要用 Stop-Process 或按锁年龄判断结束。确认旧执行已退出后，才可执行下面停止任务命令，随后再次核 task 状态和进程现场。禁用不会撤回已启动的旧执行。

```powershell
Stop-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
```

## 4. 原资料快照与残留锁处理

先确认上述旧在途已结束，再备份整个旧 home、现有新 home 及整个输出目录（包含所有周档与用户文件）。快照只备份，不删除原文件；不要只备份计数、链接或主清单。记录旧任务 XML便于审计，回退不重新启用旧writer。

```powershell
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Backup = Join-Path $env:LOCALAPPDATA ('pstate-merge-backups/' + $Stamp)
New-Item -ItemType Directory -Path $Backup -ErrorAction Stop | Out-Null
$TaskXml = Export-ScheduledTask -TaskName $TaskName
[IO.File]::WriteAllText((Join-Path $Backup 'old-task.xml'), $TaskXml, [Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath $LegacyRoot -Destination (Join-Path $Backup 'legacy-home') -Recurse -ErrorAction Stop
if (Test-Path -LiteralPath $StateRoot) {
    Copy-Item -LiteralPath $StateRoot -Destination (Join-Path $Backup 'pstate-home') -Recurse -ErrorAction Stop
}
Copy-Item -LiteralPath $Out -Destination (Join-Path $Backup 'pages') -Recurse -ErrorAction Stop
Get-ChildItem -LiteralPath $Backup -Recurse -File | Get-FileHash -Algorithm SHA256 |
    Export-Csv -LiteralPath (Join-Path $Backup 'hashes.csv') -NoTypeInformation -Encoding utf8
```

切换命令不会接管未知旧 run.lock。若它仍存在，先读其现场并再次确认原进程确已结束、已完成上面快照；只在这些条件满足后将残留锁移到备份目录，保留原内容，不删除。若对进程身份或是否仍在写有疑问，停下排查。

```powershell
$LegacyLock = Join-Path $LegacyRoot 'run.lock'
Get-Item -LiteralPath $LegacyLock -ErrorAction SilentlyContinue
# 仅在已核实原进程结束、快照完成后执行：
if (Test-Path -LiteralPath $LegacyLock) {
    Move-Item -LiteralPath $LegacyLock -Destination (Join-Path $Backup 'confirmed-stopped-run.lock') -ErrorAction Stop
}
```

## 5. 显式导入、对账与切换

命令语法经 b1 真 CLI help/ai-help 与代码 parser 核验。预演和实际调用同一写路径；每个实际步骤前检查对应预演、退出码与回执。import/reconcile 只处理显式旧 state/out，不扫聊天、不调用模型。未知结构/损坏不能以空库覆盖。

```powershell
$env:PSTATE_HOME = $StateRoot
& $Python -B $CLI sweep --config $Config import-legacy --legacy-state $Legacy --out $Out --dry-run --json
& $Python -B $CLI sweep --config $Config import-legacy --legacy-state $Legacy --out $Out --json
& $Python -B $CLI sweep --config $Config reconcile --legacy-state $Legacy --out $Out --dry-run --json
& $Python -B $CLI sweep --config $Config reconcile --legacy-state $Legacy --out $Out --json
& $Python -B $CLI sweep --config $Config sources --json |
    Set-Content -LiteralPath (Join-Path $Backup 'sources-after-import.json') -Encoding utf8
& $Python -B $CLI sweep --config $Config switch --legacy-state $Legacy --out $Out --dry-run --json
& $Python -B $CLI sweep --config $Config switch --legacy-state $Legacy --out $Out --json
& $Python -B $CLI sweep --config $Config status --json
```

对账完整摘要（含默认隐藏的已完成/放弃/公司/失败）、未知字段、关闭/取消关闭、原时间与导入原页全文；核主清单、已处理页、所有周档、孤立旧档和原路径保留。只报统计不足以证明内容保全。比较迁移前后线程库，迁移不得把旧判断或勾选写成事项状态或 seen。来源资料输出大，留在本地备份核对，不将全部聊天/来源搬进对话。

switch 使用 sources/switching→归属→页面→enabled 顺序；中断时普通 run 应拒绝，核对回执后重复显式 switch 前向恢复，不能手改 activation 或删 owner 绕过。

## 6. 无模型刷新与真实隐藏入口

以下 run 会读取当前配置的历史 roots；--no-judge保证不调用模型，dry-run不写业务资料/页面。首次实际刷新后核页面内容、原档保留及勾选往返，再做后续安装。需要缩小样本时用另一个隔离 home/out 与显式 files，不能拿正式 owner 去写临时目录。

```powershell
& $Python -B $CLI sweep --config $Config run --out $Out --no-judge --dry-run --json
& $Python -B $CLI sweep --config $Config run --out $Out --no-judge --json
& $Python -B $CLI sweep --config $Config sources --json |
    Set-Content -LiteralPath (Join-Path $Backup 'sources-after-run.json') -Encoding utf8
$Launch = Start-Process -FilePath $Pythonw -ArgumentList @('"' + $Launcher + '"', 'run', '--no-judge', '--dry-run', '--json') -WindowStyle Hidden -Wait -PassThru
$Launch.ExitCode
Get-Content -LiteralPath (Join-Path $StateRoot 'sweep/last_launch.log')
```

实际隐藏入口须记录退出码与日志路径，验证固定解释器/稳定活源/no-window。pyw本批已验证0/1/2传递，正式入口仍应观察真实结果；日志有输出不等于页面/资料验收通过。

## 7. 注册新任务与观察真实执行

新旧 task_name 同为 session-sweep；安装会 Force 更新任务。先核 dry-run脚本的 Execute/Argument指向稳定 P/run_hidden.pyw与固定 pythonw，不能指向 worktree/cache。注册本身不启用writer，前面switch成功才可运行。

```powershell
& $Python -B $CLI sweep --config $Config install-task --dry-run --json
& $Python -B $CLI sweep --config $Config install-task --json
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State,Actions,Triggers,Settings
Get-ScheduledTaskInfo -TaskName $TaskName
Start-ScheduledTask -TaskName $TaskName
```

任务默认执行 run，会依 sweep.toml 使用模型及预算。核真实进程、last_launch.log、LastRunTime/LastTaskResult、activation归属和页面/来源结果；等待这一轮完成后才记录真实任务触发通过。之后未来登录/重复定时触发要真正观察才可声称通过，本文件不预先保证。安装后交互hook需另开宿主会话验证，不能用任务或CLI代替。

## 8. 暂停回退与前向恢复

发现正式结果异常时先禁用新任务，确认新执行已自然结束；不让回退与后台并行。不恢复旧 XML为可运行writer，不覆盖当前 threads/sources/页面，不删除原资料。

```powershell
Disable-ScheduledTask -TaskName $TaskName
& $Python -B $CLI sweep --config $Config rollback --out $Out --dry-run --json
& $Python -B $CLI sweep --config $Config rollback --out $Out --json
& $Python -B $CLI sweep --config $Config status --json
```

rollback最后读入当前勾选后设paused，保留新决定和资料。核实际状态后，解决问题并从当前数据前向恢复：

```powershell
& $Python -B $CLI sweep --config $Config switch --out $Out --dry-run --json
& $Python -B $CLI sweep --config $Config switch --out $Out --json
& $Python -B $CLI sweep --config $Config run --out $Out --no-judge --json
Enable-ScheduledTask -TaskName $TaskName
```

恢复不需要再导入旧快照；只有明确对账需要时才显式传旧state，当前冲突数据仍优先。权限拒绝、未知锁或无法确认的在途不是自动重试理由，应在当前步骤查明后继续。
