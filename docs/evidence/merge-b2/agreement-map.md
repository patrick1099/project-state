# HANDOFF 有效约定去向

2026-10-23。按 P HANDOFF 首个“已谈定”区的15条逐项对账；只迁当前长期契约，不把授权历史、施工过程或本批状态写进记忆。当前计划 b1/b2 均完成，机器记录与验收材料保留在本证据树。

| 原条次 | 有效约定 | 当前权威去向 |
|---|---|---|
| 1 | 找回个人事项，由用户选择；工程0..N | BLUEPRINT §1、§2.6；skills/pstate/SKILL.md 当前会话及四个选择 |
| 2 | 一个产品、仓库和发布；旧后台独立产品退役 | BLUEPRINT §1、§2.8；S BLUEPRINT 行为与数据契约；本轮仅补明同一发布边界 |
| 3 | 原页、完整周档、路径与勾选保留 | BLUEPRINT §2.7–2.8；独立正式验收见 independent-audit.md |
| 4 | 唯一事项生命周期；旧tasks/locks兼容；AI判断不是决定 | BLUEPRINT §2.4、§2.6–2.8；AGENTS 约定 |
| 5 | schema1；冻结输入逐项回读、seen-last；失败先核实 | BLUEPRINT §2.6、§2.8；skill 多事项审阅；AGENTS 约定/易错点 |
| 6 | 平台原生身份、双版本；旧关闭不等于seen | BLUEPRINT §2.7；skill 内部后台与联合页 |
| 7 | 联合页与余部未审；默认AI筛选可找回；关闭仅提醒 | BLUEPRINT §2.7；skill 历史回顾及内部后台 |
| 8 | 摘要永久保留；丢源标记可撤；沿用原时间和未变判断 | BLUEPRINT §2.7；skill 内部后台与联合页 |
| 9 | 保留后台策略/元信息；平台能力有限；不加API/BYOK | BLUEPRINT §2.7、§3；AGENTS 共享平台注册与配置权威；本轮仅补明API/BYOK边界 |
| 10 | 模型在短锁外；整轮/输出互斥、底层归属闸；开始只轻提示 | BLUEPRINT §2.3、§2.8；AGENTS 约定；skill 后台 |
| 11 | 稳定隐藏runner；显式切换、最后勾选、先资料后页 | BLUEPRINT §2.8；AGENTS 约定；skill 后台；merge-b1/b2-commands.md |
| 12 | 不用旧快照覆盖新决定；前向恢复、同路径预演 | BLUEPRINT §2.8；skill 后台；独立正式验收及隔离恢复证据 |
| 13 | 个人worktree；发布版本/同步；无AI署名；不动其他区 | AGENTS 约定/易错点及现有全局约束；本轮未改范围外目录、未发布 |
| 14 | 旧只读/独立产品边界被本次合并替代 | 当前 P/S BLUEPRINT、S NEEDS；CHANGELOG 保留原取舍历史，不迁旧限制为当前约束 |
| 15 | 本次实现、退役、验证、发布和正式切换授权 | 已履行的一次性授权，不新增长期功能要求；结果见 release.md、independent-audit.md 与 final-closure.md |

P HANDOFF 的旧0.3.0/0.3.1交付区已是历史，仍有效的来源核验、状态选择、编码、幂等和宿主能力边界已在现行规格；被合并替代的只读约束不再沿用。既有 CHANGELOG 与 Git 保留历史，不复制成新进度库。

S HANDOFF 的三项归属待确认属于其他未完问题，保留原文并加退役说明，不替用户做决定，不自动生成事项。S AGENTS/NEEDS 改指当前规格与已完成证据，避免要求读取已删除的 P HANDOFF。
