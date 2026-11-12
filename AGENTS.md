# project-state

先读 docs/HANDOFF.md（如有）的有效约定及当前批次，再读 docs/BLUEPRINT.md 对应行为；改动前按主题查 docs/CHANGELOG.md 的取舍。没有 HANDOFF 表示没有进行中的施工；hooks 实现及部署证据见 docs/evidence/hooks-0.9.0.md，早期合并记录见 docs/evidence/merge-b2/final-closure.md。

## 命令

- 本机 Python：`<Python绝对路径>`；不使用 py/python/python3。
- `scripts/pstate.py thread --ai-help` 管事项与交互发现；`scripts/pstate.py sweep --ai-help` 管后台、联合页与显式迁移。PowerShell 可用 pstate shim，Git Bash 用 pstate.cmd。
- 测试：固定解释器加 `-B -m unittest discover -s tests -v`。验证使用临时 PSTATE_HOME/out、合成日志或显式 files，不写正式库、不调真实模型。
- 正式集成切换命令见 docs/evidence/merge-b1/b2-commands.md；验收范围见 validation.md。发布不自动导入、启用或替换计划任务。

## 约定

- 事项正文权威在 `output_root/事项卡片/<可读标题>-<完整id>.md`；先读 show/continue 的 `card.body` 与 `card.sha256`，旧结构字段只是历史快照。正文自由手补，保留顶部受管元数据；修改用精确 edit-card 或可安全匹配的 set，冲突重读，不整卡覆盖。绑定卡缺失不能从 JSON 重建。
- 唯一事项生命周期在 threads.json schema1；thread 只管立项之前，决定做后 transfer 给 Obsidian 任务目录。施工进度仍在 HANDOFF，全局事项只保留来源、决定和有时间的历史停点。只有 continue 自动把当前会话登记为卡片来源，set 不登记（scripts/pstate_threads/pickup.py；按会话号直接定位日志，不走 discover）。
- 任务认领在 PSTATE_HOME/claims/task-claims.json（全局，认 Obsidian 任务目录）；文件锁在各仓 locks.json。两把短锁不嵌套持有，只读显示不加锁不写回，认领表坏了报错不覆盖。旧看板 new 一律拒绝，旧条目只读到 done 收尾。
- 任务↔会话关联唯一存在任务目录 `会话.md` 受管区（scripts/pstate_sessions.py，`link` 命令在 pstate_sessions_cli.py）；claim/take/transfer/link 共用同一登记函数，短锁在 PSTATE_HOME/session-links，不与认领锁、事项锁嵌套；不依赖 hub、不做自动推断。
- 入口 scripts/pstate.py；事项、历史、内部后台分别位于 scripts/pstate_threads、scripts/pstate_history、scripts/pstate_sweep。历史平台与后台能力共用 pstate_history.PLATFORMS，新增平台只改适配器和注册项。
- sweep.toml 是后台策略唯一配置。Codex 只提供交互发现，未承诺后台自答。SessionStart 只提示计数，不扫描、不调模型；UserPromptSubmit 只读事项库、提醒本会话是来源的卡，不解析日志。
- sweep/sources.json 是永久来源资料，不能按缓存清空；review_version 只认正文，activity_version 包含工具活动。旧摘要、AI 判断、勾选均不能充当用户决定或 seen。
- 多事项录入用 sweep review 冻结输入，逐项保存并独立回读，完整成功最后 seen；失败先查回执与实际库，再原输入重放，不重新起名或盲加 distinct。
- 输出归属闸设在实际写/删函数内，预演走同路径。模型调用在事项短锁外；输出物理路径互斥覆盖整轮。不得绕过归属或拿旧快照覆盖切换后的决定。
- session-sweep 旧仓入口全部拒绝执行；唯一新 runner 在稳定活源根。install-task 拒绝 worktree/插件版本缓存，run_hidden.pyw 必须保持明文、固定绝对解释器并传回退出码。
- Claude 钩子在 hooks/hooks.json；Codex 在 hooks/codex.json。共享裁决在 pstate_hooks.py，输入分析在 pstate_tools.py；Codex 原生 exec_command 可能归一为 Bash，清单必须同时匹配。写前异常闭锁，诊断在 PSTATE_HOME/hook-diagnostics；其他事件报错但不拦工具。shell 写入集合不明且相关个人仓有他人活锁时拒绝，改用 pstate run --write 声明全部目标；不按全局别仓的锁阻断。
- PATH shim 指向金库活源，插件钩子指向缓存。发布时两份 manifest 同步升级、推子仓并 hub sync --refresh；不能把 CLI 回读当安装后交互钩子验收。

## 易错点

- .py 受 Esafenet 保护，读搜改用 esafe-code；.pyw 保持明文。复杂输入用 UTF-8 --input-file 或 stdin，程序调用用参数数组。
- `thread sync-cards --dry-run --json` 预演回填，`edit-card ID --rev N --input-file edits.json` 使用 `expected_sha256` 与 exact edits；set 可在 JSON 带同名 hash，无专用 CLI flag。未配置输出、卡片缺失或部分失败必须分别核对库与卡，不能报已完整记下。
- 0.10.0 在 schema1 事项中增加 card_ref；0.9.8 严格校验会拒读新库。先更新全部宿主缓存、CLI 活源与后台入口，再回填；恢复旧库及卡片需明确处理，不混用版本或静默重建。
- 不手改正式 threads/sources；损坏、未知 schema、备份恢复都需明确处理。rev 冲突先重读；replaced_unverified 先核落库，不能盲重试。
- discover 的 limit/max_chars 只限制返回页，roots 每次读全部所选 JSONL；精确抽样用 files。旧时间戳勾选只映射首次来源版本，不能让同时间戳的新工具活动继承旧勾。
- 个人仓先开 worktree 再改；不加 AI 署名。普通文档任务只改获派 MD，保留 HANDOFF 的已谈定和机器区。

- Codex SessionEnd 走 scripts/pstate_end.ps1（Windows）/pstate_end.py：只写原子回执，不等待全仓清理。pstate_lock 加载状态消费回执；恢复同线程先 H.end 清旧状态再删除回执，不能提前删回执令旧锁复活。同步钩子启动仍受宿主 3 秒限制，不能把 hook completed 代替实际回执/状态验证。
