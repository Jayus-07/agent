# P1-G Runtime Control Center：管理端产品契约与实施计划

> 决策：**UI 结构已得到用户认可，优先以产品体验定义目标契约，后对账现有后端并最小适配**。
>
> 当前状态：**DESIGN APPROVED / NOT IMPLEMENTED**。交互 HTML 和截图目前是独立演示资产（Mock），尚未承诺已纳入仓库、前后端已接通或实机已验收。
>
> 归属：总计划 [P1-G](../agent-platform-roadmap/TASK.md)；技术事实源在源码、配置、迁移及验证结果，不以本产品计划替代它们。

## 1. 产品定位与不变量

以当前 **`frontend-admin/` 管理端**为实现目标，不新开第四个 Admin 应用、不新建独立 Agent Runtime。清晰、克制、可诊断：总览 → 找出异常 Run → 查看真实 Trace → 在合法权限下处理治理动作。

- 先产品 UI/交互契约，再后端 REUSE / ADAPT / BUILD / BLOCKED 对账；仅被真实差距证明必要时才修改后端。
- **可观测只读与治理写操作分离**；前端隐藏按钮不能替代后端授权。所有跨租户和资源授权从服务端可信身份取得。
- 页面不能把 HTTP 200、Tool 传输成功、Run 完成和业务目标成功混为一谈；DEGRADED/PARTIAL/PENDING/UNKNOWN 等须呈现真实差异。
- Prompt/预算/Tool 策略等写操作经过后端权限、校验、版本 CAS/幂等及审计；UNKNOWN 不得自动重投。
- Grafana/Prometheus、Trace、PromptService、Model Budget、Tool Governance、Workflow Registry 都复用现有事实源；管理端只做产品聚合/适配。
- 不展示真实系统提示词链路的隐藏推理、用户敏感数据、Token、密钥、完整 SQL/Tool 私密参数；授权细节查看仍脱敏。
- 页面请求必须处理 loading / empty / 403 / 409 / 422 / 429 / 5xx / stale / partial / unknown；不无限 spinner、不伪造成功。

## 2. 固定导航：三组九页

| 分组 | 页面 | 核心查询与交互 | 权限与注意事项 |
| --- | --- | --- | --- |
| 运行监控 | 运行总览 | 选时间/租户（须在授权范围内）、成功率、业务降级率、p50/p95、实际费用、告警、运行中数量；点 KPI 进入过滤列表 | 只读；数据源/更新时间/样本数/币种；0 值不是无数据 |
| 运行监控 | 执行记录与 Trace | Run 列表分页/筛选；详情显示真实 Span 瀑布、RouteDecision、Planner、Supervisor、Skill/Tool、Reporter、结果、重试/取消/版本/成本 | Run 和 Span 必须权限过滤与脱敏；按实际发生节点展示，不硬编码固定图 |
| 运行监控 | Agent 与工作流 | 发现的 Agent/Domain/Workflow、描述、契约版本、依赖/步骤、运行历史与失败；只读检查拓扑 | 不在该页直接随意修改运行中 DAG/业务状态 |
| 能力治理 | Tool 与 Capability | 按 registry 查看 Tool/Skill/MCP adapter、风险等级、Schema 摘要、超时/重试/熔断/确认策略、失败率；进入详情 | 策略变更需要权限、版本、双重确认与审计，不从 UI 创建第二套注册表 |
| 能力治理 | 模型与成本 | 模型角色绑定、请求/用户/租户额度、token 与价格版本、estimated/settled/pending 账本、成本趋势及异常 | 账单数字标币种/计价时间与结算口径，编辑独立授权 |
| 能力治理 | Prompt 版本 | 受限预览、版本 diff、评测/发布状态、操作人、影响范围、发布/回滚进度 | 沿用现有 PromptService、事务/CAS/outbox，未收到服务端确认不得报发布成功 |
| 安全运维 | 权限与租户 | RBAC 角色、权限摘要、资源访问审计、租户范围；支持安全过滤 | 对敏感凭据、成员跨租户资料遵守最小披露；无越权查询 |
| 安全运维 | 审批与审计 | 审批队列、工单、请求风险、参数指纹、操作来源、状态查证、审计记录 | 批准/拒绝只通过已有业务状态机；人工确认不可由 LLM/UI 代替 |
| 安全运维 | 服务健康与告警 | APISIX/FastAPI/DB/Redis/Queue/LLM/Provider 健康、SLO、告警、时间窗、诊断跳转 | 监控不可用显示 UNKNOWN；不以旧 Prometheus 数据声称当前正常 |

### 关键交互：从运行总览进入失败 Run

总览异常 KPI → 带筛选 Run 列表 → 选定一个 Run → 顶部展示业务终态和执行终态 → 按时序查看真实 Span/工具业务结果 → 关联 Prompt、模型、Token/Cost、权限、错误与告警 → 返回列表，筛选条件保留。

Run 详情 **至少三层**：
1. 业务结果（用户目标、成功/部分成功/拒绝/待审批/未知）。
2. 实际执行过程（trace_id/span_id、节点/Tool、起止耗时、依赖和动态路由）。
3. 工程诊断（route reason、model_role/provider、prompt_version、token、estimated/settled cost、Tool HTTP/执行/业务状态、deadline/cancel/retry、error_code、脱敏证据）。

## 3. 建议的服务端契约（目标接口，不宣称已存在）

建议统一管理端只读聚合命名空间为 `/api/admin/runtime/*`，**实际路径先与现有路由和前端 BFF 对账**；如已有等价接口则复用，避免平行 API。管理端仅使用现有 BFF/API Client 和认证刷新路径。

| 契约域 | 目标查询 | 写操作（必要时独立端点） |
| --- | --- | --- |
| Overview | `GET /overview?from&to&tenant_scope` | 无 |
| Runs / Trace | `GET /runs?cursor&limit&status&agent&from&to`、`GET /runs/{id}`、`GET /runs/{id}/spans` | 禁止无依据强制重跑副作用任务 |
| Graphs / Workflow | `GET /agents`、`GET /workflows`、`GET /workflows/{id}/runs` | 如有现有工作流触发功能，复用原授权契约 |
| Capabilities | `GET /capabilities`、`GET /capabilities/{id}` | 仅已有策略变更机制及审计允许时开放 |
| Model / Budget | `GET /model-usage`、`GET /budgets`、`GET /model-roles` | 预算/绑定 CAS 更新、权限与回滚 |
| Prompts | `GET /prompts`、`GET /prompts/{id}/versions` | 已有 publish/rollback 事务与审计，不新建发布状态机 |
| Security / Approval | `GET /permissions`、`GET /approvals`、`GET /audit-events` | 受控 approve/reject；不可跨租户 |
| Health | `GET /health/services`、`GET /alerts` | 告警静音/确认由现有权限和告警后端决定 |

列表契约至少含：`items`、`next_cursor`、`observed_at`、`source`、`scope`；可选聚合指标须标 `sample_count`、`window`、`currency`、`settlement_state`。日期为带时区 UTC ISO 字符串，前端按用户时区展示；时间范围由服务端限制上限，避免大查询扫全库。

**Run 详情字段建议**（不要求立即新建表）：`run_id, trace_id, tenant_scope, entrypoint, agent_id, workflow_id, execution_mode, execution_status, business_status, started_at, ended_at, duration_ms, answer_source, prompt_versions, model_usage, estimated_cost, settled_cost, spans, approval_state, error_code, degradation_reason`。**技术执行状态、Tool 执行状态、业务状态保持分开**；`unknown_after_cancellation` 不是可自动重试的失败。

### 按权限划分操作

- 只读观察者仅能访问经授权的聚合数据和可见 Run；不一定有权查看原始文本/业务敏感数据。
- 治理编辑者仅能修改被授予的 Prompt/模型预算/Tool 策略；变更需服务端参数校验和版本保护。
- 管理员具备更高操作权限但也受租户及敏感操作审批约束；超级管理员是否存在由现有授权事实源核定，不按 UI 假定。
- 每一次写入保留操作者、作用域、旧/新版本（敏感内容只保存指纹/脱敏 diff）、审批链、结果与 trace_id。

## 4. 分批施工与禁止越界

| 批次 | 任务 | 交付物 | 依赖/退出条件 |
| --- | --- | --- | --- |
| UI-1（产品冻结） | 九页交互、信息层级、空/错/权限/响应式、设计 Token；从 Mock 定义字段清单 | 交互/导航、可读原型、页面状态清单、DTO 草案 | 用户已认可总体结构；本批只冻结业务体验，不宣称后端存在 |
| API-0（后端差距对账） | **此时才读取后端**：前端路由、BFF、Trace、Tool、Prompt、Billing、RBAC、Approval、metrics 及已有 API | REUSE / ADAPT / BUILD / BLOCKED 表，每字段唯一事实源、权限和 T2 影响 | 对账完成再确认 API 路径、DB 是否真缺字段；禁止一次性重写 |
| FE-1（只读真实链路） | 在 `frontend-admin` 复用已有组件实现 Overview/Runs/Trace/Workflow/Capabilities/Health 只读页 | 真实 API Client、分页/筛选、跨页导航、加载/错误/空态与日志脱敏 | 列表/详情一致、每个数可追溯数据源、无 Mock 混入真实看板 |
| API-1（最小适配） | 复用真实 Runtime/治理/账本数据组装 Admin 读模型，必要时加轻量 BFF/聚合 API | DTO/OpenAPI/错误码/时间窗口/权限检查/资源查询测试 | 无第二账本、无第二 Registry；租户和资源权限在后端强制 |
| GOV-1（受控写入） | Prompt 发布/回滚、预算变更、可用的审批操作按**现有**业务机制接线 | 查看 diff → 确认 → 提交 → 服务端成功/冲突/未知状态 → 审计/回滚 | B1-D/预算/审批相应后端门禁真实可用才解锁；否则明确 BLOCKED |
| T3（发布验收） | PC/窄屏、角色权限、真实 API/SSE/DB、追踪一致性、告警/计费数据对账 | 截图、API 响应、Trace、测试证据与可回滚 PR | P0 全绿；所有缺 Provider/密钥等标 BLOCKED，不能假 PASS |

### 项目协作

- UI-1 可独立开展，不修改公共后端代码；API-0 要避开并行中的 P1 B1-A～F PR，等安全交接后基于最新 main 对账。
- 各批独立分支/PR。先做只读，后做敏感写操作；不要求发布前做灰度/影子双跑，但需要真实 T2/T3 + 一键回滚路径。
- 不新增全站设计系统、不复制模型/Tool/Prompt 注册表、不因页面不存在数据强制创建 DB 表；不拆成第四个管理端工程。
- 保持原有 Grafana 作为指标与告警的数据/诊断来源，不伪造在 Admin 中即可控制所有外部平台。

## 5. 验收矩阵（每项需真实证据）

| ID | 场景 | 必须观察到 |
| --- | --- | --- |
| RC-01 | 九页面导航、浏览器刷新及返回 | 当前页、筛选、详情、链接一致；无空路由 |
| RC-02 | Run/Span 动态时间线 | 仅显示真实发生的节点；`trace_id` 与后端一致 |
| RC-03 | HTTP 200、Tool failed、Domain partial | 三者分别展示，业务失败绝不显示 SUCCESS |
| RC-04 | 取消/Deadline、执行后物理效果未知 | UNKNOWN/IN_DOUBT 清楚可见，禁止自动重投副作用 |
| RC-05 | 跨租户列表/详情/审计资源访问 | 服务端 403/404；聚合计数不泄露他人数据 |
| RC-06 | Viewer/Editor/Admin 权限边界 | 页面和 API 一致；不能靠隐藏按钮绕权 |
| RC-07 | Prompt 发布/回滚 | CAS 冲突→409；服务端成功后才更新版本，审计可追溯 |
| RC-08 | 预算/价格账本 | Token、币种、计价版本、estimated/settled/pending 与底账对齐 |
| RC-09 | 能力策略及审批 | Schema/风险/权限/确认/幂等生效；未知结果不可重复执行 |
| RC-10 | Grafana/Prometheus 不可用 | UNKNOWN/STALE，不展示旧数据为当前健康 |
| RC-11 | 分页过滤、时间范围与性能 | 不越权、不扫全库；列表/详情延迟有实测且无关键卡死 |
| RC-12 | 1280x900、1440x900、390x844、375x667 | 核心只读和审批链可操作、无水平溢出、键盘焦点可达 |
| RC-13 | 安全内容 | 秘钥、完整隐私提示词/SQL/Tool 参数、跨租户数据不暴露 |
| RC-14 | 对账及回滚 | Run/Trace/History/业务终态一致，关键治理变更可查证与回滚 |

验证层级遵循 [T0–T3 测试策略](../../development/testing-guide.md) 与 [前端设计规范](../../development/frontend-design-standard.md)。UI/Mock 交互成功不代表真实 API/T3 成功；每项记录 PASS/FAIL/BLOCKED/NOT VERIFIED。

## 6. 完成标准

- UI-1 及后端差距对账已审查；UI 目标契约因证据变化可以**版本化**调整，不能为了适配旧 API 默默丢需求。
- 九页面主要查询来自真实授权数据源，明示时间范围、来源与新鲜度；敏感写入完全受现有后端状态机管理。
- P1-G 功能发布与 P1 B1-A～F 安全/事务/取消/费用治理的共同阻断项全部满足；仅通过静态/模拟验收时保持 `PRODUCTION_READY=false`。
- 施工进度与阻断见 [PROGRESS.md](PROGRESS.md)。公共事实源见 [AI Runtime](../../architecture/ai-runtime.md)、[Tool/Skill 指南](../../development/tool-skill-guide.md)、[账本治理](../../governance/llm-budget-billing.md)、[Trace](../../observability/trace-model.md)、[Prompt](../../prompt/architecture.md)、[授权](../../auth/authorization-standard.md)。
