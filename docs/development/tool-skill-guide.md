# Agent、Skill、Tool、Workflow 与 MCP 开发规范

本文件是新增和修改这些运行资产的主要开发入口。实际接线以当前代码和声明文件为准；禁止照搬已过期文档中的旧注册步骤或代码模板。

## 职责边界

| 资产 | 责任 | 事实源或入口 |
| --- | --- | --- |
| Domain Agent / Domain Graph | 承载一个业务域的状态、路由和结果契约 | backend/domains/__init__.py、域内注册模块与图构建器 |
| Capability | 表达可路由、可调度的业务能力及其治理元数据 | backend/orchestration/router/capabilities.yaml |
| Skill | 组合业务规则并在 Runtime 中执行 Capability | backend/skills/、backend/skills/registry.py |
| Tool | 封装单一外部操作或原子动作 | backend/tools/、backend/tools/tool_registry.py |
| Workflow | 执行预定义的多步流程 | backend/orchestration/workflows/ |
| MCP | 暴露平台 Tool，并消费外部 MCP 数据源 | mcp_servers/servers/、mcp_servers/protocol_app.py、backend/app/api/routes/mcp.py；外部客户端为 backend/infra/mcp_client.py |

依赖方向遵循现有分层。Tool 不承载跨业务流程；MCP 不复制 Tool 的业务实现；新增资产优先扩展现有注册和路由机制。

## 新增或修改流程

1. 先查找现有 Skill、Tool、Domain Graph、Workflow 与契约，确认没有重复能力。
2. Tool 保持原子职责，定义明确参数与结果，按现有方式注册；不要把 Tool 放在 Skill 目录，也不要从编排代码绕过 Tool 执行入口。
3. Skill 复用 BaseSkill 和当前能力执行适配器。修改参数、输出类型或 Capability 时，同步核对调用方和实际校验逻辑。
4. Capability 及其风险、操作、参数 Schema、确认策略与负责人维护在现有 manifest；`owner`（负责人团队）在 domains 与 capabilities 每条必填，缺失或空白启动即炸（manifest fail-fast），经 `/capabilities` 与管理端 /skills 页「负责人」列对账展示。派生注册表和 lock 文件通过仓库生成器更新。
5. Workflow 复用现有 workflow registry；Domain Graph 使用现有 Domain Registry 并遵守 Router 预过滤接线；不要在主图 builder 手写自动发现节点。
6. 平台 MCP Server 在 mcp_servers/servers/ 实现并由 register_all() 登记，启动入口见 backend/app/server.py；参数 Schema 从 Tool 派生，适配器见 mcp_servers/schema_adapter.py。外部 MCP 消费走 backend/infra/mcp_client.py。两条通路都复用既有 Tool、鉴权和错误契约，不创建第二套 Tool 注册表。
7. 为新增或变化的失败、拒绝、降级和输出行为添加针对性断言，选择范围见 testing-guide.md。

## 动态 MCP 工具（MCP 能力中心）

外部 MCP Server 的接入走**配置级集成**（ADR-0011），不改代码、不重新部署：

- 连接、凭据引用、工具快照、审核状态与产品授权的事实源是数据库
  （迁移 114 的 `ai.mcp_*` 表）；进程内快照每 15s 轮询刷新（与 LLM
  registry 同契约），管理端写操作后本进程立即生效。
- 撤权/停用/漂移的时效：写操作工具（operation != read）在出网前由
  `mcp_registry.store.verify_write_authorization()` 按事实源强一致复核，
  即时拦截（fail-closed）；读操作走快照，其他 worker 最迟 15s 收敛。
- 新发现的工具默认 `pending`（不可执行）；审核通过（approve 时落库治理
  配置与 ≥2 条路由示例）并授权给产品入口（agent / customer_service）
  后，才进入 `all_tool_specs()` 与 `effective_manifest()` 的合并视图，
  经唯一的 `mcp_dynamic_skill` 节点按既有 GovernanceRuntime 执行。
- 上游 Schema/描述变化 → `schema_changed=true`，工具立即退出运行时，
  重新审核后才恢复（审核写入带 content_hash CAS，发现并发改写时拒绝
  旧审核）；上游消失 → `retired`；停用连接/工具对写调用即时拦截。
- `capabilities.yaml` 只静态声明一次 `mcp` 粗域；**禁止**把动态工具逐个
  登记进 yaml 或环境变量。上游工具名/描述/Schema 全部是不可信输入，
  进入 Planner prompt 的描述由审核者确认并截断。
- 管理端入口：`/api/admin/mcp/*`（admin 门禁 + 幂等键 + SSRF 校验 +
  security_events 审计）；前端 `/mcp` 页面（连接 → 发现 → 审核 → 授权 →
  只读测试调用）。写操作工具不能通过测试调用裸测，必须走真实业务链的
  确认/审批/幂等机制。

## 域清单协议（domain capability inventory，G5）

域图域（customer_service / travel / 未来新域）对外提供**意图级能力目录**的统一形状，
供运营视图与治理对账消费；主图不走本协议——主图 capability 清单即主图目录
（`GET /capabilities`，B13 形状），两者映射为「主图条目 = capability，域条目 = 意图」。

约定（新域照抄，禁止私搭平行形状）：

- 端点：`GET /<domain>/capabilities/inventory?range=today|d7|d30`，全部只读。
- 响应形状：`{domain, range, total, stats_available, intents:[{intent,
  description, stats:{calls, handoff_rate, unresolved}}], totals?}`；
  `range` 非法值回退 `d7`（静态清单无窗口语义时可整段降级）。
- **意图清单与描述是代码唯一事实源**：与意图枚举/路由定义同文件声明
  （参照 `backend/travel/core/intent.py::INTENT_DESCRIPTIONS`），禁止
  端点内手写第二份清单或从前端透传。
- **权限**：跨坐席运营视图 ≥ supervisor 口径（cs 域为 supervisor-only，
  `AgentContext.is_supervisor` 判定；无运营角色的域对齐该域 REST 只读
  身份闸）；租户闸必须收口——统计 join 不得跨租户。
- **统计诚实降级**：域无数据面（无意图打点/dashboard）时
  `stats_available: false` + `stats_note` 说明，`calls=0 / handoff_rate=null /
  unresolved=0`——不伪造数值、不假装有统计。补齐数据面后翻正
  `stats_available` 并保持形状不变（契约只追加）。
- 参照实现：cs 域 `backend/app/api/routes/cs_inventory.py`（运行时统计，
  G4）、travel 域 `backend/app/api/routes/travel_inventory.py`（静态清单 +
  降级标注，G5）；管理端 `/capability-inventory` 页消费。

## Tool 与 Skill 输出契约

每个 Skill 都要显式声明 `text` 或 `structured` 输出类型，并与其实际 Tool 结果保持一致：

- `text` 用于 Markdown/纯文本结果，直接作为模型可读内容传递，不包装成 JSON。
- `structured` 用于程序消费的 JSON 结果。成功封套为 `{"status":"success","data":...}`，失败封套为 `{"status":"failed","error":...}`。
- Skill/Workflow 边界负责解包和失败判定。成功只向 Reporter 传业务数据；失败必须保留失败语义，不能作为成功内容展示。

封套解析统一使用 `backend/shared/tool_envelope.py`；Skill 与 Workflow 的边界分别见 `backend/skills/base.py`、`backend/orchestration/workflow/skill_adapter.py`。契约守卫见 `backend/tests/skills/test_output_type_declarations.py` 和 `backend/tests/skills/test_tool_contract_boundary.py`。

## Tool Governance 与副作用

生产 Tool 执行必须经过当前 Governance Runtime 与安全执行器。Schema 校验、Capability/Domain 适用范围、请求预算、限流/熔断、去重以及用户确认由现有治理链负责；不得通过直接调用函数、模型输出或 MCP 路径绕过。

涉及写操作时，遵循当前操作预览、确认/审批和幂等契约。用户、租户和操作者身份只取自服务端验证后的请求上下文。任何 Tool 不得把授权、审批或副作用保护交给模型自行判断。

## 数据、模型与错误处理

- 数据访问执行资源级授权和租户隔离；SQL 查询遵循 SQL 域安全规范。
- Prompt 发布版本、模型调用和 Token/成本归因使用现有统一链路。
- 外部系统超时、拒绝、空结果和部分成功按既有 Tool/业务契约表达，不伪造成功、不静默吞错。
- 日志、Trace 与返回内容遵循敏感数据保护规则。

## 验证入口

先读 backend/tests/ 中最接近的现行测试，再选择单元、契约、接口或必要集成测试。共享注册或治理变化应覆盖其主要调用方。文档中的路径和命令如与代码不符，应以实现为准并同步修订本文件。

## 新增能力的失败与降级定义（必填）

在开始实现或修改 Tool / Skill / MCP Adapter 前，先明确下表并保留在对应实现、配置或任务验收中；不需新增第二份 Manifest：

| 项目 | 开发者必须回答 |
| --- | --- |
| 能力与操作 | 复用哪个 Capability/Registry？这是只读还是有副作用？ |
| 请求鉴权 | 租户/用户/资源级范围从哪里解析？读写均有服务端限制吗？ |
| 成功依据 | HTTP 成功之外，哪一个业务字段、Schema、证据表明真正成功？ |
| 失败类型 | 连接、超时、429/5xx、401/403、业务 failed、empty、partial 分别怎样处理？ |
| 超时/重试 | 复用 ToolPolicy、Deadline、Retry；写操作是否禁止盲重试？ |
| 用户约束 | 哪些情况下 REQUIRED，哪些情况下 IMPORTANT/OPTIONAL？ |
| Fallback | 替代来源/缓存/跳过的前提是什么？如何告知来源与时效？ |
| 终态与回显 | Reporter、SSE、前端怎样区分成功/降级/BLOCKED/FAILED？ |
| 可观测与验收 | Tool status、业务 error_code、trace、指标和测试证据在哪里？ |

执行顺序：**路由候选与合法性 → 授权/确认/幂等 → Tool Runtime 调用 → 传输与业务输出校验 → 域内 Failure Policy → Reporter/API/SSE**。不允许让 LLM 直接解释 HTML 502 页、把 `{"status":"failed"}` 看成检索证据，或让 MCP Server 自建绕过治理的重试与凭据路径。细节以 [统一失败规范](../architecture/failure-degradation.md) 为准；写操作异常的人工处置见 [幂等运行手册](../operations/idempotency-runbook.md)。

合并前至少覆盖成功、业务失败（含 HTTP 200 + failed）、无结果、权限拒绝、Tool 超时、动态硬依赖和（有副作用时）未知效果，按 [测试策略](testing-guide.md) 选择必要 T1/T2；无真实 Provider 不能将真实链路标记为已验收。

## Governance 闸门和版本一致性验收补充

**声明权威**是 `backend/orchestration/router/capabilities.yaml`；派生校验/执行分工在 `backend/core/tool_governance/registry.py`、`guard.py`、`result_validator.py` 与 `backend/core/tool_runtime/`。Tool Registry（静态定义/查重）与 Capability Registry（可路由能力）是不同职责，不可为“统一名称”合并。

- 注册新 Tool/Capability 时核查候选路由、Schema、operation、risk_level、confirmation_policy、rate limit、预算、超时、熔断、幂等与领域约束；禁止独立 MCP Server 重做且绕过这些门禁。
- 调用前的身份/资源/租户授权与用户确认均由服务端执行。写操作的批准请求、实际执行参数 fingerprint、idempotency key、未知结果查询须能闭环；失去治理服务不允许用更宽的备用路径继续写。
- `ToolLifecycle` 的 `BLOCKED/CLARIFICATION_REQUIRED/PENDING_CONFIRMATION/PENDING_APPROVAL/READY/EXECUTING/SUCCEEDED/EMPTY/DEGRADED/FAILED/TIMEOUT` 与 `ToolStatus` 并非同一枚举；业务完成仍取决于 Domain Failure Policy。
- **失败结果**：`result_validator.py` 的合法封套 `success/empty/degraded/failed` 不应被二次封装误判；详见 [失败规范](../architecture/failure-degradation.md)。
- 审批和调度要能用 Trace 查看 `capability、tool_spec/version、operation、risk、confirm/approval、执行状态、业务状态、fallback/retries`（字段以现有实现为准）；未经权限审查不得将原始参数或密钥写入 Trace。

新/改 Tool 需按既有测试补 `候选内/候选外、Schema 非法、401/403、两种用户依赖强度、HTTP 200+failed、degraded、批准后参数篡改、重复写/超时未知、MCP 边界`。仅在项目实际接入的执行通路测试；未测真实 Provider 与外部 MCP 应写 NOT VERIFIED。指标和告警见 [Grafana 规范](../observability/grafana-guide.md)。
