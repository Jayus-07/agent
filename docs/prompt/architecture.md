# Prompt Management Architecture

Prompt 是系统中的**可版本化、可测试、可发布、可回滚、可审计的配置资源**。本模块提供完整的 prompt 生命周期管理。

## Overview

```
backend/prompts/
  registry.py      # Prompt 静态元数据注册表
  renderer.py      # 模板渲染引擎 (str.format 语义)
  loader.py        # YAML defaults 加载 + DB seed
  service.py       # 核心服务 (snapshot/cache/DB/defaults 4 层读取)
  defaults/        # YAML 默认模板
```

## Prompt Registry

`PROMPT_REGISTRY` 是所有 prompt 的单一元数据源，在 `registry.py` 中以代码方式定义。

每个 prompt 由 `PromptSpec` 描述：

| 字段 | 说明 |
|------|------|
| `key` | 点分隔标识符，如 `rag.qa` |
| `name` | 中文显示名 |
| `category` | 分类：planner / rag / router / sql / memory / security / selection / evaluation 等 |
| `risk_level` | critical / high / medium / low |
| `variables` | `VarSpec` 元组，声明模板中使用的变量 |
| `code_controlled` | 为 True 时禁止 DB 编辑（仅 `security.input_guard`） |
| `required_substrings` | 渲染后必须包含的子串（用于 contract 校验） |
| `default_file` | 对应 YAML 文件名 |

注册项、风险等级和默认模板以 `PROMPT_REGISTRY` 与 `backend/prompts/defaults/` 为准；
`security.input_guard` 是代码控制项，不能通过数据库编辑。

## 3-Tier Read Path

读取 prompt 时按以下优先级回退：

```
1. In-process snapshot (dict, RLock 保护)
     ↓ miss
2. Two-tier cache (L1 memory 30s + L2 Redis TTL 300s)
     ↓ miss
3. PostgreSQL (PromptRepository)
     ↓ miss
4. YAML defaults (backend/prompts/defaults/*.yaml)
     ↓ miss
5. KeyError
```

### Snapshot

- `_snapshot: dict[str, _SnapshotEntry]` — 内存中的活跃版本缓存
- 由 `refresh_snapshot()` 从 DB 全量加载
- `publish()` / `rollback()` 后立即更新对应 key 的 snapshot entry
- `epoch` 计数器在每次 snapshot 变更时递增

### Sync Path

`render_sync()` 和 `get_template_sync()` 仅查询 snapshot + defaults，不触碰 DB：

```
snapshot → defaults → KeyError
```

这保证了同步调用路径（如 LangChain chain 内部）不会引入异步开销。

## Write Path

```
create_draft → publish → rollback
```

### create_draft

1. 校验 key 非 code-controlled
2. 在 DB 中创建新版本（status=draft）
3. 写入审计日志

### publish

1. 校验 key 非 code-controlled
2. 获取指定版本，运行 `PromptRenderer.validate()` 校验变量一致性
3. 在同一个 PostgreSQL 事务内更新 active_version、production alias、版本 status、审计与 reload epoch/outbox；Release Gate 发布时，release 状态 CAS 也在此事务内
4. 事务提交失败时，active 指针、release 状态、快照和缓存都保持旧值
5. 提交后更新本地 snapshot，再失效 `prompt:{key}` 缓存并广播已提交的 epoch
6. Redis 通知失败时保留 DB pending 事件，由请求入口的 DB epoch 检查补偿；不能把单进程刷新描述成所有 Worker 已刷新
7. 触发 reload hooks

只有已评测并审批的 Release Gate 可以跳过普通 Prompt 状态流水线。直接回滚只允许指向 `publish` 审计确认曾激活的版本；版本归档后仍可按该审计恢复，恢复会重新设为 `published`。没有发布记录的版本（包括仅经状态迁移标成 `published`/`archived` 的版本）不能通过 rollback 激活。

本地快照按同一数据库提交的 reload epoch 单调更新；热重载拉取也会记录该 epoch，迟到的旧发布提交不能覆盖已刷新的新版本。

### rollback

内部直接调用 `publish(key, version)`，复用相同的激活逻辑。

## Template Rendering

`PromptRenderer` 使用 `str.format()` 语义（非 Jinja2），通过 `string.Formatter` 解析模板变量。

### 渲染流程

1. `extract_variables(template)` — 提取 `{variable}` 占位符
2. 严格模式下校验：缺失变量 → `PromptRenderError`
3. `template.format(**safe_vars)` — 执行替换
4. 校验 `required_substrings`（如 `rag.qa` 必须包含 `<!--META`）

### 验证

`PromptRenderer.validate(template, spec)` 在 publish 时调用：

- spec 声明的变量必须在模板中出现
- 模板中的变量必须在 spec 中声明
- 返回错误列表（不抛异常）

## Permission Model

基于 `risk_level` + `role` 的权限矩阵：

| risk_level | read | draft | publish | rollback |
|------------|------|-------|---------|----------|
| critical | 所有人 | 禁止 | 禁止 | 禁止 |
| high | 所有人 | editor, admin | admin | admin |
| medium | 所有人 | editor, admin | editor, admin | editor, admin |
| low | 所有人 | editor, admin | editor, admin | editor, admin |

角色通过 `X-Operator-Role` header 传入（viewer / editor / admin）。

## YAML Defaults

每个非 code-controlled prompt 在 `backend/prompts/defaults/` 下有对应的 YAML 文件：

```yaml
key: rag.qa
template: |
  <!--META version=1-->
  基于以下上下文回答问题。
  上下文: {context}
  问题: {input}
```

`loader.py` 在启动时：
1. 解析所有 YAML 文件
2. 校验 key 与 registry 匹配
3. 校验模板变量与 spec 一致
4. 加载到 `PromptService._defaults` 作为最终回退

## Trace Integration

每次 `render()` / `render_sync()` 调用通过 ContextVar 记录 `{key, version, source}`。

`tracer.finish()` 在 trace 结束时调用 `collect_prompt_usage()` 将 prompt 使用记录附加到 span metadata，实现 prompt 版本与 trace 的关联。

## Evaluation Integration

`EvalReport.prompt_versions` 字段记录评估时活跃的 prompt 版本快照。

`baseline.diff()` 在检测到回归时，自动对比 baseline 与当前 prompt 版本差异，生成信息性警告帮助定位是否为 prompt 变更导致。

## Reload Hooks

业务模块可注册 reload hook，在 prompt 发布后执行自定义逻辑：

```python
prompt_service.register_reload_hook("rag.qa", lambda: rebuild_chain())
```

Hook 在 `publish()` 完成后同步触发，异常不会传播（仅 warning 日志）。

## 新开发的发布一致性与版本追踪门禁

这里补充**要求**，不把它误写成“所有调用点目前已通过”。现有实现的入口包括 `backend/prompts/service.py`、`backend/prompts/registry.py`、`backend/observability/prompt_trace.py`，以及项目的 Prompt 管理/评测 API。

- **注册权威**：新增或修改 prompt 从 `PROMPT_REGISTRY` 及现有 YAML 默认模板入手；先检查是否已有同一业务 key；Code-controlled 的安全 Prompt 不可通过普通数据库发布绕过。
- **发布与回滚**：Draft → 校验 → 评测/审批（按现有风险策略）→ active 版本切换 → snapshot/cache 刷新 → Trace 实际版本。active 指针、release 状态、审计与 DB reload epoch/outbox 同事务提交；进程快照和 Redis 通知只在提交后更新。发布事务失败不得改变 active 版本。发布或回滚后新请求应按已公布生效规则读取对应新 active；**不要求重启服务作为正常生效路径**。单进程成功不代表所有 Worker 实时一致。
- **请求一致性**：一次运行内是否固定版本由现有 `_prompt_pin_var`、Graph state 等实现确定，不应出现半个流程使用新版本、另外半个流程意外换旧版而没有可解释的版本边界。批量并发中的切换应覆盖跨 worker/缓存失效时序与失败恢复。
- **Trace 追踪**：调用级/节点级的实际 `prompt key@version` 必须能溯源；当前 trace-level 汇总写入见 `backend/observability/prompt_trace.py`，对同一 key 多次调用的版本归因是否完整需要实测，不可仅凭汇总数组断言每次调用版本。
- **评测**：发布候选与当前版本可比较相同评测样本的质量、成本、错误率和关键安全拒绝；样本缺失、评测失败、发布指针变化都要有拒绝/记录策略。回滚不等于清除历史审计、历史 Trace 或替换已完成任务的版本。
- **权限**：仅授权角色可以编辑/发布/回滚；只信任后端角色与资源判定，不以 `X-Operator-Role` 等客户端自报字段单独决定权限；现有 [API 说明](api.md) 的历史 Header 口径在改动前必须重新核对后端身份实现。
- **失败**：DB/cache/广播同步失败时按实际策略阻断/标记生效延迟、重试和排查，不静默宣称“全节点已热切换”。

| 编号 | 验收 |
| --- | --- |
| PG-01 | 草案、模板变量、必含安全片段、code-controlled 守卫均按约束校验 |
| PG-02 | 发布后新请求读取正确版本；无需重启，跨 Worker 通过 epoch 通知/轮询追平 |
| PG-03 | 在途请求 pin 与新请求 active 不串版本；并发发布/回滚使用 CAS，只有一个有效状态变更 |
| PG-04 | 一键回滚恢复指定历史 active；审计不丢、老请求/历史 Trace 不漂移 |
| PG-05 | Trace 能区分同 key 多次调用及节点实际版本，且不泄漏 prompt 敏感内容 |
| PG-06 | 未授权用户、跨租户和测试评测失败不会导致错误发布 |
| PG-07 | DB/Redis/Worker 同步故障有明确终态、可定位 Trace 和补救入口 |

相关现有定向测试在 `backend/tests/prompts/`、`backend/tests/observability/test_prompt_version_trace.py`。不将文档中的要求误认为所有实现已达到；差距按独立整改任务登记。
