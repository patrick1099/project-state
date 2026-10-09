# project-state

## 先读
1. docs/HANDOFF.md —— 现在做到哪了、已谈定的约定（没有这个文件 = 没有进行中的工作）
2. docs/BLUEPRINT.md —— 它是什么、行为契约；要动哪块读那块，整体重构才通读
3. docs/CHANGELOG.md —— 不默认读；要改哪块，搜那块相关的「没选的路」

文档由收工时派出的 fork 维护（vibe-flow §8）；干活时只在谈定时往 HANDOFF 的「已谈定」区记一条，动手前读回（vibe-flow §4）。

## 命令
- 跑：`py -3 scripts/pstate.py status`（`new` / `claim` / `release` / `take` / `lock` / `done` / `path` 见 `--help`）
- 测：`py -3 -m unittest discover -s tests -v`（进程身份用假后端 `FakeProc` 打桩；真 git 仓库建在临时目录）
- 试注入：`echo '{"cwd":"<仓库路径>"}' | py -3 scripts/pstate.py hook`；测试时设 `PSTATE_HOME` 指到临时目录，别污染真实任务目录
- 试真实拦截：临时目录建一个带 `docs/BLUEPRINT.md` 的仓库，往 `PSTATE_HOME` 的 `locks.json` 里写一条由另一个活 `claude.exe` 占着的占用，再用 `claude -p ... --settings <只含 PreToolUse/PostToolUse/PostToolUseFailure/SessionEnd 四个钩子、指向 worktree 里脚本的 json>` 让 headless 会话去改那个文件。占用者别用本会话进程：headless 会话是它的子孙，算同一家，会被放行。

## 约定
- 入口在 `scripts/pstate.py`（命令表、钩子入口、看板）；锁的纯逻辑和进程 / 文件锁 / git 适配在 `scripts/pstate_lock.py`。两个平台只是壳：Claude Code 的钩子在 `hooks/hooks.json`（SessionStart → `hook`，PreToolUse → `hook-pre`，PostToolUse → `hook-post`，PostToolUseFailure → `hook-fail`，SessionEnd → `hook-end`），Codex 的在 `hooks/codex.json`（只有 SessionStart），AI 用法说明在 `skills/pstate/SKILL.md`。
- 钩子子命令统一经 `run_as_hook` 进来：任何异常、参数错误都静默退出 0，只有拦截时才输出 JSON。
- 进程身份只实现了 Windows（`PROCESS_BACKENDS` 里只有 `nt`）；其他系统认不出会话，锁自动不生效。
- 条目文件格式（字段名、节名）改了，`SKILL.md`、`TASK_TEMPLATE` 和看板解析要一起改。
- 用户在 PATH 上的 `pstate.cmd` 指向金库 `shared/plugins/project-state/scripts/pstate.py`（主分支活源），钩子跑的是插件缓存里的那份。

## 坑
- Esafenet：`scripts/*.py` 落盘是密文，Bash 的 `cat` 看到乱码，用 Read 工具或 `cmd /c type` 看；写 `.py` 只走 Claude 的 Write/Edit 或 python，别用 PowerShell。`.md`、`.json` 实测是明文。
- Git Bash 里调 shim 要带后缀 `pstate.cmd`；PowerShell 里 `pstate` 即可。
- 写入前钩子每次 Write/Edit 都跑，本机约 0.35～0.4 秒（大头是 py 启动和一次 `git rev-parse`）；往里加东西前先想想是不是每次写都要做。
- 改完插件要 bump 两份 `plugin.json` 的 version、推子仓、`hub sync --refresh`，否则平台还在跑旧缓存；钩子定义变了要新开会话才生效。
