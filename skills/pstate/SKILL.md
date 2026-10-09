---
name: pstate
description: 项目看板（pstate）——个人项目里几个 Claude Code 会话同时干活时的会话互斥锁 ＋ 待办条目。Use when a "[pstate 锁]" denial blocks a Write/Edit; when a SessionStart injection shows a "[pstate]" board; when the user asks 现在都有哪些活 / 别的会话在干什么 / 记个待办 / todo / 让这个会话接手 / 放掉锁; or before starting work on something already listed on the board (claim it first). Progress and handoff go to the project's HANDOFF.md, not here. 公司仓不加锁、不新建条目（遗留任务照旧做完）；进度不写进记忆。
---

# pstate：会话互斥锁 ＋ 待办看板

pstate 只管两件事：**互斥**（哪个会话正在干哪件事、占着哪些文件，别的会话别碰）和**待办**（还没开始的事）。进度、卡在哪、下一步写项目的 `HANDOFF.md`，不写进看板条目，也不写进记忆。

## 和别处怎么分

| 地方 | 管什么 |
|---|---|
| **pstate** | 个人项目：活会话与占用、看板条目（一行标题 + 状态 + 一句目标 + HANDOFF 路径） |
| 项目的 `HANDOFF.md` | 一件事干到一半的现场：已谈定、卡在哪、下一步。做完删掉 |
| 平台原生记忆、hub 金库 | 只存长期结论和教训，不记进度 |
| session-sweep | 只是发现入口，捡起来的事落回 HANDOFF / 看板 |
| 公司仓 | 不加锁、不新建条目（用户定的："公司东西我记得住"）。遗留任务照旧更新到做完再 `done` |

## 锁是怎么工作的（自动，不用手动加）

- 只在**个人项目**生效：worktree 根有 `docs/BLUEPRINT.md`，或目标文件同目录有 `*.BLUEPRINT.md`；`lock on|off|auto` 可按仓库强制开关（off 优先）。
- 每次 Write / Edit / MultiEdit / NotebookEdit 前，钩子把这个文件（按相对路径，**跨 worktree 也算同一个**）登记到本会话名下。别的**活会话**占着它就拦下。
- 占用挂在会话进程上：窗口关掉、会话结束（含 `/clear`）就自动释放；对方的改动提交并合并进 main/master、文件干净后也自动释放。
- 同一进程里的子代理、本会话起的 headless 子会话，算同一家，不互斥。

## 被拦下（"[pstate 锁]"）时怎么做

1. **停下**，把拦截理由里的占用者、worktree、任务、日志最后更新时间告诉用户，问他：**等对方做完，还是本会话接手**。
2. 用户说接手 → 跑 `take <会话号>`，再重试那次写入。用户说等 → 去做别的，或结束。
3. **不许绕过**：改用 Bash / python / 别的工具写同一个文件，等于没拦。
4. 提示"用户已让会话 X 接手你在这个仓库的活"时：停下告诉用户；这条只拦一次，用户让你继续别的事就重试。

## 命令

Git Bash 里用 `pstate.cmd`，PowerShell 里用 `pstate`；PATH 上没有时用注入里给的 `py -3 "...pstate.py"` 绝对路径调法。默认按当前目录识别仓库，`--cwd <目录>` 可指定。

| 场景 | 命令 |
|---|---|
| 看现场：各 worktree、活会话及其占用、看板条目 | `status` |
| 记一条将来要做 / 跨会话的事（个人项目） | `new <英文短名> --title "<标题>" [--status 待办\|进行中\|阻塞]` |
| 开工前认领看板上的一条（被拒 = 别的活会话在干，停下问用户） | `claim <短名>` |
| 用户同意后接手另一个会话（转走它的占用和认领） | `take <会话号前缀\|短名>` |
| 用 squash / cherry-pick 合并、或放弃了改动，收尾放掉占用 | `release` |
| 做完，移出看板 | `done <短名>` |
| 本仓库锁开关 | `lock on\|off\|auto` |
| 打印任务目录 | `path` |

`claim` / `take` / `release` 要在 Claude Code 会话的 Bash 里跑（靠 `CLAUDE_CODE_SESSION_ID` 认出自己）；Codex 会话里不可用，v1 锁只覆盖 Claude Code。

## 什么时候建条目、什么时候认领

- **看板上已有要做的事** → 先 `claim`，再动手。
- **一个会话做不完、会换会话接手、或用户说"记个待办"** → `new` 一条（默认 `待办`），要马上做就接着 `claim`。一问一答当场做完的不建。
- 条目只写标题、状态、一句目标；新建时自动填上 worktree、分支和 HANDOFF 路径。别往条目里写进度。

## 已知盲区

- 经 Bash / python / 其他非原生编辑工具落盘的写入，钩子看不到：既不占用也不拦。
- "是不是同一件事"靠开工前 `claim` 同一条，工具判断不了两个不同短名是不是一回事。
- 闲置很久但窗口还开着的会话仍然占着锁；对话日志时间只说明它多久没动，不代表可以自动接手——问用户。
