# project-state

## 先读
1. docs/HANDOFF.md —— 未完成工作与「已谈定」约定（如有）；使用记录前先核 Git 和交接现场。
2. docs/BLUEPRINT.md —— 当前行为契约；要动哪块读哪块。
3. docs/CHANGELOG.md —— 按改动主题查「没选的路」，不默认通读。

收尾由文档 fork 维护这些文件；实现中只把谈定的约定记入 HANDOFF。

## 命令
- PowerShell：`pstate thread --ai-help`、`pstate thread list --json`；Git Bash 用 `pstate.cmd`。无 shim 时直接运行 `scripts/pstate.py`。
- 本机 Codex 的 Python 固定为 `C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe`，不使用 py/python/python3；其他宿主按本机解释器约定调用。
- 测试：上述解释器执行 `-m unittest discover -s tests -v`；临时验证必须将 `PSTATE_HOME` 指向临时目录，不能写正式库。历史抽样用 discover 的 `files`。
- 全局事项接口、JSON 样例与自然语言工作流见 `skills/pstate/SKILL.md`；旧仓库命令见 `scripts/pstate.py --help`。
- hook 测试只用临时状态与显式输入；纯翻译和脚本调用通过，不等于安装后平台钩子通过。

## 约定
- 全局 thread 与旧 tasks/locks 分开；thread 不依赖 Git，不能把“旧条目只管待办”的规则套到它。进度现场仍在 HANDOFF。
- CLI 入口为 scripts/pstate.py；全局事项和历史适配分别在 scripts/pstate_threads、scripts/pstate_history。新增历史平台只改自己的适配器及注册入口。
- Claude 壳在 hooks/hooks.json（开始、写前、成功、失败、结束）；Codex 壳在 hooks/codex.json（仅开始）。钩子异常静默退出 0；开始提示只读事项计数，不扫历史。
- 进程后端只有 Windows；Codex 锁未适配。shell/python 写文件不在旧原生编辑工具锁的覆盖内；不能以此绕过已有拦截。
- PATH shim 指向金库活源，插件钩子指向缓存。更改后需更新两份 manifest、完成有授权的发布及 hub sync --refresh；新会话才能检验缓存中的钩子。
- 旧条目格式变化要同步模板、解析与 skill；全局状态契约变化要同步 --ai-help、验证及 skill。

## 坑
- .py 受 Esafenet 保护，读搜改走 esafe-code，不能把密文当损坏；.md/.json 当前为明文。
- thread 复杂输入走 UTF-8 JSON 的 --input-file 或 stdin；程序调用用参数数组，别把原话拼进 shell。
- 不手改正式 threads.json；损坏、未知 schema、主库缺失但备份存在都需明确恢复。rev 冲突先 show 重读；replaced_unverified 先查实际落库结果。
- discover 的 limit/max_chars 只限制返回页；普通 roots 每次读全部所选 JSONL，没有扫描缓存。只想抽样必须用 files。
