# merge-b1 migration 实施证据

日期：2026-10-23。P工作树：`C:/Users/huawei/Desktop/MyProjects/my-repos/project-state-sweep-merge`，基线4921f78。仅修改内部sweep CLI/runtime/store、新增output/migration及本段测试和本文；既有events/backend改动保留。没有修改S、HANDOFF主约定/机器区、正式库/页面/计划任务，没有读凭据、调用真实模型、全量扫描历史、提交、发布或合main。

## 显式接口

沿用 `scripts/pstate.py sweep`，所有命令支持 `--json` / `--format json`；错误仍为空stdout、stderr四键信封、0/1/2退出契约。

- `import-legacy --legacy-state PATH [--out DIR] [--dry-run]`：只读取指定旧state及指定输出目录中的主清单、已处理、带工具标记的全部周归档。不读取真实聊天原文，不建立thread或seen。
- `reconcile`：与import-legacy相同，重读最新页面勾选并幂等合入。
- `switch [--legacy-state PATH] [--out DIR] [--dry-run]`：显式切换唯一写者。初次已有页面必须指定旧state；新的空输出也必须显式switch。后续switch可沿用activation记住的旧state。
- `run [--out DIR] [--no-judge] [--dry-run] [--input-file PATH]`：实际运行仍由runtime.run_sweep承接，--out也不能绕过归属。roots/files输入沿用现有有界测试接口。
- `rollback [--out DIR] [--dry-run]`：先对账当前所有页面勾选，保存来源及当前页快照，再将新写者设为paused；保留当前页、所有thread新决定、seen及来源资料，不恢复旧写者，不用旧快照覆盖新数据。再次switch前向恢复。
- `sources`：全量查询sources.json，包含被AI完成/放弃/公司归属筛除的来源、失败、已关资料、完整旧原状态/页快照。status增加activation只读信息。

发布本身不导入、不启用、不注册/卸载计划任务。

## 永久保全与冲突

sources.json仍version1，事项生命周期仍仅threads.json schema1。新辅助资料包含：

- legacy_imports：按完整指定路径+原旧state+原页面内容hash去重，原state的未知字段、原summary/verdict/全部错误、closure/last_run和完整页面原文一起保留。原文文件不存在也不影响导入。迁移不制造新活动时间或伪造正文/activity hash。
- legacy_pages：原生成页的首次完整副本；没有重建的旧周归档在原路径保留，不因本轮layout不再引用而删除。
- legacy_closures、legacy_last_runs：旧资料追溯；closure_history是按活动版本保留必要的撤勾抑制信息，防重导旧closed快照复关。旧native@timestamp先按immutable legacy_marks规范化后再判是否已被当前写者处理。
- switch_snapshots：切换前来源、全部页面、activation和thread只读快照；rollback_snapshots保留回退时页面。禁止恢复快照覆盖新决定。

导入已有identity发生差异时保留当前记录，返回明确conflicts及对应legacy_import指针，原版本始终可查。重复相同资料导入不改变sources字节。AI判断仍只是来源分类，完成/放弃沿用旧默认隐藏，不转thread状态，不写seen。

没有原文/activity hash时继续保留旧native@timestamp关闭fallback；首次共享refresh获得真实hash才绑定旧别名。相同旧活动时间的未变来源不会重复模型；源消失只标不可用，原摘要与判断保留。

## 唯一写者与锁

`adapters.output.OutputLease(out_dir, state_dir, switching=False)`：

- 以out.resolve后的物理目录规范化路径hash选系统临时协调目录，持内核字节锁覆盖整轮扫描、模型、短thread快照与所有页写；不同PSTATE_HOME/同out和两个junction别名都用同一锁。状态目录原有RunLock继续包整轮。
- 输出根 `.pstate-sweep-owner.json` 与state目录 `activation.json` 都为version1，匹配 `state/out/token`。activation.phase只能switching/enabled/paused；run要求enabled。低层切换写页要求明确switching，不因取得switching lease就放行paused或未知phase。
- `store.write_page_if_changed/remove_generated` 在读取、比较、dry_run和真实写删前均调用output.guard，要求当前上下文lease、匹配owner/activation并校验生成子路径不能逃逸物理根或经过子symlink/junction。无lease直接调用也拒绝。root本身的合法junction可作为同一输出别名。
- 原子写使用同目录mkstemp独占新文件，不沿用可预置链接的固定name.tmp。
- 显式switch的dry-run使用受switch lease约束的preview_activation上下文；同一低层guard只在dry_run时接受虚拟记录，实际业务写不接受此预演记录。预演不写来源/启用/owner/页面，不调模型；仅协调锁可创建。

旧runner互斥：LegacyLock仅用O_CREAT|O_EXCL占指定旧state同目录run.lock（内容为PID纯文本）。已有未知锁直接busy，哪怕mtime极旧也不夺锁、不调用os.kill。switch最后在双锁内再次读取所有页面、保存完整快照及对账状态，先保存sources/phase=switching，再写owner和联合页，全部成功后设enabled。任何中断都保持默认拒绝run，显式switch可幂等前向重建。

成功切换保留旧lock占位，并记录其精确内容/mtime/size签名以便同一新写者重试；rollback也保留。**占位只保证当前切换临界区/已知预约互斥，不是永久安全闸**：未改造旧引擎2小时会按年龄夺锁；长期旧入口防写必须由下一capture-compat任务退役/转发入口保证，不能以这个PID文件冒充已完成兼容验收。

## 本段验证

最终命令：

```text
C:/Users/huawei/AppData/Local/Programs/Python/Python314/python.exe -B -m unittest discover -s tests -p test_sweep*.py -q
Ran 97 tests in 13.579s
OK
```

原后台/联合页/CLI79条保留并通过；新增migration18条，全部使用合成临时PSTATE_HOME/out/旧state：

- 完整旧状态未知字段、所有summary/verdict/错误/隐藏AI完成放弃公司/原主清单/孤立旧周归档/原时间保留；重复导入字节幂等；冲突当前不覆盖；不建thread/seen。
- 原文已消失且无activity hash时旧勾选仍以[x]留在周档；首次实际refresh绑定旧timestamp且不调模型；撤勾后重导native旧别名不重新关闭。
- 古老PID锁不被夺取；低层实际/预演写删都不能无lease，paused switching lease不能写；错误home/新out/损坏owner在扫描前失败。
- Windows真实目录junction作为root别名复用归属/跨进程同锁；真实archive junction逃逸拒绝；模型期间第二进程竞争同out失败，外层仅调用一次假模型。
- 写owner前及写page时注入中断，run均拒绝；再次switch前向恢复。
- 新thread切换后转parked，rollback前最后页面勾选得到对账，回退和恢复均不更改新thread字节/决定，也不恢复旧快照。
- 真实CLI闭环：默认run拒绝→import预演/实际→reconcile→switch预演/实际→run --no-judge预演/实际（显式files）→sources→rollback预演/实际→run拒绝→switch恢复；实际信封与stdout/stderr契约核对。
- 预置固定.tmp保持不变，验证mkstemp独占新路径。当前环境没有文件symlink特权且Esafenet拒绝硬链接，因此此项实际使用普通.tmp哨兵；不把文件symlink攻击称为已实测。目录junction边界是真实测试。

全部8个本段Python文件经esafe-code read/info及AST复核：encrypted、CRLF；所有创建使用require-encrypted，修改使用明文SHA预演后同命令apply。未改既有公司cwd任何文件。全仓独立验收/兼容壳/runner由后续任务负责。

## 最终指纹

- scripts/pstate_sweep/cli.py: 550410451ca93c716222b13b3da66b234cb01286d47ad73d012a85033b58072b
- scripts/pstate_sweep/runtime.py: d2512c384cad7148832faee213550c819eab7051b6dbcd6aaf3ceba0872daaf8
- scripts/pstate_sweep/adapters/store.py: ffa42d5c0bc6286b64b9daa3e9ccc944d7a479c2a4c22800c801925bff3676a0
- scripts/pstate_sweep/adapters/output.py: 5897fe91acfafc54ed0120dfcb34261e5122064f41f6543731dd7187871867c7
- scripts/pstate_sweep/migration.py: 1acf89f5912ac880a8d675b2220a8ddb32ea89564b68e3a0bb50d7e67dcf65e1
- tests/test_sweep_run.py: 62a6c5128ecd022be83c589c61c44c39233b46d9214471bc161be673044a7cd6
- tests/test_sweep_union.py: 3fe8129cdb377774eabd06f2c0b0eeeb316428480168b4ab658b0bcb1bddfd11
- tests/test_sweep_migration.py: f1fda5e15833be557af83142a15fcd70f878ad5d4850aa873f9e3de882985eb2
