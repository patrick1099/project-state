---
name: pstate
description: 全局待续事项回顾与个人工程会话锁。Use when the user asks 回忆以前聊过什么 / 哪些事还能继续 / 继续聊 / 记下来 / 先放着 / 不做了 / 现在有哪些活; proactively capture substantive personal ideas in the current conversation; also use for a pstate lock denial, claiming an Obsidian task directory (claim/take/release), or handing a decided matter over to an Obsidian task (thread transfer). Current AI reviews bounded history and captures matters; an explicitly enabled internal sweep retains durable source summaries and unified Obsidian reminders. No GUI or automatic matter import. Progress stays in HANDOFF; company matters are not automatically captured.
---

# pstate：待续事项与工程施工

先查真实命令，再操作事项或后台：

```text
pstate thread --ai-help
pstate thread list --all --json
pstate sweep --ai-help
pstate sweep status --json
```

用户要的是找回值得继续的事情，再决定是否推进。**全局事项 thread** 可以是一段已有实质内容的想法、讨论、决定或待办，关联 0..N 个工程路径，也能完全没有 Git 仓库。**thread 只管立项之前**：聊过什么、要不要做；决定做并落进 Obsidian 任务后就转交（transferred），之后由 Obsidian 任务承接。**施工认领**认的是 Obsidian 任务目录，**文件锁**按仓库管文件占用。不要把旧版“只记还没开始的待办”误套到全局事项。

| 位置 | 承担什么 |
|---|---|
| thread | 待决定、待承接的事项，以及来源和历史讨论 |
| Obsidian 任务目录（…/task/Tnn-名称） | 要解决什么、采用什么方案、验收要求和长期结论 |
| 项目 HANDOFF | 当前未完施工的现场、阻塞、验证情况和下一步 |
| Obsidian 项目 README | 一句概况和当前有效入口 |
| 03-悬着的事 | 后台自动生成的回顾入口，勾选只关闭提醒 |

施工进度、失败现场、下一条命令写项目 HANDOFF；thread 保存动机、有效结论、未定问题、带时间的历史停点、来源与 HANDOFF 引用，转交后不再回填施工状态。记忆只存长期结论。公司事项不自动收录，混合会话按具体内容判断，不能按启动 cwd 一刀切。

## 当前会话：实质想法当场接住

1. 聊出了值得再次找到的个人事项，就主动建为 open；不必等用户说“记个待办”。随口一句、只有 AI 建议且用户未响应的方案，不自动变成承诺。
2. 先 `thread list --all --json` 对照已有事项，按目标和结果判断归并；同名不等于同一件事，同一个消息也可能包含多件事。
3. 找到真实消息来源，按下方结构准备 JSON。用户结论用 `confirmed: [{said, source}]`，保留原话；未核验标记不得冒充用户已确认。未知问题放 questions；建议写 next_suggestion，工具会标记 AI。
4. `thread add --input-file item.json --json`；同一事项新材料用 `add --into ID --rev N`，输入只能含 sources；修改摘要用 `set ID --rev N`。同锚点确实是另一件事才用 --distinct。
5. 看回执与 `thread show ID --json`，核对已持久化的内容才说“已记下”。dry-run、写失败或替换后未核验，都不能报已保存。原话尚未落到本地日志时不能伪造锚点或确认状态。

add 创建 open，追加来源不改变已有状态；新来源绝不自动重开 parked/dropped/done。重复 add 可能返回已存在 ID，仍需 show 确认。

## 历史回顾：当前 AI 先读，用户只看卡片

1. 先读全局已有事项；确定本次时间/来源范围。discover 只提供本地正文和来源，不负责语义提取。
2. 用 `thread discover --input-file discover.json --json` 分页取正文，当前 AI 自己读、筛出有实质可接续内容的个人事项，去除纯寒暄、已解决的一问一答和重复讨论。需要继续阅读时沿 next_cursor 取下一页，不让用户逐页筛原文。
3. 一个会话可拆多事项；跨会话同一事项归并来源。先查已有卡片；历史卡可记录带时间和来源的当时停点，不必为每张回顾卡审计工程。声称当前现状或真正恢复执行前，才核当前 Git、实际成果和 HANDOFF。show 只给 HANDOFF 是否存在及修改时间，不会替你核其正文。
4. 使用 add/set 形成可读卡片，展示：为什么聊、已定什么、还缺什么、截至何时停在哪、可继续的下一步及其 AI 身份。引用原话、历史摘要、当前验证事实分别说清。
5. 用户只需对卡片选“继续聊 / 决定做 / 先放着 / 不做了”；已无后续的内容可不建卡，但要在完整审阅该会话版本后才显式 seen。长会话跨页时必须读完相关全体正文，不能因为当前页看过就提前 seen。
6. seen 输入为 platform/session/version，三项取 discover 的实际返回。扫描不等于已审；分页中更新 seen 可以继续原游标，历史内容版本变化则游标失效，重新 discover。不可声称找回了全部历史。

普通 roots 模式每次读取选中目录的全部 JSONL，limit/max_chars 仅限制返回页，没有扫描缓存。只想精确抽样用 files。现代/旧格式的人类来源可信度不同，legacy/unknown 必须保留不确定；headless、agent、系统、工具、推理及可识别的压缩副本会过滤，但未知样式不能凭空恢复。本地缺失或删除记录不可找回。当前环境的 CODEX_THREAD_ID / CLAUDE_CODE_SESSION_ID 自动排除，另可传 exclude_sessions。

旧 session-sweep 独立入口已退役，使用 pstate sweep。导入摘要是永久来源资料；旧 checked/closed 只关闭该版本提醒，不能当作事项 done 或整会话 seen。后台 AI 判完成/放弃仍默认隐藏，完整内容可由 sources 找回；这个筛选不写事项状态或 seen。

## 多事项审阅：冻结计划后保存

历史会话可能拆成多个事项。读完本次声明的全部正文后，把审阅固定为 UTF-8 JSON，调用 `pstate sweep review --input-file review.json --dry-run --json`，检查预演后去掉 --dry-run 实际保存。

计划只有 platform、session、review_version、files、items、complete 六项。前三项取真实 discover；files 为平台注册名到原生日志路径数组的映射，必须含当前平台。items 每项为 `{"payload":<原 thread add JSON>}`，追加已有事项可附 into/rev，真正另一个事项才显式 distinct=true。每项必须包含该会话真实 source。files 只限定读范围，不扩大用户原话确认的信任。

写前校验全部输入及原文版本，逐项保存并独立回读；只有 complete=true 且全部成功才最后 seen。完整读完且无事项时允许空 items。未读完填 complete=false；部分关联仍显示“已关联N项，余部未审”。失败先检查回执 saved/pending 及 thread show，保留原计划、原 payload/into/rev/distinct 重放，不能重新起名、重拆或盲加 distinct。来源版本变化先重新 discover；未核实落库不能盲重试。

## 内部后台与联合页

`pstate sweep` 提供 run/status/sources/review/import-legacy/reconcile/switch/rollback/install-task/uninstall-task。先读 `pstate sweep --ai-help`；正式切换流程见 [b2 命令清单](../../docs/evidence/merge-b1/b2-commands.md)。安装或发布不会自动导入、启用 writer。

- 来源资料位于 PSTATE_HOME/sweep/sources.json，永久保存摘要、判断、错误、关闭与导入旧页；原文消失标不可用，保留内容。sources 包含默认隐藏的旧完成/放弃、公司与失败来源，输出可能很大，按需本地对账。
- review_version 是正文 hash，activity_version 含工具活动；seen 只认正文版本。旧时间戳关闭映射首次固定，新工具活动即使同时间戳也不能继承旧勾。
- 联合页显示完整事项与待审来源。勾选只关该版本整条提醒（含未识别余部），不写状态/seen；新来源不重开 parked/dropped/done。写页前最后吸收全部页勾选，保留周归档与取消恢复语义。
- 后台仅显式 switch 后运行，策略只改 sweep.toml。模型 safe-mode、预算、超时与失败上限沿配置；Codex 只有交互发现，不承诺后台自答。SessionStart 仍只轻提示。
- run --no-judge 不调用模型；dry-run 共用归属和写入闸，不写业务资料或页面。不同状态根对同一物理输出也互斥，不绕锁或归属拒绝。
- import-legacy/reconcile 读取显式旧 state/out；switch 显式启用，未知旧 run.lock 直接拒绝，不能按年龄删。rollback 吸收当前勾选后暂停，保留新数据，不恢复旧快照/旧 writer；用 switch 前向恢复。
- 任务只从稳定活源注册，worktree/版本缓存即使 dry-run 也拒绝。明文 pyw 固定绝对 Python，无窗口并保留退出码；last_launch.log 是诊断，不是成功验收。真实计划任务触发和安装后交互 hook 需另验。

## 四个选择与完成

| 用户意思 | 命令 | 实际效果 |
|---|---|---|
| 继续聊 | thread choose ID continue --json | 只恢复卡片供接着聊，状态和 rev 不变；不传 rev/input-file/dry-run |
| 决定做 | thread choose ID do --rev N --input-file choice.json --json | 记为 decided，仅记录决定，不创建执行任务、不改项目 |
| 先放着 | thread choose ID later --rev N --input-file choice.json --json | 记为 parked，默认列表隐藏，可用 list --state parked 或 list --all 找回 |
| 不做了 | thread choose ID drop --rev N --input-file choice.json --json | 记为 dropped，保留决定及来源 |
| 已完成 | thread done ID --rev N --input-file done.json --json | 必须有非空 evidence 列表；可由 assistant 依据实际证据记录，不能造用户确认 |
| 交给 Obsidian 任务 | thread transfer ID --rev N --input-file transfer.json --json | decided → transferred（已转交），记下任务目录；要求让它进入 decided 的那次 do 原话已核验 |
| 重新打开 | thread reopen ID --rev N --input-file choice.json --json | 将 parked/dropped/done/transferred 重新设为 open（清掉转交目标，历史留在 log），须明确用户原话 |

do/later/drop/reopen 需要 choice.json 的 said/source。said 必须逐字出现在用户 source.excerpt 中。工具核验消息出处，不替 AI 判断这句话是否授权该选择；核验失败仍可能记录选择且 confirmed=false，必须如实说明。不要从讨论热度推断“决定做”。dropped/done/transferred 再选 do/later/drop 前必须先 reopen；transferred 也不能直接 done，它的完成由 Obsidian 任务承接。

## 决定做之后：转交给 Obsidian 任务

1. 用户说做 → `choose do`（同一轮可以接着做下面几步，不用用户再确认一次）。do 回执 confirmed=false 时不能转交，先如实告诉用户原话没核验上。
2. 找承接的 Obsidian 任务：先按库根 AGENTS.md 查能复用的现有项目和 `task/Tnn-名称/`；属于现有项目就在它下面复用或新建任务目录。**没有合适项目就问用户是否立项，AI 不建项目。**
3. 改这个任务目录里的文档之前先 `pstate claim <任务目录绝对路径>`；新建的目录在写正文前认领。只做交接、暂不开工的，写完可以 release。
4. 把 thread 的动机、有效结论、未定问题写进该任务的主文档（按库规则复用同用途文档），写完回读。
5. `thread transfer ID --rev N`，transfer.json 为 `{"to": "<任务目录绝对路径>", "evidence": ["写入 <实际文档路径> 并回读：目标/结论/未决问题"]}`。CLI 只核目录存在且形如 `…/task/Tnn-名称`，内容是否真的交接靠 evidence 如实写。
6. 之后同一件事的新施工讨论导向那个任务，不另建同义 thread。transfer 不写 seen，也不关同一会话里其他待审来源。

完成 evidence 应可追查，例如实际提交、交付物及验证范围；“测试通过”不等于已安装或实际用户验收。done.json 仅有 evidence 时 actor=assistant；若写 said/source，则同样必须真实核验。continue 不会撤销关闭，只有明确决定才走 reopen。

## 调用和 JSON

PowerShell 用 `pstate`，Git Bash 用 `pstate.cmd`。无 shim 时直接调用脚本；本机 Codex 的解释器为 `C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe`。先查 `pstate thread --ai-help`。复杂输入用 UTF-8 JSON 文件或 `--input-file -` 的 stdin；自动调用用参数数组，不能把原话插入命令字符串。

默认历史输入示例（时间与预算按本次范围修改）：

```json
{"since":"2026-10-01T00:00:00Z","limit":4,"max_chars":16000,"exclude_sessions":[]}
```

可指定 `roots: {"claude":["C:/path/to/history"],"codex":["C:/path/to/sessions"]}`。精确抽样改为 `files: {"claude":["C:/path/one.jsonl"],"codex":["C:/path/two.jsonl"]}`；files 模式只读指定文件。下一页保持选择范围并带实际 cursor；sweep_state 是可选旧 state 文件路径。

下面是 add 的**字段示例，不是真实来源**；运行前必须整体替换 sources 中的会话、原生锚点、时间、摘录、路径与哈希，不能将样例导入正式库：

```json
{
  "title": "为小工具讨论退出清理行为",
  "motivation": "需要下次继续辨明退出时应清理的资源",
  "confirmed": [],
  "questions": ["哪些资源由本工具创建并负责释放？"],
  "stopped_at": "截至 2026-10-22T10:00:00+08:00，仍在讨论需求",
  "next_suggestion": "核对资源归属后再确定清理范围",
  "paths": [],
  "handoff": [],
  "sources": [{
    "platform": "claude",
    "session": "replace-with-native-session-id",
    "anchor": "replace-with-native-message-anchor",
    "time": "2026-10-22T10:00:00+08:00",
    "role": "user",
    "excerpt": "先记下来",
    "excerpt_hash": "0b0806ce9a388b9629406d7e61875b36a774ed6bc2fa4d9e6d2f6c50067f00de",
    "path": "C:/replace-with-native-history-path.jsonl"
  }]
}
```

source 必需 platform/session/anchor/time/role/excerpt/excerpt_hash，path 在存储层可选，但原话核验需要原始日志路径。excerpt_hash 是摘录 UTF-8 的 SHA-256，优先直接复用 discover 返回的 source；不要把 assistant 摘要当 user 原话。sources 为数组；choice.json 的 source 是单个对象，例如 `{"said":"<用户逐字原话>","source":<对应完整来源对象>}`（此处为结构示意）。confirmed 每项也只有 said/source；工具回读会增加 verified/verification_note，不把这些输出字段原样写回输入。

```text
pstate thread add --input-file item.json --dry-run --json
pstate thread add --input-file item.json --json
pstate thread show <回执中的ID> --json
pstate thread list --all --json
pstate thread seen --input-file seen.json --json
```

seen.json 结构：`{"platform":"codex","session":"<返回的会话ID>","version":"<返回的内容版本>"}`。done.json 结构：`{"evidence":["<真实完成证据与验证范围>"]}`。set 只接收可编辑字段，不可改 state；所有变更命令支持同写入路径 dry-run，预演只可能创建协调锁，不写业务库。

thread 的 JSON 信封为 ok/data/error/meta；成功在 stdout，失败在 stderr，退出码 0/1/2 分别为成功/运行失败或冲突/输入错误。帮助始终是文档。数据在 PSTATE_HOME（默认本机 LOCALAPPDATA/ai-project-state）的 threads/threads.json；有备份和版本检查。rev 冲突先 show 再重做判断；replaced_unverified 先核落库内容，不盲目重试。

## 施工认领与文件锁

- **任务认领（全局，与 git 仓无关）**：`claim <Obsidian 任务目录绝对路径>` 认的是“这件事谁在干”，防同一件事被两个会话各做一遍。存在 PSTATE_HOME/claims/，从哪个目录启动都一样。被拒说明别的活会话在干，停下问用户；用户说接手才 `take <任务目录>`，只转这一项认领。
- **任务会话**：claim/take 成功后自动写任务目录 `会话.md`；release 和会话结束不删关联。显式补记用 `link <任务目录> --platform claude|codex --session ID [--agent ID] [--reason 标签]`，无需认领或活会话。省略身份仅在 CLAUDE_CODE_SESSION_ID / CODEX_THREAD_ID 恰有一个时取默认；两者都在须显式指定。
- 派 Codex 等子 agent 到任务干活后，主会话用 `link <任务目录> --platform codex --session <派工回执里的 session id>` 登记。转交写全部 sources 和执行会话；关联失败可对同一目标、当前 rev 重跑 transfer 补记，无需 reopen。
- **文件锁（按仓库）**：个人工程按蓝图识别，`lock on|off|auto` 可设置。原生编辑前按仓库相对路径占用，跨 worktree 同路径也互斥；进程结束、会话结束或满足合并清理条件时释放。同进程/祖先会话放行，兄弟 headless 会话仍互斥。文件锁不需要先 claim。
- 遇到“[pstate 锁]”必须停下，向用户说明占用者、worktree、它认领的任务及时间，询问等候还是接手；用户明确接手才照提示跑 `--cwd <被写文件所在仓库> take <会话号>` 后重试，只转该仓文件占用，不用 shell/python 绕过。
- `release` 放掉本会话全部任务认领，以及 `--cwd` 所在仓库的文件占用（不在仓库里时会说明文件占用没处理）。squash/cherry-pick 合并或放弃施工时跑。闲置多久都不是自动接手授权。
- **旧看板已停用**：`new` 一律拒绝（含 --force），要跟踪的活去 Obsidian 建任务。`status` 仍列出待收尾的旧条目，`done <旧短名>` 只是把旧条目移出活跃视图，和 thread done 是两个命令；没做完的旧条目要先把独有信息迁到承接处，再 done 并说明“已迁出”，不能宣称业务完成。
- claim/take/release 只支持有 Claude 会话身份的环境；Codex 认领与锁未实现。shell/python 写入仍是原生编辑锁盲区。
- 会话开始只在有别的活会话或别的会话的任务认领时注入现场，另附全局 open/decided 计数；不注入旧条目、不扫历史。脚本/CLI、纯翻译和历史样本验证不等于安装后宿主钩子已验证。
