# 垂直切片 020：发布级集成与故障注入（B09）

## 交付内容

- BrowserSession 对后端/浏览器异常统一转换为 `BROWSER_SESSION_FAILED`，Session 进入 failed；故意的 Host 拒绝（如跨 origin）不会误杀 Session。
- Playwright Chromium 启动、导航、Context 默认操作和截图均显式传入受限 timeout；导航超时、Context 关闭和动作/截图崩溃均不泄露 SDK 异常细节。
- Scan 在浏览器 Session 失败时转为 failed，Operation 返回结构化错误；Session Registry 关闭时即使一个 Context 清理失败也会尝试释放全部 Session。
- 新增确定性 `scripts/resilience_scan.py`：扫描空/裸异常、无界循环和外部浏览器调用缺少 timeout，输出 JSON 门禁结果，不生成 HTML。

## 故障注入验收

- 真实 Chromium 导航超时：Session failed，错误码为 `BROWSER_SESSION_FAILED`。
- 真实 Chromium Context 崩溃：后续访问被拒绝，错误消息不包含 `TargetClosed` 等 SDK 内部类名。
- 浏览器故障进入 HostCore：Scan failed，不能继续提交普通工具请求。
- Session Registry 关闭：首个关闭异常不会阻止其余 Session 清理。
- resilience scan：blocking findings 为 0；全量单元测试和真实 Chromium 回归通过。

## 运行命令

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python scripts/resilience_scan.py --root .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m unittest tests.test_browser_playwright -v
```

## 异常韧性评审结论

| 门禁 | 结论 | 证据 |
|---|---|---|
| 超时与重试 | 已确认；本项目没有业务 HTTP/DB/MQ 重试链，浏览器连接/导航/操作/截图均有显式上限 | `browser_readonly.py`、`browser_session.py`、真实超时测试 |
| 异常兜底 | 已确认；HostError 保留稳定错误码，未知传输异常记录服务端日志且不回显堆栈 | `core.py`、`transport.py` |
| 缺失异常处理 | 已确认；确定性扫描无阻断项，关闭清理和浏览器崩溃均有故障注入测试 | `scripts/resilience_scan.py`、`test_browser_session.py` |

重试边界：浏览器动作和有副作用写请求不自动重试；`result_unknown` 继续走 Operation/恢复屏障。B09 不新增业务重试，避免重复副作用。
