# 垂直切片 005：Case 恢复与恢复屏障

## 交付内容

- 新增 `RecoveryAdapter`，恢复结果必须携带结构化检查，Host 不接受 Agent 自报的“已恢复”；
- `restore_case` 固定执行定向反向恢复，若结果不是全部必检项 `match`，自动执行刷新并重放安全入口；
- `restored` 只在全部必检维度为 `match` 时成立；`uncertain`/`failed` 会使 Case 停止；
- `pending_requests` 或 `write_request` 无法确认时，Scan 进入 `failed`，对象阻断，正式结论不能继续；
- 定向和刷新尝试、每个检查项、原因均写入 ReverseCase；恢复 Operation 幂等，重试不重新执行；
- Host 重启时遗留的动作/恢复 `running` Operation 自动收束为 `result_unknown`，关联 Case 失效，Scan 失败；
- 恢复成功后对象重新回到 `eligible`，Case 进入 `completed`，越过恢复屏障。

实现入口：[recovery.py](../src/assayer_host/recovery.py)、[core.py](../src/assayer_host/core.py)。

默认恢复适配器不可用并 fail-closed；确定性适配器仅用于行为测试，尚不代表真实浏览器恢复能力。
