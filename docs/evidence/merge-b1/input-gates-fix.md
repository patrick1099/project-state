# merge-b1 输入闸修复（首次尝试）

日期：2026-10-23。P工作树 `C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge`。根据独立验证 `validation.md` 的三个输入校验缺陷，按监工串行派工实施本轮最小修复。已读回当前HANDOFF全部有效约定及用户直接确认“确认，按这个范围完成实现、发布和切换”；本轮仍限b1隔离库/假模型，不发布或正式切换。

本轮只修改 `scripts/pstate_sweep/adapters/store.py`、`scripts/pstate_sweep/config.py`，新增 `tests/test_sweep_input_gates.py` 与本文。独立测试 `tests/test_sweep_independent.py` 原样保留，未改S/HANDOFF/入口文档、其他实施者改动、正式库/页面/调度、凭据。没有真实模型调用、提交、发布、合main或全量聊天扫描。

## 修复内容

- sources读写仍共用validate_state；新增validate_summary，在load以及实际/dry-run保存的业务写之前验证。旧格式一直存在的version键作为最低结构要求，值允许None，兼容原无时间摘要；新增review/activity等字段仍可缺失。已知文本、数值、布尔、written列表和recap字段仅在存在时按其既有类型验证。last_ts/recap.ts的浮点值拒绝nan/inf，human_count为非负整数且不接受bool。未知字段原样保留，不改schema、默认值或来源/事项语义。
- config.load在既有类型及范围约束之外，对claude_cmd/recap_model/resume_model要求strip后非空；timeout_s/budget_usd_per_call/interval_hours/idle_days/window_days拒绝非有限浮点值。保持原正值范围，idle_days/max_calls仍允许0，doc_gate等可选空文本仍保持原语义。配置仍是唯一策略来源，没有fallback。

三类缺陷均为首次产品修复尝试；包装脚本初始化的一次NameError发生在任何源码修改之前，纠正执行包装后使用同一修复内容，没有改变产品方案。

## 实测

固定解释器：`C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe -B`。

1. 原独立回归：

```text
-m unittest discover -s tests -p test_sweep_independent.py -v
Ran 5 tests in 2.212s
OK
```

原3个失败方法的19个subTest场景全部通过：坏来源summary在实际CLI返回E_IO/exit1前不改sources原字节；六个空/空白模型与命令配置在业务编排之前拒绝；十个nan/inf策略值在load拒绝。原模型预算timeout传递、能力注册拔插两方法仍过。

2. 受影响后台/来源/配置/迁移/联合页/冻结录入/隐藏runner及独立回归：

```text
-m unittest discover -s tests -p test_sweep*.py -q
Ran 121 tests in 30.751s
OK
```

无skip。新增InputGateTest四方法覆盖：

- version=None、nullable旧字段、可选新增字段缺失、任意未知嵌套资料保存读回保全。
- 无原文、无activity/review hash、无human_count/recap/last_ts的合法旧摘要仍可显式import，完整旧状态及未知字段保留，不建立thread/seen。
- 15种损坏已知字段分别在实际/dry-run保存前拒绝，write_atomic未调用，已有永久state字节不变。
- doc_gate空串、idle_days/max_calls=0仍通过；五策略字段-inf拒绝，四正值字段0仍拒绝。

测试中正常出现“worktree launcher不能注册”的E_VALIDATION信封属于原调度拒绝回归，不是测试失败。没有运行全仓新独立验收；后续由原独立验证者复核。

3. 加密、结构与差异：

本段两产品模块与新增测试均经esafe-code read取得明文SHA、dry-run预演后相同命令apply，新测试require-encrypted创建；最终逐文件read及AST解析通过，encrypted/CRLF。原独立测试SHA不变。git diff --check exit0，仅有已有HANDOFF的LF转CRLF提示，未触碰该文件。

## 最终指纹

- scripts/pstate_sweep/adapters/store.py：`36c266f46524a77a775a83a7792710f237feeaa6eb9ce1ece06d3e561c1dbdd8`
- scripts/pstate_sweep/config.py：`40cd50ef0f6e0a2d87e861ed41c5269e63d1b1cc1dc489422130b990c67f8959`
- tests/test_sweep_input_gates.py：`578134417bbefd9b7503e76cf0ae9d6163bc6a890fe48d7041b9ad0aa3b1c30c`
- 原独立tests/test_sweep_independent.py：`71b2d0f598f64ce0b5f922702b335cd0f92ba340e2787f5a699f3a31d8013b25`（未改）
