# merge-b1 capture / compatibility 实施证据

日期：2026-10-23。P：`C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge`（基线4921f78）；S：`C:/Users/huawei/Desktop/MyProjects/my-repos/session-sweep-pstate-merge`（基线d28f7e7）。

本段仅两隔离工作树的代码、测试及本文。此前整组写入因授权证据不足被自动审批拒绝，当时未执行；用户直接确认“确认，按这个范围完成实现、发布和切换”后，读回HANDOFF新增约定，同一具体esafe-code写入附授权重审通过。b1边界不变：没有提交、推送、合main、发布、读取凭据、注册/卸载真实任务、真实模型、全量聊天扫描、正式状态或Obsidian页写入；没有修改HANDOFF、BLUEPRINT、AGENTS或skill文档。

## 冻结审阅接口

`pstate sweep review --input-file PATH|- [--dry-run] --json|--format json`。输入严格为：

- platform、session（原生身份）、review_version；
- files：注册平台名到显式原生JSONL路径列表的映射，只读取指定文件；
- items：`{payload: 原thread add JSON, into?: ID, rev?: N, distinct?: boolean}`列表。into要求明确rev；distinct默认false，绝不自动补。
- complete：显式boolean；允许items为空的完整审阅，避免为了seen而编造事项。

实现位于内部`pstate_sweep.review`，没有模型参与。整份输入先深拷贝冻结、计算canonical SHA-256，再完整核验结构、真实原生日志的review_version、来源路径/角色/锚点/时间/原话与excerpt_hash，并用原core.evolve对短锁快照shadow预验全部add/into。旧摘要、recap、后台AI判断不能冒原消息来源或用户done。

真实执行逐项调用原thread store.mutate，再独立store.read，核对首次capture_identity及请求来源确已保存；shadow临时UUID从不当作真实落库证据。add保持原capture_identity重放，因此原事项改名后同输入仍回到原ID；into已追加来源时，即使原rev过期也按原语义幂等回放，不改已有状态。

只有complete=true、全部项独立回读成功，并再次核对原文review_version及全部事项后，才最后调用原seen写路径并回读。complete=false不写seen；关联从item.sources现场派生，不建指针副本、事务日志或覆盖账本。线程库仍threads.json schema1，不改变旧CLI语义。仅store.mutate新增内部preview_state可选参数，只允许dry_run，串行预演仍走相同evolve/validate及写入闸，支持连续into的rev推进；预演不声称已持久化/真实回读。

失败保留已保存事项，错误details包含input_hash、独立核实的saved、待核实pending ID/序号、phase及原commit_status等cause。明确要求先核查落库结果，再用相同冻结JSON/hash重试；不重起名、不重新拆分、不盲带distinct。

confirmed直接复用原`pstate_threads.cli.verify`的原话和默认原生历史信任边界。review.files仅扩读取范围，不自动扩大用户确认信任；同一有效外部fixture可存真实来源但verified=false，默认原生根内真实human原话才可能verified=true。review不记录do/later/drop/done等生命周期决定。

## 当前S写入退役

采用明确拒绝，统一消息来自`session_sweep.retired.reject`，无环境绕过开关：

- 原sweep.py仍调用原module CLI；run、install-task、uninstall-task均在读取配置或创建模型前拒绝，dry-run安装亦拒绝。
- module层run_sweep、write_pages入口先拒绝，防调用者跳过顶层shim继续写。
- store.save_state、write_atomic、write_page_if_changed、remove_generated及RunLock进入/退出先拒绝。因此相同内容的写页、底层删归档、旧状态写、超过两小时锁接管和直接退出删锁都不能写。
- 旧schtask.install_script/uninstall_script/run_powershell也拒绝，手动调用不能重装旧writer。
- S的明文run_hidden.pyw直接exit1且输出退役诊断，不起旧Python子进程、不创建旧日志目录。status/help与旧状态读取保留。

这是当前两个worktree中的源码闭环；不能限制任意另存的旧代码，实际旧安装路径和正式调度更换属于b2。迁移PID占位仍只用于切换临界区，不再被误称永久防旧写闸。

原S的旧运行回归已由backend任务迁P。本段将S不再适用的RunSweepTest替换为7项有意义拒绝测试，调度builder测试改为拒绝断言；保留core、judge构造/解析、docgate及config纯逻辑回归，没有skip失败。

## 稳定隐藏入口和计划任务

P根`run_hidden.pyw`保持明文及.pyw后缀，以固定绝对路径
`C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe`
和CREATE_NO_WINDOW=0x08000000运行`scripts/pstate.py sweep`，保持子进程退出码并传递输出。无参数默认run，但仍经过默认禁用/归属闸，不暗启用。

pyw仅附内部隐藏启动标记，home仍由pstate唯一入口解析。子CLI通过现有原子写适配器将stdout/stderr/exit保存至`PSTATE_HOME/sweep/last_launch.log`，因此pythonw无控制台时不会丢诊断。日志是独立的best-effort诊断；失败不覆盖来源/threads/page，不改变原子命令退出码。普通CLI不因这项功能额外写日志。

`pstate sweep install-task|uninstall-task [--dry-run]`沿唯一sweep.toml取task_name/interval_hours，不复制模型/预算/调度默认。install正式和dry-run共同校验：拒绝.git指向worktrees的检出及plugins/cache路径（含解析后的路径），要求稳定根run_hidden.pyw与scripts/pstate.py存在，pythonw也固定绝对解释器。当前真实worktree安装预演会明确拒绝；临时稳定目录只生成脚本验证，未实际注册。PowerShell失败返回E_EXTERNAL_TOOL并保留原退出码/输出；缺工具也按外部失败处理。注册本身不启用writer。

## 验证

均使用临时PSTATE_HOME、临时输出及合成JSONL，无真实模型/任务：

- P：`Python314/python.exe -B -m unittest discover -s tests -p test_sweep*.py -q`，最终112/112通过（原97，加review10与runner5）。
- P：原`test_threads.py`20/20通过，覆盖旧事项CLI、幂等、rev/备份/原话确认、并发及dry-run。
- S：`Python314/python.exe -B -m unittest discover -s tests -q`，最终47/47通过（没有skip）。
- review专项：第二项保存失败后保留第一项且不seen；改名后同冻结输入重放不重复；into旧rev回放；独立回读失败；完整输入预校验；complete=false与空items完整审阅；假摘要/角色/hash拒绝；确认信任不扩张及默认原生根正例；原文在写前/保存后变版均不提前seen；顺序into预演；真实CLI文件输入与json/format/error details。
- runner专项：实际加密CLI隔离运行；明文pyw与直接CLI的0/1/2退出和输出一致；真实pythonw无控制台执行0/1/2并读回临时last_launch.log；固定解释器/隐藏标记；worktree与版本缓存拒绝；真实TOML调度值替换后脚本跟随；卸载仅预演。
- 现成contract-test的7个通用检查函数经内存子命令适配全部通过：argparse JSON信封、普通错误、format等价、ai-help、eager help、主help提示和--终止语义。使用临时home；未修改该工具或其注册表。机器frontmatter名为pstate-sweep，真实调用名为pstate sweep，分别按对应契约检查。
- 15个本段源码/测试逐一esafe read及AST解析通过；13个.py保持encrypted，两个.pyw保持plain，全部CRLF。所有源码写入经esafe-code预演、同命令apply及明文SHA闸；新增.py均require-encrypted。两仓diff --check通过（P只有既有HANDOFF行尾转换提示）。
- 发现并修正的回归：原迁入调度测试曾用不存在的C:/r及任意pythonw，新增验证正确拒绝；现使用临时稳定目录与固定解释器，保留原脚本内容断言，没有绕过校验。

上述为实现者专项结果。全仓独立验收、结构/对抗复核及b2正式发布迁移切换仍由后续角色完成。

## 最终指纹

| 仓 | 文件 | SHA-256 |
|---|---|---|
| P | scripts/pstate_threads/store.py | 86bbf4964474043ce1bd3997291115a57c25c8cdc4b6be6280c2295914660333 |
| P | scripts/pstate_sweep/cli.py | 5205f47e53df86ce190d750b6e28b5c542f3d73db539ef0582e20689fa14b12a |
| P | scripts/pstate_sweep/review.py | 83977259c5fef7447e6d661e5e1e7a7f5bee0ea8be231932fd0c53254dca4484 |
| P | scripts/pstate_sweep/adapters/store.py | 2c9936af72f46f6626c8c72c9d852692d5e252384d01d15453c855bc4816b6ec |
| P | scripts/pstate_sweep/adapters/schtask.py | 7530ef8d9b23e1e5cf822d9a8eb11a70512e5f12bd1ef31c77869eef6b0822b4 |
| P | run_hidden.pyw | 57c8523ddb9059a2594a6c9019cf035af939534f1c3e81938cd5b209b0f80030 |
| P | tests/test_sweep_review.py | bb0ac5405fbb58e0a3df37d913f05a7375a0153facc5f820048b062f2411de07 |
| P | tests/test_sweep_runner.py | 603e3e636e6277a48214d1ddf967a1e5bd09faaa96df18c97f944457fd2b6fa8 |
| P | tests/test_sweep_run.py | 320dcdd789d556dda2b3311484d41e83a23fc7ffa75032d0106aaac51e137a71 |
| S | session_sweep/cli.py | 3cf626811e6a546526a21df2ba5c902fb1a19131ad51f3ec189e58371bb45059 |
| S | session_sweep/retired.py | 82ca0ee7905a4110cc0899b9336cd5786629df4f82713f4a4305d387a4ba8a1d |
| S | session_sweep/adapters/store.py | c85cb0d5adc1f3faba40ba9f57d1187ac60aac2b12cf561542d5aefb8a115924 |
| S | session_sweep/adapters/schtask.py | 686101124f8821563e6ca4741d95321a0d59c752a6c7ecc713b9a4bd74a4f311 |
| S | run_hidden.pyw | 1037b47c77c2486638ef455a06075e7beded4af2d0f41b919b7ab569e5747b6d |
| S | tests/test_run.py | d49c74a1ae55c79166c7ca8b382616c0f2f685db423ea45c3282c77e74b813a9 |
