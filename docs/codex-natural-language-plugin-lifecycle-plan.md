# Codex 自然语言插件全生命周期计划

| 元数据 | 内容 |
|---|---|
| 文档版本 | 1.0.0 |
| 日期 | 2026-09-06 |
| 状态 | 待实施计划 |
| Owner | Assayer Maintainers |
| 主要客户端 | Codex CLI |

## 1. 目标

Assayer 最终需要提供一条连续的 Codex 自然语言插件旅程：用户不需要编写
Assayer CLI 命令、不需要手动配置 MCP、不需要接触内部协议字段，也不需要
手动重启进程，就可以完成以下操作：

```text
安装 Assayer
安装 ass-spec
用 ass-spec 审查 spec.md
查看插件状态
升级 ass-spec
回滚 ass-spec
降级 ass-spec
卸载 ass-spec
```

所有有副作用的操作都必须先生成可读的确定性计划，获得用户明确确认后，
再由 Host 的生命周期代码执行。自然语言是用户入口，不应成为信任边界；
插件包校验、版本约束、安装状态、运行时注册和结果持久化仍由 Assayer
确定性代码负责。

## 2. 生命周期边界

Assayer 当前包含两类生命周期对象，必须保持边界清晰。

### 2.1 Assayer Codex Plugin

Assayer 本身是 Codex Marketplace 中的产品插件，包含 Skill、MCP launcher、
Python runtime、规则和 schemas。首次安装发生在 Assayer 尚未存在之前，
因此应由 Codex Marketplace 或 Codex 原生插件发现能力完成。

Assayer 需要负责：

- 提供可验证的 Marketplace release；
- 提供 `.codex-plugin/plugin.json` 和 `.mcp.json`；
- 启动并校验私有 MCP runtime；
- 通过 Codex 的自然语言入口引导后续操作；
- 提供 Assayer 自身的 upgrade、rollback、uninstall 生命周期。

### 2.2 Assayer domain plugin

`ass-spec`、`test-minimal` 以及未来的外部审计插件由 Assayer 自己的持久化
plugin store 管理。它们必须通过 catalog、checksum、package conformance、
isolated installation conformance 和 registration validation 后，才能进入
可运行状态。

domain plugin 的用户入口包括：

```text
安装、查看、升级、降级、回滚、卸载插件
运行插件的指定 Check
继续被中断的插件 Run
解释插件产生的结构化结果
```

## 3. 目标用户旅程

### 3.1 首次安装

```text
用户在 Codex 中发现并安装 Assayer Marketplace Plugin
    -> Codex 启用 Assayer Skills
    -> Codex 按 .mcp.json 启动 Assayer MCP
    -> launcher 校验 Python、平台、bundle 和 wheel 完整性
    -> Assayer MCP 对 Codex 暴露插件管理和插件运行工具
```

首次安装的产品承诺是“用户不需要手动配置 Assayer”，但 Python 和 Chromium
等外部前置依赖仍必须被明确报告。缺少依赖时必须返回可理解的失败原因，
不能静默降级到 smoke 或其他非正式流程。

### 3.2 安装并使用外部插件

```text
用户：安装 ass-spec
Codex：查询 catalog，展示版本、来源、checksum 和安装门槛
用户：确认
Assayer：下载、校验、隔离安装、注册并写入 durable store
Assayer：刷新当前 MCP 的 plugin registry
用户：用 ass-spec 审查这个 spec.md
Codex：启动 Run，驱动检查、语义审查、结果提交和结果解释
```

安装完成后，当前 MCP 连接必须能够立即发现并运行新插件。用户不应被要求
手动重启 Codex 或 MCP。

### 3.3 运行插件

用户只提供业务意图和业务输入，例如：

```text
用 ass-spec 审查这个 spec.md，告诉我需求是否完整、是否存在歧义。
```

Codex 负责识别插件、Check 和 scope；Assayer Host 负责 Run ID、版本、证据、
checkpoint、恢复、coverage 和 canonical result。无法唯一确定插件、Check
或 scope 时必须澄清，不能猜测。

## 4. 当前基线和已知断点

当前代码已经有以下基础：

- `PluginInstallationStore` 提供持久化安装索引和原子写入；
- `PluginLifecycleManager` 提供 install、upgrade、downgrade、rollback 和
  uninstall；
- lifecycle MCP 已暴露 list、info、install、upgrade、downgrade、rollback
  和 uninstall；
- `assayer-plugin-lifecycle` Skill 已定义自然语言到 lifecycle tool 的映射；
- `assayer-plugin` Skill 已定义 interactive Run、semantic review、恢复和
  结果分页流程；
- `store_backed_plugin_registry` 能在新进程启动时合并 built-in 和 store
  中的插件；
- Assayer 自身已有 Codex command adapter 和产品生命周期规划代码。

当前需要优先解决的断点：

1. domain plugin 安装后，当前 MCP 进程里的 interactive registry 不会自动
   刷新，安装成功后不能可靠地立即运行新插件。
2. lifecycle MCP 声明了安装 `version` 参数，但当前实现没有把它传入实际
   catalog 版本解析。
3. domain plugin mutation 的确认主要依赖 Skill 约束，MCP transport 没有
   强制性的 plan token 和 confirmed gate。
4. Assayer 产品插件生命周期和 domain plugin 生命周期是两套入口，没有被
   统一成一套用户可理解的自然语言路由。
5. README 当前仍将独立插件生态和真实 clean-Codex lifecycle gate 标记为
   未完全完成，缺少完整的真实 Codex 验收证据。

## 5. 实施计划

## 5.1 P0：闭合安装后立即运行

### 任务 P0-1：增加 plugin registry 动态刷新

修改运行侧 MCP transport，使其能够根据 plugin store 的变化重新加载 registry。

建议实现：

- transport 持有 `store_root`；
- 在 MCP 启动时记录 `index.json` 的 digest 或版本信息；
- 每次 `start_plugin_run` 前检查 store 是否发生变化；
- store 发生变化且没有 active Run 时，重新构建 store-backed registry；
- lifecycle 操作成功后主动触发 refresh；
- registry refresh 失败时 fail closed，并返回稳定错误码；
- 当前 active Run 使用的 registry 在 Run 结束前保持不变。

验收：

```text
同一个 MCP 进程：install_plugin -> list_plugins -> start_plugin_run
```

上述三个调用必须连续成功，且第二步能看到新插件，第三步能真正加载新插件。

### 任务 P0-2：处理 registry refresh 与 active Run 的互斥

生命周期 mutation 必须检查活动 Run：

- Run 运行中不能卸载正在使用的插件；
- Run 运行中不能切换其使用的插件版本；
- terminal Run 完成后允许刷新 registry；
- MCP 重启后仍按 `resume_plugin_run` 的明确规则恢复；
- 一个 Run 不能中途切换到另一个插件版本。

### 任务 P0-3：修复 MCP 的指定版本安装

确保以下调用真正安装指定版本：

```text
install_plugin(plugin="ass-spec", version="0.9.0")
```

需要贯通：

```text
MCP arguments.version
    -> IntentStep.version
    -> add_from_catalog(version=...)
    -> resolve_version(...)
```

同时覆盖版本不存在、版本格式非法、已安装版本冲突和指定版本 checksum
不匹配等失败场景。

## 5.2 P0：把确认机制下沉到 Host/MCP

### 任务 P0-4：统一 mutation plan 和 execution

install、upgrade、downgrade、rollback、uninstall 都改为两阶段流程：

```text
plan_plugin_change
    -> 展示确定性计划

execute_plugin_change
    -> 只接受确认后的有效 plan token
```

计划至少包含：

- operation；
- pluginId；
- 当前版本和目标版本；
- 当前状态和目标状态；
- catalog 来源；
- checksum；
- conformance gate 结果；
- active Run guard；
- 是否需要用户确认；
- 执行失败后的可恢复性。

plan token 必须：

- 一次性使用；
- 有 TTL；
- 绑定 plugin、版本、operation 和当前安装状态；
- catalog 或 store 状态变化后失效；
- 不能重放或跨插件复用。

### 任务 P0-5：统一生命周期公开状态

对 Codex 暴露稳定的状态集合：

```text
absent
available
installed
upgradable
dirty
blocked
completed
aborted
failed
```

每个 mutation 结果必须说明：

- 做了什么；
- 目标插件和版本；
- 当前最终状态；
- previousVersion 和 activeVersion；
- 错误码和用户可读原因；
- 是否可以安全重试；
- 下一步动作。

dirty 插件必须保持“可见但不可运行”：`info` 显示原因，`run` 返回
`PLUGIN_DIRTY`，repair/reinstall 或 uninstall 不得留下半写入状态。

## 5.3 P1：统一自然语言路由

### 任务 P1-1：增加统一 Assayer lifecycle router

统一区分以下意图：

```text
安装 Assayer
安装 ass-spec
用 ass-spec 审查 spec.md
升级 Assayer
升级 ass-spec
回滚 Assayer
回滚 ass-spec
```

路由规则：

- Assayer 自身使用 Codex Marketplace/product lifecycle；
- domain plugin 使用 Assayer plugin lifecycle；
- plugin usage 使用 `assayer-plugin` Run workflow；
- URL 审查使用 `assayer-audit` workflow；
- 不确定对象时先读取状态或向用户澄清。

### 任务 P1-2：支持“安装并使用”的连续任务

当用户要求使用尚未安装的插件时，Codex 应执行：

```text
查询安装状态
    -> 生成安装计划
    -> 等待确认
    -> 安装插件
    -> 刷新 registry
    -> 校验 pluginId 和 Check
    -> 启动 Run
```

安装失败、quarantine、版本冲突或 registry refresh 失败时必须停止，不能
自动继续运行。

### 任务 P1-3：将 `plugin_intent.py` 定位为确定性校验层

自然语言的复杂理解由 Codex Agent 完成；Assayer 提供结构化 intent schema，
并验证以下内容：

- operation 是否允许；
- pluginId 是否存在且唯一；
- version 是否符合 semver；
- scope 是否存在并符合插件 schema；
- 目标版本是否满足 upgrade/downgrade 方向；
- 当前状态是否允许执行。

无法确定时必须返回澄清错误，而不是猜测。

## 5.4 P1：完善 Assayer 自身产品生命周期

### 任务 P1-4：明确首次安装由 Codex Marketplace 负责

首次安装不能由尚未安装的 Assayer Skill 自己完成。需要把职责固定为：

- Codex Marketplace 负责首次发现和安装 Assayer；
- Assayer bundle 负责 launcher、runtime 和 MCP 完整性；
- 缺少 Python、Chromium 或平台不匹配时返回明确错误；
- 安装完成后 Skill 自动引导用户进入 domain plugin lifecycle。

### 任务 P1-5：统一 Assayer 产品和 domain plugin 状态语义

需要定义 Assayer 被卸载、重新安装、降级时 domain plugin store 的行为。
建议规则：

- 卸载 Assayer 时保留 domain plugin store，不自动删除用户插件；
- 重新安装 Assayer 时重新校验现有 domain plugins；
- platform API 不兼容的插件进入 dirty；
- dirty 插件可查看、修复或删除，但不可运行；
- Assayer 版本切换不能静默删除 domain plugin。

## 5.5 P1：完善 catalog 和插件信任链

### 任务 P1-6：强化 catalog 安装验证

安装前后需要验证：

- catalog schema；
- HTTPS 或允许的 catalog 来源；
- wheel SHA-256；
- release descriptor；
- wheel metadata 与 plugin identity；
- plugin version 与 manifest version；
- platform API compatibility；
- registration source 是否位于 package root 内；
- package conformance 和 isolated installation conformance。

安装计划中应向用户展示来源、版本和 checksum 摘要。Alpha 阶段如果还没有
签名能力，只能宣称 checksum integrity，不能宣称 publisher authenticity。

### 任务 P1-7：固定插件依赖和 runtime 策略

建议 Alpha 阶段要求插件 distribution self-contained：

- 不修改 Assayer 私有 runtime；
- 不允许插件安装时执行任意 pip 逻辑；
- 依赖缺失时进入 dirty；
- 插件间依赖冲突 fail closed；
- 插件只能写入 Assayer plugin store 和受控运行输出目录。

## 5.6 P2：提升插件使用体验

### 任务 P2-1：从 pluginId/checkId 驱动升级为目标驱动

用户可以表达业务目标：

```text
用 ass-spec 审查这个 spec.md，重点看需求完整性和歧义。
```

Codex 根据 plugin manifest、Check 描述和 scope schema 选择 Check；有多个
匹配项时展示选择计划或请求澄清。

### 任务 P2-2：统一运行前检查

每次 Run 前执行：

1. 查询当前 registry 和 store；
2. 检查插件是否已安装；
3. 检查是否 dirty；
4. 检查 Check 是否存在；
5. 检查 scope 是否存在并符合 schema；
6. 检查是否存在 active Run；
7. 再调用 `start_plugin_run`。

### 任务 P2-3：统一结果解释

Codex 完成 Run 后必须告诉用户：

- 使用了哪个插件和版本；
- 执行了哪个 Check；
- 检查了什么 scope；
- 结果是 completed、partial 还是 failed；
- 已确认的问题；
- 未覆盖或仍需审查的内容；
- 输出报告在哪里；
- 下一步应该怎么做。

## 6. 测试计划

### 6.1 单元测试

覆盖以下行为：

- MCP 指定版本安装；
- catalog 版本解析；
- install、upgrade、downgrade、rollback、uninstall 状态转换；
- dirty quarantine；
- checksum 和 registration 失败；
- registry 动态刷新；
- active Run 与 mutation 互斥；
- plan token 过期、重放和状态失效；
- 安装后当前 MCP 进程立即运行；
- 卸载后当前 MCP 进程立即拒绝运行；
- Assayer 产品 lifecycle 与 domain lifecycle 不串扰。

### 6.2 MCP 集成测试

启动真实 `assayer-mcp`，在同一个 stdio 连接中按以下顺序调用（mutation 一律
走 plan -> confirm -> execute 两阶段）：

```text
list_plugins
plan_plugin_change(install) -> execute_plugin_change(token, confirmed=true)
list_plugins
start_plugin_run
finish_plugin_run
plan_plugin_change(uninstall) -> execute_plugin_change(token, confirmed=true)
list_plugins
start_plugin_run
```

必须验证：

- 安装成功后同一 MCP 连接能看到插件；
- 安装成功后同一 MCP 连接能运行插件；
- 卸载成功后同一 MCP 连接不再允许运行插件；
- 失败操作不会破坏旧版本或留下不可解释的 active state。

该流程已固化为自动化测试 `tests/test_mcp_stdio_integration.py`（通过官方 MCP
SDK 拉起 `assayer-mcp` 子进程并在单条 stdio 连接中完成安装、运行、卸载、
再次运行被拒绝的完整验证）。

### 6.3 Clean Codex 验收

在干净 Codex profile 中验证完整用户旅程：

```text
安装 Assayer
    -> MCP 首次启动
    -> 自然语言安装 ass-spec
    -> 用户确认
    -> 同一任务立即使用 ass-spec
    -> 查看结果
    -> 升级 ass-spec
    -> 回滚 ass-spec
    -> 卸载 ass-spec
    -> 重启 Codex 后验证状态一致
```

验收记录至少包含：

- Codex 版本；
- Assayer 版本；
- plugin 版本；
- OS、架构和 Python 版本；
- MCP 启动结果；
- 用户自然语言请求；
- 实际工具调用；
- lifecycle plan 和 confirmation 结果；
- store 最终状态；
- 失败、恢复和重试结果。

## 7. 交付顺序

建议按以下顺序实施：

1. 冻结端到端验收场景和状态模型。
2. 修复 MCP 指定版本参数传递。
3. 实现 MCP registry 动态刷新。
4. 增加 active Run 与 lifecycle mutation 互斥。
5. 将确认机制下沉为 plan token。
6. 补齐 install -> refresh -> run MCP 集成测试。
7. 统一 lifecycle、audit 和 plugin usage Skill 路由。
8. 统一 Assayer 产品插件和 domain plugin 的自然语言入口。
9. 完善 catalog、来源、checksum 和兼容性展示。
10. 执行 clean Codex acceptance。
11. 最后再推进签名、复杂依赖管理、Marketplace UI 和更大插件生态。

## 8. 完成标准

本计划完成必须同时满足以下条件：

- 用户可通过 Codex Marketplace 安装并启用 Assayer；
- Assayer MCP 能在无手工配置的情况下启动并完成 runtime 校验；
- 用户可用自然语言安装、查看、升级、降级、回滚和卸载 domain plugin；
- 所有 mutation 都先生成计划并等待明确确认；
- 指定版本安装语义真实生效；
- 安装完成后无需重启 MCP 即可使用插件；
- 使用流程只要求用户提供自然语言意图和业务 scope；
- 插件运行遵循统一的 Run、evidence、semantic review、recovery 和 result
  workflow；
- dirty、版本不兼容、checksum 失败和 catalog 不可用时都 fail closed；
- Assayer 升级、回滚、卸载不会破坏 domain plugin store；
- 同一 MCP 连接内完成 install -> use -> upgrade/rollback -> uninstall；
- clean Codex 验收证据已经记录，且不依赖 repository 内部环境；
- 文档、Skill、MCP schema、实现和测试对生命周期状态保持一致。

在这些条件全部满足前，Assayer 只能宣称“具备大部分插件生命周期底层能力
和自然语言原型”，不能宣称已经完成“从安装到使用的全程 Codex 自然语言
生命周期体验”。
