# ADR-0011: MCP 能力中心（动态外部工具的统一治理接入）

状态：Accepted（随本 PR 实施）
日期：2026-10-26

## 背景与动机

外部 MCP Server 的接入此前是**代码级集成**：每个新数据源要写一个
`backend/tools/` 包装、一组 `*_MCP_*` 环境变量、一份 capabilities.yaml 契约，
再重新部署。管理员无法在不改代码的情况下接入新 MCP Server、发现工具、
审核风险并授权给 Agent。

目标链路：
`管理端配置连接 → MCP 发现工具 → 安全审核 → 产品授权 → 动态注册 →
统一治理执行 → Trace 观测 → 停用与回滚`，全程不修改业务代码、不重启应用。

## 决策

### D1. 数据库是动态配置的唯一事实源；capabilities.yaml 保持静态内置工具的事实源

- 内置 Tool/Capability：继续以 `capabilities.yaml` 声明，行为零变化。
- 外部 MCP Tool：连接、凭据引用、工具快照、审核状态、治理配置、产品授权
  全部持久化在 `ai.mcp_*` 表（迁移 114）。
- 两类来源在进程内合并为**同一个治理视图**：
  - `tool_governance/registry.all_tool_specs()` = 静态 YAML specs + 动态 approved specs；
  - `router/manifest.effective_manifest()` = 静态 manifest + 动态 CapabilityDecl。
  不存在第二份互相矛盾的"动态 capabilities.yaml"；动态工具**逐个登记进
  capabilities.yaml 是被禁止的**。

### D2. 动态注册 = 进程内快照 + 15s 轮询（复用 LLM registry_store 既有契约）

`backend/infra/mcp_registry/store.py` 照 `infra/llm/registry_store.py` 模式：
每 worker 后台 15s 轮询 DB → 派生不可变快照（ToolSpec / CapabilityDecl /
授权集合 / 连接运行时信息）→ 签名变化时失效下游缓存
（`tool_registry.invalidate_cache()`、向量路由索引对账重建）。

一致性契约：
- 管理端写操作成功后本进程立即刷新；其余 worker 最迟一个轮询周期内生效。
- 刷新失败 fail-open：保留上一次已知快照（与 LLM registry 相同方向），
  绝不因 DB 抖动把已生效工具集清空。
- 快照只加载 `status=approved AND schema_changed=false AND 连接 enabled`
  的工具——未审核、Schema 漂移、停用连接三者在派生层即被排除。
- 快照签名包含每个工具声明内容的稳定摘要（描述/Schema/治理配置/示例），
  同名工具内容变化同样触发其他 worker 的派生缓存失效——只比较键集合时，
  其他 worker 的 CAPABILITY_SCHEMA 会一直保留旧参数声明。
- **写操作强一致（撤权/停用/漂移的即时拦截）**：快照是最终一致视图，
  `operation != read` 的动态工具在出网前由
  `store.verify_write_authorization()` 按事实源（ai.mcp_* 表）原子复核
  审核/漂移/连接/授权四态，任一失效即拒绝（fail-closed）；读操作保持
  15s 收敛语义。internal（管理端治理验证）不依赖产品授权，测试调用
  通道本身限只读工具。

### D3. 上游 Schema 变化 = 待重审（保守失效策略）

发现时对上游声明的 description + inputSchema + outputSchema 计算
content_hash；与 `approved_hash` 不一致即置 `schema_changed=true`，工具
立即退出运行时快照（新调用被 `tool_not_registered` 拒绝），管理端标记
"Schema 已变化，需重新审核"。不做兼容性自动判定——上游声明全部是
不可信输入，任何变化都要求人工重审。重审通过后更新 `approved_hash`。

### D4. 动态工具的执行 = 通用 MCP Skill + 现有 GovernanceRuntime，零新执行器

- 新增唯一的 LangGraph 节点 `mcp_dynamic_skill`（`backend/skills/mcp_dynamic/`），
  启动时注册；运行时按 step 的 capability 从快照解析连接与上游工具名。
- `capability_registry.get_node()` 对 `mcp.*` capability 固定映射到该节点，
  因此**不需要重建 LangGraph 图**，也不需要为每个动态工具注册 Skill。
- 执行走 `BaseSkill.execute → _execute_governed → governance_runtime.execute →
  SafeToolExecutor → infra/mcp_client.call_tool` 的既有全链：
  Schema 校验、风险判定、确认/审批、预算、限流、去重、熔断、超时、
  写操作零重试、Trace/指标全部复用，**不建立第二套 Tool Runtime**。
- `tool_runtime/policy.get_policy()` 增加动态层：`mcp.*` capability 的
  ToolPolicy 从审核治理配置派生（超时/重试/熔断/操作类型）。

### D5. 产品授权是执行契约的一部分（ADR-0002 对齐）

- `product_allows_capability()` 对 `mcp.*` capability 增加动态授权检查：
  当前服务端产品身份 + capability + 租户必须在授权快照中
  （`ai.mcp_tool_grants`，product_key ∈ {agent, customer_service}）。
- 该函数被 router 候选过滤、direct_executor、BaseSkill.execute、
  GovernanceRuntime.evaluate 四处消费——改一处全链生效；`domain_hint`、
  工具名、Planner 输出不构成授权。
- internal 服务身份保持全放行（与静态能力一致），public 永拒。

### D6. 命名与路由

- 动态 capability 名：`mcp.<slug>`，slug = `<连接名 slug>__<上游工具名 slug>`，
  必须满足 manifest 的 `<domain>.<action>` 两段正则，全局唯一（DB 唯一索引）。
- `capabilities.yaml` 的 `domains` 段静态声明一次 `mcp` 粗域；所有动态
  capability 挂该域，通过既有的分层路由进入候选。
- 审核时管理员必须提供 ≥2 条路由 examples（与静态能力同规则），向量路由
  索引在快照签名变化时对账重建。

### D7. 安全基线

- SSRF：连接创建/编辑时 `assert_url_allowed(base_url, allow_private=
  network_scope=='private')`；调用路径同样复用 url_guard 的校验语义。
  V1 仅支持 Streamable HTTP，不开放 stdio。
- 凭据：Fernet 密文分表存储（`mcp_connection_credentials`），API/审计/日志
  只暴露 fingerprint/last4。
- 审计：连接/凭据/审核/授权/启停/发现全部写 `ai.security_events`
  （ADMIN_ACTION + category=`mcp_*`），不建平行审计表。
- 权限：管理 API 走 `require_admin_user`（写操作带幂等键走
  `run_governance_mutation`）；测试调用（B4）走与生产完全相同的治理链。

## 后果

- 多 worker 下停用/撤权对**写调用**即时生效（出网前按事实源强一致复核，
  见 D2）；对读调用最迟 15s 生效（与 LLM provider 配置一致）；本进程立即生效。
- 动态工具不进入 `tool_contracts.lock.json`（构建期产物只覆盖静态工具），
  其版本追溯依赖 mcp_tool_snapshots + trace 中的 capability/slug。
- 上游工具描述不可信：只作为审核参考展示；进入 Planner/FC prompt 的
  description 由审核者确认（默认截断自上游描述，可编辑）。

## 验证要求

见 `docs/architecture/adr/0011` 对应测试：
`backend/tests/mcp_registry/`（快照/派生/失效）、`backend/tests/api/test_admin_mcp*.py`
（权限/幂等/SSRF/凭据）、`backend/tests/test_mcp_dynamic_e2e.py`
（mock MCP Server 端到端）。
