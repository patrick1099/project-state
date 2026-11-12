# 0.9.5 DSH 宿主适配验证

## 平台证据（真实进程链，2026-11-10）

- 本会话 `pwsh` 工具调用链：`pwsh.exe`(26796) ← `DeepSeek Harness.exe`(39244) ← `DeepSeek Harness.exe`(35364) ← `DeepSeek Harness.exe`(30620) ← `explorer.exe`。
- GUI `127.0.0.1:31099` 的监听进程是 PID 35364，命令行含 `--expose-internals ...\@deepseek-ai\dsh-desktop-host\lib\index.js ... profiles\desktop`：DSH desktop host 是 Electron 二进制以 node 模式运行，进程名就是 `DeepSeek Harness.exe`。
- 35364 的子进程里确有多个 `node.exe`（`rea.mjs mcp`、playwright MCP 等），但没有承载 agent 的 node 进程，因此 `HOST_PROCESSES["dsh"]` 只登记 `("deepseek harness.exe",)`，不登记 `node.exe`。若将来 dsh TUI/CLI 改用 node.exe 承载 agent，须先取新进程链证据再加。
- DSH 给工具子进程注入的环境变量：`DSH_SESSION_ID`、`DSH_HOME`、`DSH_PROFILE`、`DSH_PROFILE_DIR`、`DSH_SHELL`、`DSH_WEB_URL`。`DSH_SESSION_ID` 会被所有子进程继承，所以 `host_session()` 只在没有 Claude/Codex 变量时才认它；hook-pre 的写前互斥仍只用进程链 identify，不靠这个变量。

## 事件与载荷翻译

| DSH 原生事件 | 薄壳命令 | 共享入口 | 退出码语义 |
|---|---|---|---|
| `agent/created` | `start` | 清理结束回执 + `hook_context`/`thread hint` 提示，不扫描历史 | 0 |
| `agent/inbox/inserted` | `prompt` | 每轮来源卡提醒（只读事项库） | 0 |
| `tools/pre-execute` | `pre` | `pstate_hooks.begin`（写前裁决，出错闭锁） | 0 放行 / 2 阻断 |
| `tools/post-execute` | `post` | `pstate_hooks.finish`，失败由 `tool_response` 判定 | 0 |
| `agent/disposed` | `end` | `pstate_hooks.end`（会话、认领、在途标记、回执） | 0 / 1 非阻断错误 |
| `agent/turn-stopping` | （未在清单注册） | 不做检查、不释放：Stop 不是 SessionEnd | — |

归一：`tool_name` 小写名映射（write→Write、edit→Edit、pwsh/bash→Bash，apply_patch 原样），相对 `workdir` 锚到 payload `cwd`，`dsh.session_log`→`transcript_path`，`dsh.turn`→`turn_id`，`hook_event_name` 映射成 Claude 兼容事件名供共享输出使用；`dsh` 会话键加 `dsh:` 前缀。

## 行为回归

`py -3 -B -m unittest discover -s tests -v`（2026-11-10）：**Ran 314 tests, 241.078s，313 通过、1 失败**。DSH 新增 31 项全部通过（`tests/test_dsh_hooks.py`）：载荷翻译与工具名归一、走真实 git 仓库（假进程后端）的放行/冲突拒绝/多路径无部分占用/坏状态闭锁/结束回执恢复/结算成功与失败回滚/`agent/disposed` 清理/`start` 只提示不扫描不占用/未知命令非阻断/清单契约。

唯一失败 `test_worktree_and_version_cache_registration_refused_even_in_dryrun` 属既有环境差异：该用例要求从链接 worktree 运行（`validate_launcher` 靠 `.git` 是文件来识别 worktree），而本仓当前是主 worktree（`.git` 是目录）。用 `git worktree add --detach <临时目录> HEAD` 建临时链接 worktree 后单独跑 `test_sweep_runner.py` 得到 `Ran 5 tests ... OK`，随后 `git worktree remove --force` 清理；`scripts/pstate_sweep/adapters/schtask.py` 本次未改。

既有 claude/codex 用例无需改判定即可通过；仅 `tests/test_session_links.py` 的夹具在清空宿主环境变量时补上了 `DSH_SESSION_ID`（否则在本机 DSH 里跑测试会把继承值当成 dsh 会话）。

## 脚本级三态（直接跑 pstate_dsh.py）

临时 PSTATE_HOME + 临时个人 git 仓，真实 DSH 进程链（父进程链含 `DeepSeek Harness.exe`），A/B 两个会话号：

| 步骤 | 命令 | 退出码 | 状态变化 |
|---|---|---|---|
| 会话开始 | `start` (`agent/created`) | 0 | 无输出，未写状态 |
| A 预写 | `pre` (`write a.txt`) | 0 | `claims["a.txt"].holder = dsh:A` |
| B 预写同一文件 | `pre` (`write a.txt`) | 2 | stdout `permissionDecision: deny`，占用仍归 A |
| A 写入成功 | `post` (`is_error=false`) | 0 | `written = true` |
| A 结束 | `end` (`agent/disposed`) | 0 | 会话与占用清空 |
| B 预写 | `pre` (`write b.txt`) | 0 | 恢复放行，占用归 `dsh:B` |
| B 写入失败 | `post` (`is_error=true`) | 0 | 本次新建占用回滚 |
| turn-stopping | `turn` | 0 | 无输出、不释放 |
| 未知命令 | `bogus` | 1 | stderr 诊断，非阻断 |
| 坏 JSON | `pre` | 2 | 闭锁拒绝 + `hook-diagnostics` |

## 帮助入口

`pstate.py --ai-help`、`hook --ai-help`、`thread --ai-help`、`sweep --ai-help`、`link --ai-help`、`run --ai-help` 均退出 0；`hook-pre --help` 显示 `--host {claude,codex,dsh}`；`pstate_dsh.py --help` 退出 0。

## 明确未做

- DSH 原生历史发现（`~/.dsh/sessions/<cwd键>/<sid>/session.v4.jsonl.zstd`，v4 记录流）本轮不注册进 `pstate_history.PLATFORMS`：没有格式契约，不猜人工/agent 消息判定。
- 未经 dsh-claude-plugin-loader 安装后的真实交互钩子验收；本记录只证明载荷翻译、共享判定和进程级三态。
- 父金库 `shared/plugins/manifest.toml` 的 platforms 与 本机设备目录下的 `device.toml` 由 Lead 维护，不在本仓改动范围。

## 磁盘加密态

改动脚本经 esafe-code 读→改→写回，`esafe-code info` 复查：7 个修改脚本、新增 `scripts/pstate_dsh.py` 与 `tests/test_dsh_hooks.py` 均为 `encrypted`、CRLF；`hooks/dsh.json`、两份 manifest 与文档为 `plain`、CRLF。
