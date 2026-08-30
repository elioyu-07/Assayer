# 垂直切片 016：真实浏览器恢复屏障

## 交付内容

- 新增 `BrowserRecoveryAdapter`，与页面、对象、动作和网络守卫共享同一 `BrowserSession` 与 Context。
- `begin_case` 建立 Host 内存中的恢复基线：规范化 URL/route、页面层级、对象 fingerprint，以及固定恢复探针采集的 Tab、overlay、控件和局部视觉状态；原始 DOM 和控件值不落账。
- 成功的 `expand`/`collapse` 动作由 Host 自动记录固定 inverse；`focus`/`scroll` 记录 `noop`，其余动作使用 `refresh_only`。Agent 不能提交 selector、脚本或任意反向动作。
- 定向恢复按 Case 动作逆序执行 inverse，之后重新绑定对象并检查九个恢复维度：`url_route`、`page_layer`、`active_tab`、`overlay_state`、`control_state`、`object_identity`、`pending_requests`、`write_request`、`local_visual`。
- 定向结果为 `uncertain` 或 `failed` 时自动执行同源基线 URL 刷新回放，并再次生成完整检查记录。只有全部维度 `match` 才返回 `restored`。
- 网络追踪通过 Playwright `requestfinished/requestfailed` 证明 pending 请求收束；没有事件追踪能力时恢复不会乐观判定成功。

## 验收场景

1. 真实 Chromium 展开筛选区后执行 `collapse` inverse，九个检查维度全部 `match`，Case 越过恢复屏障。
2. 动作改变 route，定向检查失败，随后 `refresh_replay` 回到基线 URL 并成功恢复。
3. 现有确定性测试继续覆盖 unknown pending、持久化写入、对象歧义、浏览器恢复失败和 Host 重启收束。

## 安装与验证

```bash
python3 -m pip install -e '.[browser]'
python3 -m playwright install chromium --no-shell
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest tests.test_browser_playwright -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -q
```

## 边界

- 当前恢复探针只对通用页面层、默认 Tab、overlay、固定筛选区域控件和局部几何状态做 Host 可验证比较；复杂业务控件必须增加版本化探针和回归样本。
- B06 不实现 Evidence 或截图；后续 B07a/B07b 分别实现结构化 Evidence 和真实对象级截图。B07c 自动脱敏未完成前，真实 `issue_found` 仍受脱敏门禁禁止。
- 恢复通过不等于规则结论通过；正式 Issue 仍受 Evidence、Screenshot、Decision 和完整生命周期门禁约束。
