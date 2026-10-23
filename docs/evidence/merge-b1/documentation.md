# merge-b1 文档收尾证据

2026-10-23。文档角色只写获派 Markdown：两仓 AGENTS.md、docs/BLUEPRINT.md、docs/CHANGELOG.md，S docs/NEEDS.md，P skills/pstate/SKILL.md，以及本文件和 b2-commands.md。未写任一 HANDOFF、代码、测试、配置、manifest/version、正式数据或计划任务；无提交、发布、真实模型调用。

## 文档依据与结果

读回当前有效约定、计划、旧文档与最终独立验证：P200/200无skip、CLI7/7；S47/47独立执行，随后相关源码/测试指纹未变。初次19失败子场景及修复记录仍保留在 validation.md，没有改写为首次即通过。

P蓝图/技能记录唯一事项schema1、旧tasks/locks兼容、双版本、永久来源、默认AI筛选、部分关联与完整已审、勾选提醒、冻结多事项逐项保存回读/seen-last、输出归属/互斥、显式迁移、中断前向恢复与暂停回退。S当前蓝图改为退役契约；NEEDS新增当前合并需求，合并前逐字正文保留为历史，仅按监工指令移除加粗Markdown标记；旧变更记录保留。两仓HANDOFF均保留。

b2-commands.md提供已核CLI语法与稳定活源路径的完整待执行清单。任务注册拒worktree/cache、发布不自动启用、旧在途确认、全资料备份、未知锁不按年龄抢、暂停不覆盖新决定等边界均保留。正式版本号、manifest变更/提交和hub实际刷新由b2执行；本轮未核外部hub CLI帮助，不编造自动bump或S远程发布命令。

## 真实文档检查

使用固定解释器与真实脚本，不以目测代替闸：

```text
Python = C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe
Lint = C:/Users/huawei/.codex/plugins/cache/xu-skills/xu-skills/0.10.1/skills/writing-readme/scripts/readme_lint.py
Skill validator = C:/Users/huawei/.codex/skills/.system/skill-creator/scripts/quick_validate.py
```

- 修改前真实 check：P技能失败2项（首屏证据/说理位置）；P旧蓝图失败3项、S旧蓝图失败2项。没有把这些旧结果当成通过。
- 修改后 `-B <Lint> check <P/skills/pstate/SKILL.md>`：exit0，无超阈值命中；首屏加入可直接调用的真实命令。
- 修改后 `-B <Lint> check <P/docs/evidence/merge-b1/b2-commands.md>`：exit0，无超阈值命中。
- 两仓AGENTS/BLUEPRINT：`-B <Lint> check <文件> --skip evidence-first install-pos preamble`，四份均exit0。它们是入口约定/规格参考，不承担README证据、安装和使用前说理的位置要求；文体、加粗、长度等其余检查实际运行。
- 两仓CHANGELOG及S NEEDS首次增量检查：`-B <Lint> diff <文件> --skip evidence-first preamble install-pos`，三份均exit0，相对HEAD无新增命中，各有1项历史存量。监工要求S NEEDS去除旧加粗标记后全量过闸，已只删第15、21-23、30-31、36-40、48-52行的Markdown强调，逐字正文保留；最终check结果见下。两份CHANGELOG按skill增量模式保留历史条目，不重写旧记录。
- diff运行的Git信任仅通过当前进程GIT_CONFIG_COUNT与两条safe.directory传给子进程，没有写全局或仓库配置。
- `-B <Skill validator> <P/skills/pstate>`：`Skill is valid!`，exit0。
- PowerShell Parser逐块解析b2清单13个powershell代码块：parse_errors=0。仅解析文本，没有执行其中发布、复制、迁移或计划任务命令。

CLI语法核查实际执行 `pstate.py sweep --help`、`sweep --ai-help`、`sweep review --help`、`sweep switch --help`、`sweep import-legacy --help`，并通过esafe-code读cli parser、review输入、明文pyw与调度适配器。所有读取只用于核命令/契约，没有写入源码。曾查询 `thread review --help`得到非法子命令，已确认正确入口是sweep review；最终文档没有使用错误入口。

## 文档与代码边界

文档前监工提供的非MD等价严指纹：

- P：0dbc4bef2d22690a2f4acd4c498193f2a19f158bb0e46d0887ffddeee318aa31
- S：e3e07f6a33a0cbbba3c67a6578e7973373cdcd9a658a6dd6102ae9977ebadc3f

文档角色没有写非MD文件；文档后指纹由监工独立核对，本文件不预先声称核对已通过。正式库全量对账、发布/缓存刷新、真实计划任务替换和未来定时触发、安装后交互hook均未在本轮执行。文件symlink边界不扩大，不能把已测junction冒充任意文件链接均已测。

最终补记：S NEEDS经 -B <Lint> check <S/docs/NEEDS.md> --skip evidence-first preamble install-pos 全量检查exit0，无超阈值命中；未跳过bold规则。与Git HEAD历史段分别去除加粗标记并统一换行后，逐字正文对比为True（1668字符）。首次直接比对因CRLF/LF不一致返回False，换行归一后确认无文字改写。
