# 0.9.0 hooks 验证

## 行为回归

原子多路径登记、跨 worktree 冲突、同 PID 不同线程、失败仅回滚本次新增、旧占用保留、坏状态拒绝并保留原件、shell 完整命令只读判定、显式入口及加密探测都有行为测试。最终全量 283 项通过（345.626 秒）；结束回执的 3 项新增回归包括同 PID 释放落盘、相同线程恢复和坏状态保留。

## 原生宿主

2026-11-03，Windows Codex 0.164.0-alpha.11.1；临时 CODEX_HOME、个人测试 git 仓和 PSTATE_HOME；保留 :workspace 权限，通过正常 commandApproval 仅批准 fixture 的固定 Set-Content 命令。本机 Responses fixture 驱动实际工具分发，无外部模型调用。

同 app-server PID 两个线程：A 写 shared.txt 为 A，成功 Post 结算 written；B 写同文件被 Pre 拒绝，内容仍 A；归档 A 后 SessionEnd 回执真实落盘，B 写成 B，原始 locks.json 不再有 A session/claim。归档 B 后有效状态 sessions/claims 均为空。两个结束钩子耗时 2298/2606 ms。去除本机路径和身份的回执见 `hooks-native-receipt.json`。不是仅凭 hook completed 推断释放；同时核对回执、文件内容、原始锁和共享读取结果。

原生 exec_command 被宿主规范为 Bash，输入仅 command，实际 workdir 丢失。可识别但无法定位的相对写入在个人仓拒绝，提示绝对路径、绝对 cd/root 或显式入口。当前线程之外隐藏 workdir 的跨仓目标不在 hook 可观测范围内。

PostToolUse 的 shell 回应为纯 stdout；失败/取消审批并非一定有后置回执，未结算在途占用保守保留到结束/接手。当前 schema 无 PostToolUseFailure。apply_patch 的输入适配有回归，但本轮宿主成功 patch 分发受阻，未冒称真实成功验收。

当前宿主把 SessionEnd 同步执行限制为 3 秒，timeout=30 与 async=true 均不能改变它；未保留无效 async。曾观察到入口未落回执便超时。最终 Windows 走短 PowerShell 回执，非 Windows 走 stdlib Python；没有常驻任务。缺回执时不得释放，进程 PID/ctime 判死或显式 release/take 仍可清理。实际死进程旧锁验收：两 session 判死，清理后 sessions/claims 为零。

## 磁盘加密态

真实 PowerShell 观测受控 fixture：Python 新建的源码磁盘头检测为 encrypted；用非白名单进程写成明文后 verify 精确报告该路径。测试结束删除临时 fixture。run 只检查声明的具体文件，不扫描公司仓，不声称事后检查能撤销内容写入。注入探测器回归与真实磁盘验收分别记录。

## 发布边界

候选 worktree 产物，不代表已安装、被信任或覆盖旧会话。正式发布、hooks/list 和信任读回由主会话统一执行。
