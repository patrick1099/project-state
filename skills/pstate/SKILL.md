---
name: pstate
description: 项目任务交接板（pstate）——同一 git 仓库的所有 worktree、所有 AI 会话（Claude Code / Codex）共用的待办与交接。Use when the user says 交接 / handoff / 记一下进度 / 记个待办 / todo / 下次接着做 / 收工 / 换个会话继续 / 让别的 AI 接手 / 现在都有哪些活 / 别的 worktree 在干什么；when a task will span sessions or worktrees; when stopping mid-task with uncommitted changes that must be kept; or when a SessionStart injection shows a "[pstate]" board. Not for projects that keep a vibe-flow HANDOFF.md — their progress stays in HANDOFF. 没有 HANDOFF 的仓库（公司仓等），进度和交接写这里；都不写进记忆。
---

# pstate：项目任务交接板

记忆存长期有效的结论和教训；**pstate 存"现在"**：谁在哪个工作区做什么、停在哪、下一步、在等什么、哪些未提交改动为什么要留着。一件事做完就移出活跃视图，不攒历史。

## 和别处怎么分：同一件事的进度只记一处

| 地方 | 管什么 |
|---|---|
| 项目的 `HANDOFF.md`（vibe-flow 的个人项目） | 这个项目做到哪、下一步、谈定的约定。有它就只写它，**不在 pstate 另建任务** |
| **pstate** | 没有 HANDOFF 的仓库（公司固件仓等）里跨会话、跨 worktree、要留未提交改动的活 |
| 平台原生记忆、hub 金库 | 只存长期结论和教训，**不记进度**。看到进度类记忆，以 git log 和上面两处为准，删掉或改成指针 |
| session-sweep（Obsidian「悬着的事」） | 只是发现入口：从它那里捡起一件事，进度落回上面两处之一，不在它那里维护 |

## 数据在哪

- 本机 `%LOCALAPPDATA%\ai-project-state\<仓库名>-<哈希>\tasks\<短名>.md`，一个任务一个文件。同一仓库的所有 worktree 找到的是同一个目录（按 `git rev-parse --git-common-dir` 识别），不进仓库、不进 git。
- 做完的任务在同级 `done\` 里，不会再注入。
- 文件是明文 UTF-8 `.md`，任何工具都能直接读写。

## 命令

| 场景 | Git Bash（Claude Code） | PowerShell（Codex） |
|---|---|---|
| 看现场：各 worktree 的分支和未提交改动数 + 活跃任务 | `pstate.cmd status` | `pstate status` |
| 新开一件跨会话的活 | `pstate.cmd new <英文短名> --title "<标题>" [--status 进行中\|阻塞\|待办]` | 同左，去掉 `.cmd` |
| 做完，移出活跃视图 | `pstate.cmd done <短名>` | `pstate done <短名>` |
| 打印任务目录 | `pstate.cmd path` | `pstate path` |

默认按当前目录识别仓库，`--cwd <目录>` 可指定。`new` 自动填上当前 worktree 和分支，打印新文件路径。PATH 上没有 `pstate` 时，SessionStart 注入里给了带绝对路径的 `py -3 "...pstate.py"` 调法，照用。

## 什么时候写

- **开新的跨会话活——AI 主动建，不等用户说"记个任务"**：在没有 HANDOFF 的仓库里，判断出要做的事一个会话做不完、会换 AI 接手，或要留未提交改动等评估时，当场 `new` 一个任务，并在回复里告诉用户一句建了什么。一问一答、当场做完的小事不用建。
- **有实质进展、要停下、要交接时**：直接编辑任务文件（普通 `.md`，用文件编辑工具），更新这几节：
  - `## 目标`：一两句话，用户要的结果。
  - `## 停在哪 / 下一步`：**第一行写下一步**，看板只显示这一行。零上下文的接手者读完要能直接开工。
  - `## 阻塞 / 在等什么`：等谁、等什么、什么条件解除。
  - `## 保留的未提交改动`：哪些文件的改动要留着、为什么不提交、哪些不能被覆盖。**git 能查到的改动清单不用抄**，看板每次现场汇总。
  - 头部 `- 状态:` 在 `进行中 / 阻塞 / 待办` 里改。
- **做完**：`done <短名>`。别直接删文件。

## 读的时候注意

- 看板标「久未更新」或「工作区已不在 git 登记里」的任务，内容可能过期，先核实再照做。
- 只在当前 worktree 的任务上动手；别的 worktree 的任务是别的会话的地盘，用户明说才接。
- 注入只在有活跃任务时出现，所以没看到看板不等于这个仓库没用过 pstate，拿不准就跑一次 `status`。
