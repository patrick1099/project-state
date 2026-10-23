# merge-b2 独立正式结果验收

最终：独立验收通过。发布、正式迁移与切换、原配置下的一次真实计划任务自然完成均通过；未来自然登录/4h重复触发和安装后新宿主hook保留未观察边界。

本验证者只写本报告及 independent-audit.json，不改实现、正式 home、页面、配置、任务或 HANDOFF，不运行 sweep、不制造用户决定。已读 role-reading、公共决策边界和验证闸、HANDOFF 当前有效约定、b2-commands 与 b1 独立验收记录；不重审已定方案，不调外部模型，不重跑无新理由全仓测试。

## 本轮独立内容对账

- 对 snapshot-hashes.json 的 31 个备份文件逐一重新计算 SHA256，全部等于原记录：旧 home 4、新 home 20、原页面 7。不是仅比计数。
- 正式 sources.json 的 legacy_imports.state 与备份 state.json 整棵对象相等，逐核全部 168 个原记录的所有字段、嵌套 summary/verdict/error 和未知字段；原 top-level/closures/last_run 亦相等。新来源刷新可更新当前视图，原资料保留区不改。
- 原 7 页逐全文比较 legacy_pages 与 legacy_imports.pages 均相等，含主清单、已处理和 5 个原周档；7 个原相对路径仍存在。公开 independent-audit.json 仅保留页序号、原文字数、正文SHA及比较结论；路径与逐项明细保存在本地详细审计副本。新渲染页允许随窗口重排，原文并未变为单纯计数或链接。
- 原 15 条 closure 的原 key/at 对象和映射目标逐条相等。30 个现 closure 为 15 个旧 timestamp 与 15 个 activity hash 版本并存；原33个页marker中15个checked都映射closed，现渲染15个checked亦全部对应closed，不代表新增15次用户决定。
- 原 threads.json 整份字节与备份相等：schema1、两事项、ID/rev/log/原话/状态均不变，seen仍0；旧 state.json 整份原字节不变。
- 同原version的166条当前summary，last_ts全与原值一致；另2条为真实roots解析的新版本。8条旧error仍保留。原 owner/verdict 原字段初轮读回均一致；后续真实task判断允许添加新结果，原结果仍在保留区。17 legacy provenance及3 unknown未伪装为verified。
- activation enabled，owner的version/state/out/token与activation逐字段相同；正式输出目录为原Obsidian路径。

## 配置、发布与已有演练

- 原scan/owner/judge/schedule与稳定活源TOML逐结构相等：haiku/sonnet、timeout300s、每次预算上限2美元、max_calls20、max_failures3、4h均未改。原projects_dir迁入history.roots.claude，新配置与备份TOML完全等价，文件仅LF/CRLF不同。
- 以每命令仅本进程safe.directory核Git：P main及origin/main均9e45c08b695c5be77028b3e8a3ae142dc9ec0ce5；S为05c2809860bf512221741cc54cfcfd0f6278decf且无remote；Vault为9804cb8954c0fd617ffb480c7ebc915f4829abc7。身份及提交正文无AI署名。
- 读回真实hub回执exit0、5成功/0失败，以及release/cache-live/retired-live证据；两个0.4.0缓存实际新进程入口通过属发布实施证据，本轮没有再次调用模型或冒称新宿主hook已验证。
- 读回备份内13步真实CLI恢复演练原始结果：dry/apply、勾选、rollback/paused-run拒绝、forward、撤勾链路均满足预期；这是隔离演练，不冒称正式回退。首次正式nojudge dry/actual和稳定隐藏入口exit0、calls0；这些不能替代默认模型的真实任务结果。
- b1 P200/CLI7/S47、配置注入和变化轴拔插结果仅引用其历史独立证据。发布阶段仅去空白且AST不变的记录已读，不重复跑全仓。

## 真实计划任务最后一轮结果审与边界

真实任务Action为固定绝对pythonw与稳定活源run_hidden.pyw run，没有no-judge参数；huawei InteractiveToken、IgnoreNew、1h限时、登录和4h触发。16:14:40 本验证者以只读 Get-ScheduledTask/Get-ScheduledTaskInfo/Get-CimInstance 独立读回：Ready、Enabled=true、LastTaskResult=0，Action路径正确，当前runner进程0。原手动触发实例自然完成，运行中第一个定时点重入由IgnoreNew拒绝，没有并发writer；调度器LastRunTime记录为16:08:08，不能据此伪称自然4h周期和登录已经测试。

最终启动日志exit0，stdout为默认run的直接data，非dry-run、非--no-judge替代；calls20、failures0、discarded0、planned36，listed2/archived38/queued16。calls20正好命中原max_calls20，每次2美元是上限而非实测花费，不声称实际总费用。coverage files168、unreadable/missing_roots/malformed_lines0，仍明确17legacy和3unknown。当前sources.last_run与日志stdout整对象相等，保留的14-task-launch.log与当前日志整份字节相等。

任务后再次逐内容核：原168payload、原state整对象、7页legacy全文及原路径全部通过；threads两事项open/done与seen0整份原字节、旧state原字节不变；owner逐字段匹配enabled activation。post-switch备份下threads/sweep/pages每个文件均与当前目标逐字节一致，16个文件均经过逐字节核对；逐文件路径与SHA在本地 private-audit-evidence/independent-audit.json，公开同名JSON仅保留聚合结论、哈希和本地指针，不只确认目录存在。

未来自然登录/4h重复触发、安装后新宿主hook尚未观察。本轮通过范围是一轮真实任务引擎执行及正式结果，历史b1的隔离验证保持其证据层级。
## 公开证据与本地完整副本

公开JSON已经脱敏；原聊天、threads/sources payload、页面全文、完整任务XML、逐来源ID/关闭时间及原CLI输出不进入公开仓。完整审计材料保留于本地 `C:/Users/huawei/AppData/Local/pstate-merge-backups/20261023-154713/private-audit-evidence/`；公开 independent-audit.json 的 private_full_audit_path 与 private_full_audit_sha256 指向对应完整副本。本文的内容核验结论引用该实际独立审计，不表示公开聚合版含有原资料全文或全部逐项明细。
