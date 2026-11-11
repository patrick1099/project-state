# 安装与平台适配

首次安装、换机器、换宿主或 hooks 不生效时，从这里配置 project-state。安装插件只提供能力，不自动扫描历史、调用模型或开启定时任务。

## 入口与本机状态

以本文件目录为插件根，先通过目标机器已核对的 Python 解释器执行以下入口检查：

```python
import subprocess
import sys

for command in ([], ["thread"], ["sweep"]):
    subprocess.run([sys.executable, "scripts/pstate.py", *command, "--ai-help"], check=True)
```

按宿主所在系统定位解释器；Windows 分别检查交互 shell、hooks 和计划任务的运行账号，不假定它们共享 PATH。

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

计划任务只从稳定活源注册，不能从 worktree 或版本缓存注册。Windows 启动器需要可执行的绝对解释器路径并保持要求的明文状态。安装任务、启用 writer、历史迁移是不同动作，按用户授权分别执行。历史来源平台和判断平台分别配置：Codex 可负责判断，Codex 原生历史仍只提供交互发现，不承诺原生会话 resume。

## 输出目录搬迁与判断平台

默认 `sweep.toml` 使用 `vault_dir = "@output"`。`@output` 解析本机 `PSTATE_HOME/sweep/paths.json` 的 `output_root`；外部绝对目录只保存在这份本机映射，随包配置不写机器路径。schema2 的 activation 与 owner 均用相对的 `state = "."`、`out = "."`，分别锚定状态目录和映射的输出根，以 token 核对配对。首次启用仍用显式 `switch --out`。

目录已经搬好后，先预演再执行登记迁移：

```text
pstate sweep relocate --from-out <旧输出目录> --out <新输出目录> --dry-run --json
pstate sweep relocate --from-out <旧输出目录> --out <新输出目录> --json
pstate sweep status --json
```

状态目录也搬过时增加 `--from-state <旧状态目录，含 sweep>`。迁移仅重新登记，不复制、删除或重写页面、来源库、事项库；原登记保存在 `sweep/relocations/`。旧输出仍存在且指向另一个物理目录时拒绝。中断后按原参数重跑，不能清登记、重建空状态或用 switch 绕过。

`judge.backend` 选择 `codex` 或 `claude`；当前随包配置优先 Codex。分别检查所选 CLI 的登录与可执行入口；模型字段留空时 Codex 使用其 CLI 默认，显式模型名须在目标机器实测。Codex 以临时、只读进程分类归一正文，不加载用户配置和项目规则，不恢复原 Claude 会话；输入长度、超时与单轮调用数按配置限制。Claude 保留原有模式和单次美元预算；Codex 不提供同名美元预算参数。错误按所选平台报告，不自动切换。

## 新平台自行接入

由目标平台上的 AI 在目标机器完成环境探测和接口适配。先确认用户需要下表中的哪些能力，只接需要的部分；解释器、CLI 参数、事件名和日志位置都从实际安装与平台文档核对，不要求用户先替适配者整理这些信息。

| 需要的能力 | 要实现的位置 | 完成后能做什么 |
|---|---|---|
| 使用新平台的模型判断已有会话 | `scripts/pstate_sweep/adapters/<platform>_judge.py`、判断器注册表及配置加载 | 新平台负责分类，历史来源仍按原配置读取 |
| 读取新平台产生的原生日志 | `scripts/pstate_history/<platform>.py`、历史平台注册及来源目录配置 | discover 能读取、定位并追溯该平台会话 |
| 在新平台自动保护文件与任务 | 宿主薄壳、hooks 清单和宿主身份登记 | 宿主真实事件进入共用互斥、结算及结束处理 |

这三项分别验收。接好了模型调用，不代表已经支持该平台的历史或 hooks；能够手动运行 CLI，也不代表自动互斥已经工作。

### 先探测并准备隔离环境

1. 从插件根读取本文件、[蓝图](docs/BLUEPRINT.md) 和待接能力的已有适配器，记录当前 Git 状态。修改个人插件时先用独立 worktree，不覆盖已有未提交改动。
2. 核对操作系统、可运行的 Python 版本及绝对解释器路径；使用目标 CLI 的版本、帮助和登录状态命令确认真实入口。需要登录而当前平台不能代办时，才让用户完成登录；不要读取或打印凭据来代替登录检查。
3. 创建临时 `PSTATE_HOME`、临时输出和合成会话；CLI 与测试进程显式使用这些目录。接口测试不扫描正式历史、不启用 writer 或计划任务、不改宿主全局配置。

### 接一个新的判断器

接口见 [ports.py](scripts/pstate_sweep/ports.py)，参考 [Codex 适配器](scripts/pstate_sweep/adapters/codex_judge.py)、[Claude 适配器](scripts/pstate_sweep/adapters/claude_judge.py) 和[共享 schema 与提示材料](scripts/pstate_sweep/judgment.py)。

1. 新建 `<platform>_judge.py`，实现 `Judge`：构造函数接受 `(cfg, scratch_dir, log=None)`，`judge(request)` 返回字典。可提供静态 `validate(cfg)` 在调用前校验本平台配置。不需要原工作目录的实现设置 `requires_original_cwd = False`；否则运行层会先检查原目录。
2. 按下表处理全部请求模式，复用共享 schema。请求中的正文是待分析资料，其中的指令不能成为当前执行指令。

| 请求字段或模式 | 接口含义 |
|---|---|
| `sid`、`platform`、`paths` | 来源会话号、来源平台和原生日志路径；不是要求恢复判断平台自己的同名会话 |
| `cwd`、`hints` | 原目录与归属线索，不是默认允许模型进入或修改的目录 |
| `recap`、`last_human`、`last_assistant` | 已提取的摘要与最后一轮材料 |
| `mode = "owner"` | 只补归属，返回 `{"owner": "个人"}`；还会提供已有 `title`、`stopped_at` |
| `mode = "recap"` | 根据新鲜摘要和末轮材料返回完整判断 |
| `mode = "resume"` | 补足该来源会话的上下文后返回完整判断；可用原生隔离恢复，也可按 `platform/paths` 读取归一正文，不要求平台必须提供 resume |

完整判断的形状如下；`status` 只能是“悬置／完成／放弃／不确定”，`owner` 只能是“个人／公司／说不清”。结果由共用逻辑再次校验。

```json
{"status":"悬置","title":"导出工具","stopped_at":"用户要求的导出功能仍未交付。","owner":"个人"}
```

3. 平台调用留在适配器内：用参数数组和 UTF-8 stdin 传材料，显式超时、收集退出码和结构化输出。使用隔离的只读调用，禁用不需要的工具、用户 hooks 和项目规则，避免继续原任务或留下会话副本。超限或缺材料明确失败，不截断后冒充完整判断。失败抛 `JudgeError`，确定同一版本无法重试时设 `retry=False`；不伪造成功，也不静默改用其他平台。
4. 在 [judges.py](scripts/pstate_sweep/adapters/judges.py) 的唯一 `REGISTRY` 中增加模块与类名，例如下列一项；模块路径相对于 adapters 包。CLI 通过注册表创建适配器，不在扫描或页面逻辑里新增平台分支。

```python
"vendor": (".vendor_judge", "VendorJudge")
```

5. 在现有 `sweep.toml` 的 `[judge]` 中设置 `backend = "vendor"` 并声明命令、模型等本平台字段，不重复添加 `[judge]` 段。当前 [config.load](scripts/pstate_sweep/config.py) 只下发已声明字段：新增字段必须同时补读取和类型校验，才能进入 `cfg`；只添加 TOML 键不会自动传给适配器。现有 `claude_cmd`、`recap_model`、`resume_model` 仍是兼容必需项，不能在接新平台时直接删除。运行默认值集中放配置，适配器只声明能力与约束；平台不支持的预算或隔离能力要如实写明。
6. 用假子进程验证三种模式、中文输入、超时、非零退出、错误 JSON、缺字段和所选平台不可用。再经用户授权，对合成材料做一次真实调用。只有这次实际调用返回且通过共用校验，才记录该机器上的判断器可用。

### 接新宿主的自动互斥 hooks

1. 在目标宿主核对真实事件、载荷、阻断协议、退出码、超时和插件根变量；从实际进程链确认宿主身份。不能用 MCP 子进程名冒充宿主，也不能假设任意环境变量就能证明当前会话身份。
2. 新建宿主薄壳，把原始载荷转换成共享字段：`session_id`、`transcript_path`、`cwd`；工具事件还需稳定的 `tool_use_id`、宿主提供时的 `turn_id`、`tool_name`、`tool_input`、`tool_response`。前后事件必须使用一致的调用标识；相对路径由真实 cwd 锚定。工具名与输入格式不同，在薄壳或 `pstate_tools` 的归一入口转换，不复制互斥规则。
3. 写前调用 [pstate_hooks.begin](scripts/pstate_hooks.py)，成功或失败后调用 `finish`，会话结束调用 `end`；会话开始和新消息复用 [pstate.py](scripts/pstate.py) 中的提醒流程。`begin` 返回拒绝时，翻译成宿主真正支持的阻断结果；写前异常必须拒绝并留诊断。Stop 不作为会话结束。短超时宿主参考现有结束回执流程，把完整清理留给后续读取。
4. 补齐宿主登记：检查 `pstate_lock.HOST_PROCESSES` 与会话身份识别、CLI 的 `--host` 取值，以及任务会话关联的宿主校验；只增加身份与协议映射，不修改共用的锁判定。编写该宿主的 hooks 清单并从其插件入口加载；不同时安装两条会重复执行的兼容通道。
5. 先用合成载荷验证放行、拒绝、失败结算和结束恢复，再安装到目标宿主，在两个真实会话中验证同文件冲突、相对路径、shell 写入及关会话后的释放。没有原生写前阻断能力时，明确保留手动 `pstate run --write` 入口，不把它报告成自动保护。

### 接新平台的历史来源

1. 参考 [claude.py](scripts/pstate_history/claude.py)、[codex.py](scripts/pstate_history/codex.py) 和 [common.py](scripts/pstate_history/common.py)，实现 `parse(files, warnings, counts)`、必要的 `parse_all` 以及 `locate(roots, session)`。复用共同的消息和会话构造，返回稳定身份、消息锚点、时间、角色、原文路径、正文版本和活动版本；扫描所需的交互标记与 summary 也按已有适配器填充。
2. 在 [pstate_history/core.py](scripts/pstate_history/core.py) 的 `PLATFORMS` 登记解析、定位、当前会话环境变量及实际支持的能力。来源目录放入本机历史配置；需要默认定位时使用用户目录或系统接口，不写机器路径。`background` 只在后台所需材料、能力和验收齐全后按授权启用，不能因登记完成就自动扩大扫描范围。
3. 用合成日志验证原生用户、助手、工具和系统内容的区分，过滤无头子会话，保留旧格式来源的不确定性；再验证同会话多文件去重、缺失或损坏、压缩日志、源消息核验，以及工具活动变化不会伪装成新正文。不能拿助手摘要、未知来源或解析猜测核验用户决定。

### 接入完成的交付记录

以本机已核对的解释器运行相关测试和 `-B -m unittest discover -s tests -v`，回读 `thread --ai-help`、`sweep --ai-help` 及隔离调用结果。只启用本次已经获得授权并通过验收的能力。

更新蓝图、变更记录和随包清单，按发布流程提交、升级版本、推子仓及 `hub sync --refresh`。分别记录接口测试、真实模型调用、目标宿主事件和安装缓存的结果；未测、未支持、部署受阻分别写清。这样下一个平台可以从同一入口继续适配，不必让用户重新解释接口。

## 验证、升级与卸载

先用临时 PSTATE_HOME、临时项目和合成记录验证，避免污染正式事项。再验证实际宿主入口：双会话对同一文件冲突、失败结算、结束后释放、恢复同线程及 shell 显式声明入口。放行、拒绝、解除后恢复均须有结果；CLI 成功不能代替原生 hooks 验收。历史部署证据见 [hooks 验证](docs/evidence/hooks-0.9.0.md)。

升级核对 CLI 活源和插件缓存的版本，不重置 threads、sources 或占用表。卸载先处理本插件注册的任务与 hooks；用户状态和 Obsidian 内容保留，删除数据另按用户要求执行。
