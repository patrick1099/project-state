# project-state

先读 docs/HANDOFF.md（如有）的有效约定及当前批次，再读 docs/BLUEPRINT.md 对应行为；改动前按主题查 docs/CHANGELOG.md 的取舍。没有 HANDOFF 表示没有进行中的施工；已完成合并的证据入口为 docs/evidence/merge-b2/final-closure.md。

## 命令

- 本机 Python：`C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe`；不使用 py/python/python3。
- `scripts/pstate.py thread --ai-help` 管事项与交互发现；`scripts/pstate.py sweep --ai-help` 管后台、联合页与显式迁移。PowerShell 可用 pstate shim，Git Bash 用 pstate.cmd。
- 测试：固定解释器加 `-B -m unittest discover -s tests -v`。验证使用临时 PSTATE_HOME/out、合成日志或显式 files，不写正式库、不调真实模型。
- 正式集成切换命令见 docs/evidence/merge-b1/b2-commands.md；验收范围见 validation.md。发布不自动导入、启用或替换计划任务。

## 约定

- 唯一事项生命周期在 threads.json schema1；thread 只管立项之前，决定做后 transfer 给 Obsidian 任务目录。施工进度仍在 HANDOFF，全局事项只保留来源、决定和有时间的历史停点。
- 任务认领在 PSTATE_HOME/claims/task-claims.json（全局，认 Obsidian 任务目录）；文件锁在各仓 locks.json。两把短锁不嵌套持有，只读显示不加锁不写回，认领表坏了报错不覆盖。旧看板 new 一律拒绝，旧条目只读到 done 收尾。
- 任务↔会话关联唯一存在任务目录 `会话.md` 受管区（scripts/pstate_sessions.py，`link` 命令在 pstate_sessions_cli.py）；claim/take/transfer/link 共用同一登记函数，短锁在 PSTATE_HOME/session-links，不与认领锁、事项锁嵌套；不依赖 hub、不做自动推断。
- 入口 scripts/pstate.py；事项、历史、内部后台分别位于 scripts/pstate_threads、scripts/pstate_history、scripts/pstate_sweep。历史平台与后台能力共用 pstate_history.PLATFORMS，新增平台只改适配器和注册项。
- sweep.toml 是后台策略唯一配置。Codex 只提供交互发现，未承诺后台自答。SessionStart 只提示计数，不扫描、不调模型。
- sweep/sources.json 是永久来源资料，不能按缓存清空；review_version 只认正文，activity_version 包含工具活动。旧摘要、AI 判断、勾选均不能充当用户决定或 seen。
- 多事项录入用 sweep review 冻结输入，逐项保存并独立回读，完整成功最后 seen；失败先查回执与实际库，再原输入重放，不重新起名或盲加 distinct。
- 输出归属闸设在实际写/删函数内，预演走同路径。模型调用在事项短锁外；输出物理路径互斥覆盖整轮。不得绕过归属或拿旧快照覆盖切换后的决定。
- session-sweep 旧仓入口全部拒绝执行；唯一新 runner 在稳定活源根。install-task 拒绝 worktree/插件版本缓存，run_hidden.pyw 必须保持明文、固定绝对解释器并传回退出码。
- Claude 钩子在 hooks/hooks.json；Codex 在 hooks/codex.json（仅开始，施工锁未适配）。钩子异常静默放行；shell/python 写文件仍不在旧原生编辑锁覆盖内，不能借此绕已有拦截。
- PATH shim 指向金库活源，插件钩子指向缓存。发布时两份 manifest 同步升级、推子仓并 hub sync --refresh；不能把 CLI 回读当安装后交互钩子验收。

## 易错点

- .py 受 Esafenet 保护，读搜改用 esafe-code；.pyw 保持明文。复杂输入用 UTF-8 --input-file 或 stdin，程序调用用参数数组。
- 不手改正式 threads/sources；损坏、未知 schema、备份恢复都需明确处理。rev 冲突先重读；replaced_unverified 先核落库，不能盲重试。
- discover 的 limit/max_chars 只限制返回页，roots 每次读全部所选 JSONL；精确抽样用 files。旧时间戳勾选只映射首次来源版本，不能让同时间戳的新工具活动继承旧勾。
- 个人仓先开 worktree 再改；不加 AI 署名。普通文档任务只改获派 MD，保留 HANDOFF 的已谈定和机器区。
