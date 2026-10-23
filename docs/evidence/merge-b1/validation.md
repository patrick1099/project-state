# merge-b1 独立验证

当前结果：输入闸修复后独立验收通过，P 全仓 200/200、现成 CLI 契约 7/7。初次失败事实保留，详细复验与最终矩阵见文末。

日期：2026-10-23。验证者只新增本文件与 `tests/test_sweep_independent.py`，没有改产品实现、HANDOFF、AGENTS、BLUEPRINT 或机器批次区。P 基线 4921f78；S 基线 d28f7e7。读回 P HANDOFF 当前全部有效约定及两仓 AGENTS、四份实施证据后开展验收。以下结果来自本轮独立执行，不能将实现者的测试数字代作独立通过。

## 初次验收结论（修复前）

**初次验收未通过，以下保留原始失败记录。** 原有 P 191/191、S 47/47 全仓 unittest 均通过，无 skip；另外运行现成 contract-test 七个通用检查全部通过。独立新增 5 个测试方法中 2 个通过，3 个方法产生 19 个失败子场景，证明永久来源资料与必需配置的校验仍有缺口。产品修复由监工串行安排，本验证者没有修改实现。

所有执行使用固定绝对解释器 `C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe -B`。CLI 使用新子进程、临时 PSTATE_HOME/out 和合成 JSONL/显式 files；模型只使用 fake 或 mock，不调用真实后台模型，不扫描真实全量聊天，不读取凭据，不写正式资料、不执行真实计划任务、不提交发布或合 main。

## 初次阻断缺陷与最小复现（复验已消除）

1. **永久来源资料损坏时仍可改写，或静默成功。** 产品位置 `scripts/pstate_sweep/adapters/store.py:validate_state`、`runtime.py:run_sweep/refresh_sessions`。先在临时 home/out 正常 switch，然后将 sources.json 的某个 summary 分别置为 `{}`、已知数值字段 `last_ts="broken-time"` 或 `human_count="broken-count"`，以真实 CLI `run --no-judge --input-file files.json --out TEMP --json`（files 与 roots 均为空）执行。预期：加载即 E_IO/exit1，原 state 字节不变。实际：三个输入均改写了 sources.json；空 summary 场景 exit0，另两场景在改写后才报错。未知字段保全及合法旧 summary 可选字段兼容仍需保留，不能靠强制所有新字段必填来修复。
2. **必需模型/命令空白仍进入运行编排。** 产品位置 `scripts/pstate_sweep/config.py:load`。真实 TOML 加载路径中分别将 recap_model、resume_model、claude_cmd 改为空串或仅空格，共 6 例。预期：ConfigError/E_VALIDATION/exit2，业务编排和外部调用均不发生。实际：六例均进入 runtime.run_sweep；测试已 mock 此边界，未真正调用模型。
3. **运行策略接受非有限数值。** 同一位置。timeout_s、budget_usd_per_call、interval_hours、idle_days、window_days 分别设为 TOML nan/inf，共 10 例，均被接受。预期：配置加载即拒绝，而不是进入外部命令、计费参数、时间运算或任务脚本。

失败回归：`tests/test_sweep_independent.py`。复跑命令：

```text
C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe -B -m unittest discover -s tests -p test_sweep_independent.py -v
Ran 5 tests in 5.013s
FAILED (failures=19)
```

这里 19 是 subTest 失败数，**不是 19 个独立测试方法**。两个通过方法证明：实际 TOML 改模型、预算和 timeout 后，fake subprocess 最终 argv/timeout 跟随；向注册表增加 fake 成员并切换 background 能力，调用方 roots 自动跟随。

## 独立执行记录

| 检查 | 本轮命令/入口 | 结果 |
| --- | --- | --- |
| P 原全仓 | 固定 Python -B -m unittest discover -s tests -v（新增独立测试前） | 191 tests / 113.486s / OK |
| S 全仓 | 同解释器 -B -m unittest discover -s tests -v | 47 tests / 4.408s / OK |
| P 新增对抗测试 | 上述 test_sweep_independent.py | 5 方法：2 通过、3 方法共 19 子场景失败 |
| CLI 通用契约 | 直接 import 现成 contract_test 模块，七个原检查函数；entry 为真实 P/run_hidden.pyw，不替换 _run、不更改工具注册表 | 7/7 passed |
| Python 结构 | 经 esafe-code read 读取 pstate_sweep 全部 14 个模块后 AST 检查 | 均可解析；调用方条件中未发现 claude/codex/haiku/sonnet 名称分支 |
| 配置端到端 | 真 TOML load -> ClaudeJudge -> mock subprocess.run | model=independent-model、budget=0.37、timeout=17；safe-mode 保留 |
| 注册能力拔插 | fake-platform 注册项 background True -> False | roots 从 fake-root 自动变 [] |

七项契约为 JSON 参数错误信封、普通参数错误、format json 等价、AI help 完整性、eager help、-- 终止语义、help LLM 提示。均以真实明文 pyw 启动加密 CLI，临时 home 仅产生启动诊断，不写 sources/threads。AI help frontmatter 名使用 pstate-sweep；帮助行真实调用名使用 pstate sweep，没有为统一名字修改产品。

## 八组验收映射与证据边界（初次记录）

本节保留修复前结论，当前结果以文末复验矩阵为准。

1. **旧事项和身份不回归。** 本轮执行 `test_pstate` 22、`test_threads` 20、`test_history` 26、`test_history_events` 11；旧 tasks/locks、schema1、ID/rev/log/capture_identity、原话确认边界、旧摘要/勾选不成为用户状态/seen、平台+native ID、正文/工具双 hash、recap 工具后变旧、Codex 交互保留而 background=False 通过。独立阅读 review、history core、runtime 接点，没有新增第二事项生命周期。
2. **真实迁移 CLI 与完整资料保全。** `test_sweep_migration.test_cli_closed_loop_and_machine_contract` 在真实子进程临时库执行默认 run 拒绝、import 预演/实际、reconcile、switch 预演/实际、no-judge run 预演/实际、sources、rollback 预演/实际、暂停拒绝和 switch 前向恢复。原摘要、未知字段、verdict/error、closures/last_run、原时间、主清单和孤立旧周档完整副本、缺原文旧摘要均有断言；全量 sources 保存 AI 完成/放弃/公司隐藏资料。这里只验证合成样本，没有盘点正式 Obsidian 全量资料。
3. **部分关联、冻结录入、重放与 seen-last。** 独立执行 review 十项、union 部分关联及勾选测试；第二项失败/独立回读失败保留已存第一项，不写 seen；改名重放、不自动 distinct、into 旧 rev 幂等、写前/写后源变版、空 items+complete、顺序 into dry-run、原文/角色/hash 假冒、外部 files 不扩大确认信任通过。旧线程回归 `test_replay_distinct_into_preserves_closed_state` 与 union parked 场景覆盖闭合状态不复活。checkbox 仅关闭提醒，线程原字节/seen 不变；同 timestamp 工具内容变化不继承旧勾。
4. **回退及切换中断。** migration 直接断言 rollback 只 paused，保留切换后 parked 决定与页勾选；原 native 别名重导不重关撤勾。注入 owner 写前/page 写中中断后正常 run 均拒绝，显式 switch 可恢复。源码复核顺序为 sources/switching -> owner -> 页 -> enabled；页重建前最后 reconcile 全主/已处理/工具周档。跨周勾选往返、判断过程迟到勾选、只删生成空归档、非生成文件保全由 `test_sweep_run` 相应测试通过。
5. **互斥和写入归属。** 本轮真实跨进程测试通过：整轮模型期间另一 home 对同 out 被内核锁拒绝，thread 短锁允许另写；真实 Windows root junction 别名共用 owner/锁，archive junction 逃逸拒绝。无 lease 的实际/dry-run 写页和删归档、paused/错误 owner、错误 home、新 out、不可读页均拒绝。固定 .tmp 哨兵未覆盖，源码使用 mkstemp 独占随机路径。**损坏 source 资料的深层校验失败见缺陷1，不能将本组整体记通过。** 文件 symlink 权限和硬链接限制沿环境边界保留；本轮不将普通 .tmp 哨兵冒称已实测文件 symlink 攻击。
6. **退役和隐藏启动器。** S47 包含真实 sweep.py run、pyw、module run_sweep/write_pages、save_state/write_atomic/write_page_if_changed/remove_generated、旧 RunLock enter/exit 和调度 install/uninstall/run_powershell 的拒绝断言，不只顶层 shim。P runner 五项含真实 pythonw 隐藏 0/1/2 与 last_launch.log、固定 Python/CREATE_NO_WINDOW、worktree/cache 的 dry-run 拒绝、稳定临时根脚本与 TOML 调度替换通过。未真实注册任何任务；无法约束任意另存旧源码，正式安装路径退役属 b2。
7. **CLI/结构/配置。** 信封四键、stdout/stderr、0/1/2、eager help、预演同写路径均由上列真进程和直接低层测试覆盖。backend/runtime/platform loader 从唯一 PLATFORMS 派生；14 模块 AST 未见另写平台名单条件。fake 平台及能力拔插通过；实际模型/预算/timeout 传递通过；**必需配置空白/非有限值仍失败，见缺陷2/3。** 当前实际平台适配器仅 stdlib，无独立第三方加载依赖，未将“未来依赖加载故障隔离”虚称实测。
8. **加密检查。** 开始前执行指定 esafenet-baseline.ps1 check（无 -Fix）：P 56 文件、S 27 文件均无脱密。新增独立 .py 经 esafe-code create 先 dry-run、同命令 apply，require-encrypted，UTF-8/CRLF，sha256=`71b2d0f598f64ce0b5f922702b335cd0f92ba340e2787f5a699f3a31d8013b25`。新 .py 逐项 info 与两 pyw 明文检查及末次 baseline 结果见下补记。没有修加密态。

## 本轮限制

上述是隔离副本、合成数据、真实 CLI 与 fake 模型的验收。正式库迁移对账、稳定活源发布、真实计划任务替换/定时触发和真实平台钩子均不在 b1。初次验收要求的三个缺陷修复后定向复验及受影响全仓复跑已完成，结果见文末；本文件不替监工更新批次状态。

## 末次加密与差异检查补记

末次 baseline check：P 57 文件、S 27 文件，均 exit0/no file lost its encryption，未使用 -Fix。逐项 esafe info：P 新增 24 个 .py、S 新增 retired.py 均 encrypted；P run_hidden.pyw 为 plain。S 已跟踪的 run_hidden.pyw 另行 info 核验。两仓 git diff --check exit0；P 仅已有 HANDOFF 的 LF->CRLF 提示，无 whitespace error。本验证者未修改该文件。

## 输入闸修复后的独立复验（当前结论）

**独立验收通过，未发现新的阻断缺陷。** 本次在修复执行者 final 停工后独立读取两个产品模块、修复证据及新增测试，再实际运行全仓和契约检查。以下为验证者本轮结果，不引用实现者的通过数字。初次 19 个失败子场景及复现记录保留在上文，现已全部通过。

```text
C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe -B -m unittest discover -s tests -v
Ran 200 tests in 178.140s
OK
```

200 个测试方法全部通过，无 skip；其中包含原独立 5 方法、原 19 个失败 subTest，以及新增 InputGateTest 4 方法。原独立测试 SHA 仍为 `71b2d0f598f64ce0b5f922702b335cd0f92ba340e2787f5a699f3a31d8013b25`，没有修改预期来迁就产品。修复新增测试 SHA 为 `578134417bbefd9b7503e76cf0ae9d6163bc6a890fe48d7041b9ad0aa3b1c30c`。

### 修复有效性与兼容性

- 原三类损坏 summary 通过真实新进程 CLI 在业务写之前返回 E_IO/exit1；sources.json 原字节不变。另 15 种已知字段类型损坏在 save_state 实际和 dry-run 路径均拒绝，write_atomic 未调用。
- recap_model、resume_model、claude_cmd 的空串/全空格六例，在配置入口拒绝且 runtime.run_sweep 未调用；timeout/budget/interval/idle/window 的 nan/inf 十例、补充 -inf 五例均在真实 TOML load 拒绝。
- version=None、cwd/branch/entrypoint/last_ts 的合法旧空值、recap=None、可选新字段缺省、未知顶层和嵌套字段保存读回保持。无原文、无 review/activity hash 的最小合法旧摘要仍可 import，原 legacy state 整份保留，不创建 thread/seen。
- 原配置的 doc_gate 空串、idle_days/max_calls=0 继续合法；四个必须为正的策略字段 0 继续拒绝。没有新增默认值或 fallback。
- 独立对照旧 S summarize_rows 的实际生产形状：旧 nullable 字段与 version=None 的来源契约相符；新增检查不强制平台/hash 等新字段存在。两个产品模块只补输入闸，没有变更来源身份、事项生命周期、模型选择或页写路径。

### 最终验收矩阵

| 组 | 当前结果 | 证据 |
| --- | --- | --- |
| 1 旧 tasks/locks、threads、来源身份与双版本 | 通过 | 本轮 P200 重跑含原锁22、threads20、history26、events11 |
| 2 合成旧资料保全与真实迁移 CLI 闭环 | 通过 | 本轮 migration18；完整摘要/未知字段/隐藏资料/原路径资料与回退链路 |
| 3 冻结 review、逐项回读、重放与 seen-last | 通过 | 本轮 review10、union 与旧 threads；原独立测试保持原 SHA |
| 4 暂停回退、前向恢复、勾选/归档保全 | 通过 | 本轮 migration/run/union 回归及源码顺序复核 |
| 5 输出归属、跨进程/junction、坏资料写前拒绝 | 通过本批已约定边界 | 本轮锁/路径回归；损坏 summary 原字节保全及低层实际/dry-save拒绝；文件 symlink 限制不变 |
| 6 S 旧入口退役、P 隐藏 launcher | 通过 | P runner 本轮重跑；S 六个相关源码/测试 SHA 与首次验收一致，沿用已独立执行的 S47/47，不无理由重复跑 |
| 7 CLI 契约、平台注册拔插、配置传递/拒绝 | 通过 | 本轮现成 contract-test 7/7；独立5方法及输入闸4方法包含在 P200 |
| 8 加密态与差异 | 通过 | 本轮 baseline P60/S27 无脱密；产品/测试 encrypted，pyw plain；diff --check exit0 |

本轮 CLI 契约仍用现成七个检查函数和真实 run_hidden.pyw 新进程，未替换 _run、未改工具注册表；七项均 passed。修复后的 store/config及新增测试均经 esafe-code read/info 核验，CRLF/encrypted；产品 SHA 分别为：

- store.py：`36c266f46524a77a775a83a7792710f237feeaa6eb9ce1ece06d3e561c1dbdd8`
- config.py：`40cd50ef0f6e0a2d87e861ed41c5269e63d1b1cc1dc489422130b990c67f8959`

S 指纹复核覆盖 cli.py、retired.py、adapters/store.py、adapters/schtask.py、run_hidden.pyw、tests/test_run.py，均与 capture-compat 及首次独立验收一致；.py 为 encrypted，.pyw 为 plain。git diff --check 只有既有 HANDOFF 的 LF->CRLF 提示，没有 whitespace error。

验证者本次仅更新 validation.md，未改产品、测试、HANDOFF 或批次状态。独立验收通过不表示文档收尾、正式发布、正式资料迁移/任务切换或真实定时触发已完成；这些边界仍按 b1/b2 计划由监工处理。
