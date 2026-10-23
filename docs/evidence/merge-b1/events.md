# merge-b1 events 实施证据

日期：2026-10-23。工作树：`C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge`。基线：`4921f7858ab2`。只修改 `scripts/pstate_history` 与新增 `tests/test_history_events.py`；未修改 HANDOFF/机器区、未提交、未发布，未调用模型、未读凭据、未写正式状态或页面。

## 共用接口

`from pstate_history import load_sessions, PLATFORMS`

`load_sessions(options=None, include_excluded=False)` 返回 `{"sessions": [...], "coverage": {...}, "warnings": [...]}`。options沿用discover的roots/files。内部后台应直接读取session.summary，不能再次解析原始rows。需要保留headless元信息或纯图片用户输入时传 `include_excluded=True`，随后按 `session.interactive` 和注册能力选候选；subagents目录仍不枚举。交互discover及verify_source继续使用原过滤和原生来源核验。

`PLATFORMS` 仍是来源唯一注册表；`background` 能力为Claude True、Codex False。调用方从注册表能力派生后台名单，不能再写平台名称分支。新平台只加适配器及注册项。parse_all提供元信息保留入口；未提供该能力的扩展仍回用parse。

session字段：

- `platform/session/identity`：原生平台、原生ID、`platform:native-id`，不取文件名冒充原生ID。
- `version == review_version`：保持原pstate正文hash算法和原version兼容。seen、分页游标、来源核验沿用纯正文语义。
- `activity_version`：有效用户/助手及工具调用/结果的内容与时间hash；归档副本去重，工具输入改变即使时间相同也改变hash，recap更新自身不构成新对话活动。
- `activity_time == summary.version`：最后有效对话的原时间戳，供旧 `sid@timestamp` 勾选映射。它与hash是两种不同字段。
- `interactive/headless/paths`：入口判断及全部本次读取的来源路径。
- `summary`：内部完整后台摘要，见下文；discover不会输出该对象，避免recap/内部文本混入纯正文和绕过redact。

summary保留旧S全部所需字段：`cwd/branch/entrypoint/human_count/first_human/last_human/last_assistant/last_ts/version/recap/written`。recap保留 `{ts,text}` 并去原生suffix；`recap_fresh` 根据含工具活动的最后有效时间计算。原有裁剪上限仍为first_human 200、last_human/last_assistant 1500、written 300。

补充字段：`platform/native_session/identity/review_version/activity_version/interactive/headless/owner_hints`。`owner_hints={cwd,written}`仅线索，不判定事项归属。Claude另保留entrypoints、origin_kinds；Codex另保留native_sources（含subagent父ID）、thread_sources、originators、原生cwd/git分支。Codex后台能力没有扩大。

成功写路径：Claude仅识别已知Write/Edit/MultiEdit/NotebookEdit并匹配无is_error的tool_result；失败与无结果不收录。Codex仅识别已知编辑工具及apply_patch，必须匹配成功输出，未知/失败/在途不推断为写入；不从任意shell语句猜写路径。

## 验证

- `Python314/python.exe -B -m unittest discover -s tests -p 'test_history*.py' -v`：37/37通过，含原26条及新增11条。新增覆盖：新鲜recap后出现工具活动变陈旧、正文不变但activity改变、同时间工具内容变化、原生身份、cwd/branch/owner线索、成功/失败/在途写路径、归档副本去重、headless显式保留但discover排除、纯图片输入元信息保留、recap不制造对话活动、注册表扩展、内部recap/正文secret不会从discover元信息泄露。
- 实际CLI：以临时 `PSTATE_HOME`、临时JSONL和显式files运行 `scripts/pstate.py thread discover --input-file ... --json`，exit 0、ok true、1个 `claude:native-cli`、review_version与旧version相同、activity_version存在。对完整stdout拼接后验证：虚构secret已redact，内部recap及summary字段不出现。未扫描真实历史。
- 拔插自查：history调用方不存在另写平台名称分支；能力仅在core.PLATFORMS登记。假future注册项可被load_sessions及discover调用，后台能力可从注册表发现。
- 全部5个修改源码和新增测试经esafe-code read/info复核：ASCII兼容、CRLF、encrypted，无脱密；所有源码写入均为esafe-code hash预演后同命令apply，外部工作树写入使用具体require_escalated。
- 本任务仅定向历史回归和CLI入口通过；整仓集成、迁移、后台判断/调度由后续任务验证。

## 最终源码指纹

- `scripts/pstate_history/__init__.py`：`97515a623ab8273a1a0277d2b2b9227c85d93036da23f31bcdb921a6d78b417c` (encrypted, crlf)
- `scripts/pstate_history/common.py`：`9b92bf1ef788c0c0d5a49bd51254cff9fe286add47e149553f48f942504768be` (encrypted, crlf)
- `scripts/pstate_history/claude.py`：`ef22b315fa319e127bc88c0cf8cbf500f2568aa6dd3f3093f7bfedd265dab178` (encrypted, crlf)
- `scripts/pstate_history/codex.py`：`b4d2734d3ba4dbd421aa055cdcd7dd96b6708b1d0447768818937e67ff5ecb54` (encrypted, crlf)
- `scripts/pstate_history/core.py`：`261ac8746edca61e4db7c3cf309a1ac84584758c887d067c631914d2e2f37a39` (encrypted, crlf)
- `tests/test_history_events.py`：`661ea3c6956bdfe234493fa464620506b504f012e6c69dbe7f84b4b4200538e5` (encrypted, crlf)
