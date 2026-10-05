# Frozen Contracts — 冻结契约清单（生产架构基线红线）

日期：2026-09-29（STOP H 收官冻结）
性质：**不可随意修改的契约总台账**。每条含：契约内容 / 冻结理由 / 守卫（锁定它的测试或门）/ 变更流程。本文是 STOP A-H 各审计与结案报告中「禁改清单」的合并收口；细节证据见各 STOP 文档（同目录）。

**变更流程（对所有条目一律适用）**：破坏性变更 = 新 ADR + 例外台账登记 + 消费方同仓原子迁移 + 版本化兼容期；只向后兼容演进（新增可选参数/新增枚举值）可直接进行，但必须跑该条目「守卫」列的全部测试。

---

## 1. LangGraph 拓扑

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 主图核心节点 | 9 个核心节点名与顺序（含 `general_chat`），builder 接线不手改；Skill/域图节点由自动发现加入 | checkpoint 兼容与 trace 连续性依赖 node id 稳定；守卫：`tests/orchestration/graph/` 节点清单用例、`test_registry_consistency.py` |
| 子图节点名 | 各域图 `add_node` 名（如 `cs_*` / `travel_*`） | 同上；STOP E 语义边界测试 |
| recursion_limit | `MAIN_GRAPH_RECURSION_LIMIT`（默认 80） | 防失控循环的既有生产口径 |
| state schema | `OrchestratorState` / 三域 state / `funnel_context` / `travel_context`——新增节点标记字段必须入 state（LangGraph updates 流剥离未知键） | 守卫：schema 一致性用例；教训在案（selection_blocked / funnel_context / travel_context 三次补登记） |

## 2. Checkpoint

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| checkpointer 构造 | 三处 `_build_checkpointer`（主图/客服/旅游）postgres 优先；主图/客服走 `degrade_or_raise`——production 默认 fail-loud（`CHECKPOINTER_ALLOW_DEGRADE` 默认 false），开发环境降级 MemorySaver；旅游域当前未接（postgres 失败静默降级，待收口）；psycopg v3 + `langgraph-checkpoint-postgres` | 跨轮状态与断点续跑的根基 |
| thread 前缀 | `travel:{tenant:user}:{conv}` 前缀防 CS 共表覆盖 | 旅游跨轮恢复契约（STOP F-line）；守卫：`tests/travel/test_checkpointer.py` |
| TTL 清理 | 收敛 `orchestration/graph/checkpointer_cleanup.py` 全进程单例，改 TTL 三处一起改 | 守卫：checkpointer_cleanup 用例 |

## 3. Router Runtime

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| route_mode | 取值集与语义（direct / workflow / plan / clarify / general_chat + 域分流 domain_graph；2026-10-05 随 RoutingEngine 收口兼容扩容补齐归宿） | 上游消费方（SSE 事件/评测/前端展示）绑定；守卫：`tests/orchestration/router/` |
| prefilter 顺序 | 域预过滤优先于 RoutingEngine 统一路由；优先级**客服 > 旅游 > 选品 > 预订 > 商务**（「订单里的行程单」属客服诉求） | 生产口径；守卫：`tests/orchestration/graph/test_router_prefilter_order.py` |
| 注册键 | router 注册键冻结零 diff（STOP B/E） | 守卫：registry 一致性三件套 |

## 4. Capability 元数据

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| SSOT | capability→Skill→metadata 唯一事实源 = `backend/orchestration/router/capabilities.yaml`；派生量禁止手写回文档（G2） | 双源曾致路由失明（workflow 段漏登记）；守卫：`test_registry_consistency.py`、`test_layer_consistency.py`、`test_adr0001_dual_registry_merge.py` |
| capability 命名 | 恰一个点 `<域>.<动作>` 全域唯一；workflow 纯蛇形不带点 | 路由/规划唯一键 |

## 5. Tool Runtime（执行治理）

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 九件套 | `core/tool_runtime/`（executor/models/policy/deadline/retry/circuit_breaker/bulkhead/error_mapper/metrics）冻结 | Phase2 生产收口冻结；retry/breaker/bulkhead 是 Tool 层专属语义，禁止向 Expert/Node 层扩散 |
| `ToolResult.status` | **执行态**语义（timeout/breaker/bulkhead/...），与业务态正交；executor 对 Tool 返回值零改动透传 | STOP G 明确裁决：业务失败识别收敛在 skill 层 validate_semantics，两层概念不得焊死 |
| 输出契约基座 | `tools/map/_base.py` 的 `ok/fail/not_configured`；「查不到」与「查不了」分开 | 行程幻觉防线的语义约定 |

## 6. Tool Contract Boundary（STOP G）

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 两型契约 | Tool 输出恰为二型之一：text（Markdown/纯文本给 LLM）或 structured（`{"status","data"}` 封套给程序）；**不统一为单一返回类型** | 受众分裂是对的（审计裁决）；守卫：`tests/skills/test_output_type_declarations.py` |
| 声明 | 每个 Skill 显式 `output_type`（ClassVar，留在代码不迁 yaml），禁止隐式默认 | 守卫：同上（vars 断言） |
| 封套形状 | 成功 `{"status":"success","data":...}`；失败 `{"status":"failed","error":...}`（+`error_protocol` 九字段可选） | 守卫：`tests/test_tool_envelope.py` |
| 唯一解包出口 | `shared/tool_envelope.py::unwrap_envelope`——全仓仅 skill 边界与 skill_adapter 两个消费方，禁止第三种手写解包 | 守卫：`tests/skills/test_tool_contract_boundary.py` |
| 失败语义 | `validate_semantics` 以 `status=="failed"` 为第一等依据，error 键嗅探为兼容兜底 | 守卫：同上 |
| 边界不变式 | 封套不透出 skill 边界流到 Reporter；`_normalize_output`/`_coerce_final_answer`/`step_results` 契约不动 | 守卫：`tests/test_map_lookup_direct_contract.py`、`tests/skills/test_base_output_contract.py` |
| 34 Tool 函数体 | 不重写（E8 的 18 个 text 型维持 Markdown——强套封套 = prompt 变胖） | E8 台账（四层设计规范） |

## 7. Expert Runtime（STOP F）

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 公开签名 | 两个 `run_expert_safely`（CS 带 `timeout_s`）签名、`ExpertResult`/`TravelExpertResult` TypedDict 字段、status 枚举值、日志前缀（`[CS Expert]`/`[Travel Expert]`）、`record_cs_expert_result` metrics 名、`travel_expert_{name}` span 命名 | 12+8 个调用点与观测口径绑定；守卫：`tests/node_runtime/test_parity.py`（旧 vs 新 oracle）、既有专家测试 |
| 超时实现 | `TimeoutStrategy.THREAD_ISOLATED`（per-call 单 worker 池 + `contextvars.copy_context()`）是**唯一**超时实现；禁止共享线程池 timeout | P2.3（共享池饿死误判超时）+ B4（线程丢业务 pin）两次生产事故语义；守卫：`tests/node_runtime/test_cs_thread_isolation.py` |
| 遥测形态 | CS 专家 metrics-only 无 span；Travel 专家 span 软失败——禁止互相补齐（CS 补 span 已登记 Deferred） | trace 形态是行为变更；守卫：span parity 用例 |
| NodeResult | 仅 status/error/duration_ms/data 四字段，业务字段禁入 | 守卫：`tests/node_runtime/test_contract.py` 字段清单断言 |

## 8. Trace / SSE / API

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| TraceMiddleware | builder 接线与 span 形态冻结（span 名称/结构/metrics 口径）；其内部是否改用 NodeRunner 属独立后续 | 主图 9+5+12 节点覆盖面冻结 |
| SSE 帧序 | `meta → status/log/delta → done/error`；done 事件 `sources` 由服务端预提取 | 三端前端消费绑定（`frontend/src/lib/types.ts`） |
| API 响应 | 统一 Result 包裹 `{code,message,data}`；列表分页；写接口幂等键 | 前端 `unwrapResult` 依赖（frontend auth 曾因 503 HTML 报「缺 data 字段」） |
| 身份头 | APISIX `gateway-auth` 验 JWT 注入 `X-User-Id`（Bearer 优先于 X-API-Key） | `docs/contracts/identity-header-protocol.md` |

## 9. Domain Runtime（STOP E）

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 语义边界 | `DomainGraph.name` / subflow metadata 冻结；5 物理域图 = 3 顶级业务域（travel 含 planning/commerce/booking 子流），commerce/booking 保留独立生命周期与开关 | 评测 runner 命名空间与开关治理绑定；守卫：`tests/orchestration/test_domain_registry.py`、`test_domain_semantic_consistency.py` |
| 交付契约 | 四个域图 reporter 各自的类型化交付契约（ExpertResult / 行程产物 / CommerceResult / 漏斗状态） | 不经主图 step_results；STOP G 明确不纳入 Tool Contract |
| 域图服务地图 | 专家×第三方服务×凭据×降级五行（新增服务必须补全） | `docs/architecture/domain-service-map.md` |

## 10. MCP Boundary（Integration Adapter）

| 项 | 契约 | 理由与守卫 |
|---|---|---|
| 定位 | 纯 Integration Adapter（Tool 第二出口）；内部主链路不经 MCP；2026-10-02 起外部 MCP server 可经 `infra/mcp_client.py` 作为 Tool 数据源（Infrastructure 侧新方向，不改变本层「纯出口」定位） | STOP G 审计确证（零 tool_runtime/skills 依赖）；数据源方向首例 b42ab10（12306） |
| 对外信封 | `manager.route` 统一 `{ok, tool, server, result|error}`；三出口（REST /api/mcp、:8091 标准协议、internal_ai）收敛于此 | 对外契约，禁与内部封套互相迁移 |
| 参数派生 | `langchain_tool_to_mcp_meta` 从 `args_schema` 派生，**禁止手写** | 单一事实源 |

## 11. 平台铁律（横切）

- **G1** 声明式注册、启动期派生、fail-fast｜**G2** 单一事实源，派生量禁止手写回去｜**G3** 谁定义谁注册，禁止集中代注册｜**G4** 例外必须登记四层规范 §4 台账。
- 调用方向：`Planner → capability → Skill → Tool → Infrastructure`；Tool 不得 import Skill。
- 数据库：结构变更只走 `sql/migrations/` 迁移脚本且可回滚；新迁移登记 `MIGRATION_TARGETS`。
- 写操作：副作用 Tool 必须过 `security/tool_approval.ensure_approved()`。
