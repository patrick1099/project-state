# project-state

## 先读
1. docs/HANDOFF.md —— 现在做到哪了、已谈定的约定（没有这个文件 = 没有进行中的工作）
2. docs/BLUEPRINT.md —— 它是什么、行为契约；要动哪块读那块，整体重构才通读
3. docs/CHANGELOG.md —— 不默认读；要改哪块，搜那块相关的「没选的路」

文档由收工时派出的 fork 维护（vibe-flow §8）；干活时只在谈定时往 HANDOFF 的「已谈定」区记一条，动手前读回（vibe-flow §4）。

## 命令
- 跑：`py -3 scripts/pstate.py status`（`new` / `done` / `path` / `hook` 见 `--help`）
- 测：`py -3 -m unittest discover -s tests -v`
- 试注入：`echo '{"cwd":"<仓库路径>"}' | py -3 scripts/pstate.py hook`；测试时设 `PSTATE_HOME` 指到临时目录，别污染真实任务目录

## 约定
- 唯一的逻辑在 `scripts/pstate.py`；两个平台只是壳：Claude Code 的钩子在 `hooks/hooks.json`，Codex 的在 `hooks/codex.json`（由 `.codex-plugin/plugin.json` 指过去），AI 用法说明在 `skills/pstate/SKILL.md`。
- 用户在 PATH 上的 `pstate.cmd` 指向金库 `shared/plugins/project-state/scripts/pstate.py`（主分支活源），钩子跑的是插件缓存里的那份。
- 任务文件格式（字段名、节名）改了，`SKILL.md`、`TASK_TEMPLATE` 和看板解析要一起改。

## 坑
- Esafenet：`scripts/*.py` 落盘是密文，Bash 的 `cat` 看到乱码，用 Read 工具或 `cmd /c type` 看；写 `.py` 只走 Claude 的 Write/Edit 或 python，别用 PowerShell。`.md` 实测是明文。
- Git Bash 里调 shim 要带后缀 `pstate.cmd`；PowerShell 里 `pstate` 即可。
- 改完插件要 bump 两份 `plugin.json` 的 version、推子仓、`hub sync --refresh`，否则平台还在跑旧缓存。
