# 安装与平台适配

首次安装、换机器、换宿主或 hooks 不生效时，从这里配置 project-state。安装插件只提供能力，不自动扫描历史、调用模型或开启定时任务。

## 入口与本机状态

以本文件目录为插件根，使用可运行本仓脚本的 Python，先执行 `scripts/pstate.py --ai-help`、`scripts/pstate.py thread --ai-help` 和 `scripts/pstate.py sweep --ai-help`。按宿主所在系统定位解释器；Windows 分别检查交互 shell、hooks 和计划任务的运行账号，不假定它们共享 PATH。

状态根由 `PSTATE_HOME` 或程序默认位置决定；默认本机数据位置见 [pstate](skills/pstate/SKILL.md)。CLI 与 hooks 必须访问同一份预期状态，且各自有相应读写权限。权限失败先核对账号与目录，不创建第二套空状态冒充修复。可选全局命令薄壳应指向稳定活源，不指向临时 worktree。

| 宿主 | 随包入口 |
|---|---|
| Claude Code | [.claude-plugin/plugin.json](.claude-plugin/plugin.json)、[hooks/hooks.json](hooks/hooks.json) |
| Codex | [.codex-plugin/plugin.json](.codex-plugin/plugin.json)、[hooks/codex.json](hooks/codex.json) |
| 其他宿主 | 尚无随包 hooks；手动 CLI 可用与自动会话保护可用分别验收 |

通过宿主插件机制加载，完成必要的启用或信任；已有插件注册时不再重复添加全局 hooks。

## hooks 的行为契约

| 时机 | 所需信息 | 必须保持的结果 |
|---|---|---|
| 会话开始、用户新消息 | 宿主会话身份、事项来源及状态 | 仅提示相关事项与占用，不在入口自动扫描历史或调用模型 |
| 写入之前 | 会话身份、目标仓库、本次完整写路径；移动含两端 | 同一批路径原子裁决；冲突或写前检查出错时拒绝，不能改用另一个工具绕过 |
| 写入成功或失败之后 | 对应调用身份、写入结果 | 结算该次占用；失败只回滚符合原规则的新占用 |
| 会话结束及恢复 | 会话身份与结束回执 | 结束回执和后续清理相衔接；Stop 不是 SessionEnd，恢复同线程须先处理旧状态 |

权威行为见 [蓝图](docs/BLUEPRINT.md)，实现入口与边界见 [AGENTS.md](AGENTS.md)。复用 pstate_hooks、pstate_tools 的共享判断；新宿主薄壳负责输入与输出翻译。没有可信会话身份、写前阻断或完整路径信息时，不能宣称自动互斥已支持。

现有 Codex Windows 结束入口使用 `scripts/pstate_end.ps1`，其他结束入口见清单；不要把耗时清理重新塞回短超时钩子。未知 shell 副作用不等于已识别完整写集合。

## 可选后台与计划任务

只有用户需要后台功能时才配置。先读 `sweep --ai-help`、[当前切换命令](docs/evidence/merge-b1/b2-commands.md) 和稳定活源的 sweep.toml，核对输出目录、历史来源、模型预算和运行账号。不要沿用仓库中另一台机器的路径。

计划任务只从稳定活源注册，不能从 worktree 或版本缓存注册。Windows 启动器需要可执行的绝对解释器路径并保持要求的明文状态。安装任务、启用 writer、历史迁移是不同动作，按用户授权分别执行；Codex 交互发现不等于支持后台自答。

## 验证、升级与卸载

先用临时 PSTATE_HOME、临时项目和合成记录验证，避免污染正式事项。再验证实际宿主入口：双会话对同一文件冲突、失败结算、结束后释放、恢复同线程及 shell 显式声明入口。放行、拒绝、解除后恢复均须有结果；CLI 成功不能代替原生 hooks 验收。历史部署证据见 [hooks 验证](docs/evidence/hooks-0.9.0.md)。

升级核对 CLI 活源和插件缓存的版本，不重置 threads、sources 或占用表。卸载先处理本插件注册的任务与 hooks；用户状态和 Obsidian 内容保留，删除数据另按用户要求执行。
