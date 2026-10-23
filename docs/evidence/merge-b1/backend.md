# merge-b1 backend 实施证据

日期：2026-10-23。工作树：`C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge`。基线：`4921f7858ab2`。只修改 `scripts/pstate.py`、新增 `scripts/pstate_sweep`、`sweep.toml`、后台测试与本文；未修改 S、已谈定、HANDOFF 机器区或 BLUEPRINT，未提交、发布、合并、注册计划任务。没有读取凭据、调用真实模型、扫描真实全量聊天或写正式状态/页面。

## 已实现与接点

- `pstate sweep status|run` 接入原入口；原 thread、tasks/locks 路由保持。`--json`/`--format json`、eager `--ai-help`、用法错误信封和 0/1/2 退出语义齐全。
- 新发布无隐式启用/迁移。`runtime.require_enabled(home, state_dir, out_dir)` 当前始终明确拒绝，真实 CLI run 在扫描和建立模型适配器之前失败且不创建状态/输出。下一迁移任务须将此窄接点替换为显式 switch 与所有权核验；没有 force/unsafe 绕过。
- `runtime.run_sweep(cfg, judge, gate_loader, out_dir, state_dir, ..., threads_dir=None, history_loader=load_sessions, history_input=None, dry_run=False)` 是可注入后台入口。整轮 `store.RunLock` 使用内核字节锁，不靠超时夺锁，跨进程互斥。模型阶段不持 thread 短锁，调用结束后才 `pstate_threads.store.read()` 获取快照。
- 共用 `pstate_history.load_sessions(..., include_excluded=True)` 与 `PLATFORMS.background`、interactive。没有第二份原始 rows parser 或后台平台名单；单点平台注册表派生扫描 roots，Codex 后台能力没有扩大。刷新时保留 review_version / activity_version / 原 timestamp、recap_fresh、owner 与成功写路径；模型请求使用原生 sid。
- `sweep.toml` 是模型、预算、路径和调度运行策略的唯一默认配置。旧路径/模型/预算/失败上限保留，新来源 roots 配置在 `[history.roots]`。删除旧 state_dir/projects_dir 选择，CLI 状态固定 `PSTATE_HOME/sweep`，不落旧 session-sweep 缓存。配置严格校验必需字段、正预算、页面相对文件名与平台 roots。
- `pstate_sweep.adapters.claude_judge` 保留 `--safe-mode`、空 tools、no-session-persistence、schema、budget、timeout、resume fork 隔离；docgate 与 schtask 适配器迁入。调度模块当前只是保留的适配器，未提供正式安装注册入口。

## 永久资料、页写与迁移形状

- `store.STATE_FILE == "sources.json"`。这是永久来源资料，不是可删缓存；version=1，sessions 按 `platform:native_id` 保存 `{summary, paths, gone, verdict?, error?, owner_error?, source_unavailable?}`。顶层 closures、thread_closures、legacy_marks、last_run；不存在第二事项状态。没有 sig 缓存；当前共用解析器每轮读取所选来源，任何未来可重建缓存均不能覆盖或清掉 sources.json。
- summary 原时间不以扫描时间刷新。正文消失只标 gone 并保留摘要、判断、失败；来源重现撤 gone 且未变内容不重调模型。来源读失败/重新分类无法返回时明确暂不可读，不擅称已清理。缺路径资料同样保留。
- 新来源提醒标记 `%%ss:platform:native_id@activity_hash%%`；closures 主键同形。旧 `ss:sid@timestamp` 和“关闭 sid @ timestamp”继续识别，legacy_marks 只记录旧别名首次绑定的 `{identity, activity_version}` 来源指针，后续 setdefault，不能把同 timestamp 的新工具活动重新绑定旧勾。
- `runtime.write_pages(cfg, state, out, now, since_ts, doc_issues, doc_error, save, snapshot=None, dry_run=False)` 是统一页写入口。模型结束后最后读主清单/已处理/全部工具归档页的勾选，合并后先 save，再写页。旧主清单、已处理、周归档文件名和条目完整正文/恢复命令保留；只删除带工具标记的空归档。多个归档重复勾选恢复优先。
- `store.write_page_if_changed(path,text,dry_run=False)`、`remove_generated(path,predicate,dry_run=False)`、`save_state(...,dry_run=False)` 把 dry-run 闸落在正式写路径上。dry-run 不调用模型、不写业务状态或页，仅允许协调锁文件；只有 FileNotFound 当空，权限、编码、归档枚举或其他 IO 失败停写。内容不变不重写。
- 下一迁移任务须在统一页写入口/底层写删函数加入所有权闸，以及按输出目录的跨进程整轮互斥；接入显式 import/switch/rollback、冻结输入捕获与完成后 seen、稳定 runner 和旧入口兼容。当前只有按 state_dir 的整轮锁，尚不能声称不同 state_dir 的相同 out 已互斥。

## 联合页语义

- 从唯一 threads store 的短锁只读快照渲染完整 active 事项卡，保留动机、多行停点、问题、AI建议、工程/HANDOFF、原话/哈希/时间/来源、决定及证据。`%%pt:id@rev%%` 提醒勾选仅写 thread_closures，绝不更改事项或 seen；取消恢复。
- 从 item.sources 现场派生来源关联，无额外生命周期副本。一会话关联 N 项仍显示“已关联N项，余部未审”；仅 seen.version 匹配当前 review_version 才从待审区收起，完整原来源正文仍在相应主清单/已处理/周归档的“已审来源资料”区保留。
- 用户主动旧 sid@timestamp/新活动hash勾选关闭整来源版本提醒，包括未识别余部，不动独立事项/seen，discover 仍可找回。新来源不会重开 parked/dropped/done；旧 AI 完成/放弃判断从不写用户决定或事项完成。
- root 已核口径：保留旧后台对完成/放弃候选的筛除，不全铺旧噪音；整版已审收起约束针对已进入待审展示的来源在拆分/关联中自动隐藏。旧判断不是 seen，完整资料仍可通过来源库和 discover 取回。

## 验证

- `Python314/python.exe -B -m unittest discover -s tests -p 'test_sweep*.py' -q`：79/79 通过。原 S 63 条 core/run/config/judge/docgate/归档/勾选回归迁入；摘要 fixture 改用共用原生解析器，不保留第二份生产 parser。
- 新增联合专项12条：部分关联与 seen 哈希、来源勾选独立、完整事项卡勾选往返、parked 不复活、同 timestamp 工具内容变化不得继承新/旧关闭、源丢失及恢复、不可读不宣称 gone、dry-run 无状态/输出/模型、模型阶段能写 thread 短锁、不可读页面停写、注册表能力注入、跨进程内核整轮锁。
- 新增真实 CLI4条，全部使用临时 PSTATE_HOME/输出：默认 run exit1/stdout空/失败信封/零副作用；status 的 json 与 format json 一致；eager help；用法 exit2 E_VALIDATION；未知来源库版本 E_IO 且字节不变；从真实 TOML 加载替换模型值，最终 argv 跟随；坏配置在任何业务写和模型前失败。
- 最终17个 Python 文件逐一经 esafe-code read 复核为 encrypted/CRLF，AST 解析通过。Core 无 open/print、无 summarize_rows。新源码均通过 esafe-code create/edit 先预演、相同命令 apply，修改旧 pstate.py 使用 plaintext SHA 必检；外部 worktree 写入均具体 require_escalated。
- 这只是后台/联合页切片通过。全仓独立验收、正式输出所有权/跨 state 同输出互斥、迁移/切换/回退、真实调度及真实模型尚由后续任务完成，不据本次假模型结果宣称已验证。

## 最终源码指纹

- `scripts/pstate.py`: `f9ef084f2b0a68891da0ed1c1637b3875ab8934134bbdb1475337e4b3df56314` (encrypted, crlf)
- `scripts/pstate_sweep/__init__.py`: `00d00990eb24ec1ef635d9a4d4811786f16f5db49767b9d4f2ab22341d3c5c1a` (encrypted, crlf)
- `scripts/pstate_sweep/adapters/__init__.py`: `00d00990eb24ec1ef635d9a4d4811786f16f5db49767b9d4f2ab22341d3c5c1a` (encrypted, crlf)
- `scripts/pstate_sweep/adapters/claude_judge.py`: `ad1e37286fadc92eb4250aaaf963bb6281d609ee6ef6345dd9dff8f1013925e9` (encrypted, crlf)
- `scripts/pstate_sweep/adapters/docgate.py`: `92aabd8ab1624c778d42c221fb2279f3ae0ef20df5f34571c6d58d21a2f88ea4` (encrypted, crlf)
- `scripts/pstate_sweep/adapters/schtask.py`: `be7223d88902e94b5fb6bbb038e29b836b80df8feed513b23ed77bdd14002485` (encrypted, crlf)
- `scripts/pstate_sweep/adapters/store.py`: `3fc016f25196b2cba7c26015c2a5f3e4fb66fd349196dccd35d4a7ff869ec868` (encrypted, crlf)
- `scripts/pstate_sweep/cli.py`: `d6e8c5a81e0f287acbfdeab0b27f9f7d13f740d1e716a3292f565103ebd08c4e` (encrypted, crlf)
- `scripts/pstate_sweep/config.py`: `ada45a909ae60665132bc6c71fcfda0761b6f0c405c83b7e08d8bf8fdad9ed13` (encrypted, crlf)
- `scripts/pstate_sweep/core.py`: `a7774bda0ec4c4fd1f03b355835fe1c081ba22ab9faf30c4e4dcc0959d59792c` (encrypted, crlf)
- `scripts/pstate_sweep/ports.py`: `82bbe124b00a299754f82d640ab9afa060cd51bdebe9e8d11f5742e2a156b44f` (encrypted, crlf)
- `scripts/pstate_sweep/runtime.py`: `a09af7741e3246c636b328f172ede2a2c13c0331ac25cd28730b655e3655feea` (encrypted, crlf)
- `tests/sweep_helpers.py`: `b394bfdb53645e1395d6d728ae721cdd2664dbc4a0a4c1fa3e1b96d99ff879b7` (encrypted, crlf)
- `tests/test_sweep_core.py`: `8b4e68d94704f20130f4ceac2c9c3c7f43c8b9311c79946f0b89a3a64f38bc29` (encrypted, crlf)
- `tests/test_sweep_run.py`: `38c08c8697ec9c73c222bef3d9f94b9792f121f25526d9961f4299140467837a` (encrypted, crlf)
- `tests/test_sweep_union.py`: `04e720c5a77a4df949243d2268799c5dd1087076f97558c89a097dce8f130f7b` (encrypted, crlf)
- `tests/test_sweep_cli.py`: `82087b4b9e362a280c690ac917db02ae067def349885333ff9ee02d61a032e6f` (encrypted, crlf)
