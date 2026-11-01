# 计划：travel_commerce / travel_booking 收编为旅游域子智能体（重构）· 交下一会话执行

> 2026-11-01 v2 · 基于 main@0d848a6 只读侦察 + 用户需求确认（2026-11-01）· 归属：docs/tasks/travel-v2-production/
> 前置阅读：AGENTS-DESIGN.md（智能体设计）、TASK.md（对账矩阵）、本文 §1 核实事实。
> 执行会话纪律：从最新 main 重建 worktree（feat/travel-domain-consolidation）；提交双路径限定走 PR；SQL 全参数绑定（Mimosa 验收条件）；不跑全量 pytest。

---

## 0. 需求确认（用户指令，2026-11-01）

> 「travel_commerce / travel_booking 架构上是顶级域图，但功能是旅游域用的。现在需要把它们**删掉，或者放到旅游域下面做子智能体，重构一遍**。每个子智能体的能力（skill/tool/mcp）**需要测试返回的数据，结合真实数据改项目**。」

即：消除「功能属旅游、架构却平行注册为域图」的错位——两个子图都要**收编为旅游域内部的子智能体**，能力接真实数据源并配套数据契约测试。

## 1. 核实事实（main@0d848a6，全仓证据）

1. **两域不是独立顶级目录**：`backend/travel/commerce/`（18 文件 ≈2660 行）与 `backend/travel/booking/`（18 文件 ≈2402 行）均在 travel 包内；注册名 `travel_commerce` / `travel_booking`（domains/__init__.py:18-19，STOP K/L），domain="travel"（commerce/register.py:26、booking/register.py:24）。开关 `TRAVEL_COMMERCE_ENABLED` / `TRAVEL_BOOKING_ENABLED` 默认 false（config/travel_commerce.py:22-27、config/travel_booking.py:16-26），compose 不注入、sys_config 无 DB 覆盖、用户端前端零引用、无专属 HTTP 端点。
2. **commerce 是 hotel/flight 契约位的完整实现**：normalize fail-closed 三道门 / Decimal Money / offer_fingerprint / ranking / deeplink 安全校验；travel Provider 层 CAP_HOTEL_SEARCH / CAP_FLIGHT_SEARCH 登记为 OUT（contracts.py:160-161），live 实现未接。travel 域本体 query_hotel 只是轻量版（auxiliary_tasks.py:246-253）。
3. **booking 是预订事务安全件的唯一实现**：executor.py 381 行固定前门序（authorization→quote→confirmation→复核→执行，不可证明成败即 IN_DOUBT）、store.py 403 行 DB CAS 状态机+同事务事件账、identity 四层身份、revalidate 价格复核、webhook Inbox、reconciliation 对账、recovery（celery beat 60s）、state 白名单状态机、reporter 话术白名单。**这些是原型 B7 确认门与 F07/E1 的承载，收编时必须原样保留。**
4. **booking 单向依赖 commerce 三处**：booking/graph_builder.py:94,126（extract）、booking/service.py:281-282 与 revalidate.py:17-18（service/request）——commerce 收编时同步改指向。
5. **联动修改面**：删除域图壳涉及约 30 处（注册/预滤器/续填/健康检查/圆桌/celery include 与 beat/评测 runner+MODULE_KINDS+Golden 契约/datasets/frontend-admin/README/docs），全清单见 §4.4 与 §5.4。
6. **052 迁移**（booking 四表 travel.booking_quotes/orders/webhook_inbox/events）：幂等 additive、无 GRANT，保留不动；commerce 无专属表。
7. **真实数据可用性**（决定契约测试怎么写）：高德 REST（AMAP_KEY，merchant.py 已在用）真实可用；腾讯 LBS / 和风真实可用（已接）；12306 MCP 已接默认关（无 SLA 披露）；rollinggo MCP **BLOCKED**（权限待上游开通）；flight/event/notice 契约位无源。

## 2. 目标架构决策

| 对象 | 重构形态 |
|---|---|
| **travel_commerce** | **删除域图壳**（register/graph_builder/graph_node/graph_state/graph_node_render/service/health/reporter/telemetry）；**收编实现**：request/extract/normalize/models/ranking/identity/cities/deeplink 移入 `backend/travel/providers_support/commerce/`，成为 **Lodging/Intercity 子智能体的数据源实现层**（`travel.hotel.search` / `travel.flight.search`） |
| **travel_booking** | **删除域图壳**（register/graph_builder/graph_node/graph_state）；**事务机制整包保留为旅游域子智能体「Booking Executor」**：executor/store/state/identity/revalidate/webhook/reconciliation/recovery/reporter/telemetry/provider_capabilities 原样保留在 `backend/travel/booking/`，对外形态从「聊天子图」改为 **travel_v2 REST 端点**（quote→confirm→status 三段，见 §5），确定性重搜逻辑从 commerce 收编件引用 |

统一后旅游域子智能体全景（域图 5 专家 + auxiliary + 新增 lodging；图外子智能体以 REST/服务形态挂 travel_v2）：

```
旅游域（backend/travel/）
├─ 生成主线（LangGraph 域图，12+1 节点）
│   slot_filler → supervisor → poi → transit → lodging* → weather → budget → risk
│   → validator⇄repair → auxiliary_tasks(Dining/Intercity Scout) → reporter
├─ 查询/推荐子智能体
│   Dining Scout（auxiliary 扩展）· Intercity Scout（auxiliary 扩展）· Local Context（契约位）
├─ 编辑子智能体（travel_v2 ai-edits 状态机 + trip_write_validator）
├─ **预订子智能体（原 travel_booking 收编：BookingExecutor REST 化）** ★
├─ **自动优选子智能体（travel_v2 auto-optimize 服务）** ★
└─ 支撑：providers_support/commerce（收编件）· providers/live 链账 · knowledge/memory
★ = 本轮重构新增/收编
```

## 3. D 系列：commerce 收编与删壳

### 3.1 收割（先建后删）
- 新建 `backend/travel/providers_support/commerce/`：移入 request.py / extract.py / normalize.py / models.py / ranking.py / identity.py / cities.py / deeplink.py；**新增 offer_search.py**（从 commerce/service.py 抽出 booking 复用的确定性重搜逻辑）；booking 三处 import 同步改。
- `travel_v2/services/hotel_search_service.py`：包装 normalize/ranking + provider factory（off/fake/live 三态），供 `travel.hotel.search` ToolSpec 与 `/travel/v2/search/hotels` 数据源切换。

### 3.2 删壳
- 删 commerce 的 register / graph_builder / graph_node / graph_node_render / graph_state / service / health / reporter / telemetry / __init__。

### 3.3 联动修改全清单（逐项勾选）
- [ ] domains/__init__.py:18 删 commerce 注册
- [ ] orchestration/graph/commerce_prefilter.py 整删；routing/prefilter_chain.py 移除链项
- [ ] routing/continuation.py:112 移除 travel_commerce 续填
- [ ] context/booking_pending_resolver.py:43-52 移除 commerce 白名单（保留 booking）
- [ ] router/capability_router.py:14-15 移除别名；observability/llm_context.py:39 KNOWN_DOMAINS 移除
- [ ] core/product_context.py:176,180 移除 commerce root
- [ ] app/api/routes/health.py:58,162-175 删 commerce 组件
- [ ] app/api/routes/agent_roundtable.py:73-81,106-107 删 commerce 人设
- [ ] evaluation：runners/builtin.py:19-20,35-36、runners/travel_commerce.py（删）、models.py MODULE_KINDS、evaluation_v2/domain_contracts.py:77-79,302-330、datasets/travel-commerce/（删）、scripts/e2e_travel_commerce.py（删）
- [ ] frontend-admin：roundtable/page.tsx:36-37、AddToEvalModal.tsx:23-24、domain-attribution.test.ts:8-49
- [ ] README.md:147-157、docs/evaluation/evaluation-governance.md:9-10、docs/contracts/evaluation-v2-api.md:53-69

### 3.4 测试同步
- 删 `backend/tests/travel/commerce/`（7 文件）；同步 tests/test_domain_registration.py:51、tests/api/test_registry_overview_api.py:100-109、tests/orchestration/test_runtime_registry.py:41-61、test_domain_semantic_consistency.py:61-70,399-400、router/test_router_domain_literal_guard.py:19-20、test_router_consolidation_adapters.py:56-57,209、graph/test_entry_mode_handoff.py:51,71、evaluation 侧断言。
- 新增：providers_support 迁移后 import 全绿测试 + hotel_search_service 单测（三态 factory/fail-closed/参数绑定检查）。

## 4. B 系列：booking 收编为「预订子智能体」（REST 化）

### 4.1 删壳
- 删 `backend/travel/booking/{register,graph_builder,graph_node,graph_state}.py`；domains/__init__.py:19 删注册；booking_prefilter.py 整删 + prefilter_chain 链项移除；continuation.py:112 移除 booking 续填；booking_pending_resolver.py 相关白名单/开关读取删除；agent_roundtable.py 删 booking 人设；tasks/celery_app.py:48 include 保留 travel_booking_tasks（recovery beat 保留）。
- 配置：`TRAVEL_BOOKING_ENABLED` 语义退役（改为 `TRAVEL_BOOKING_PROVIDER` 单开关控制 provider off/fake/live）；`.env.example` 同步；README:145-157 域图表更新。

### 4.2 REST 化（travel_v2 新增端点，挂在 trips 下）
- `POST /api/travel/v2/trips/{trip_id}/bookings/quote`：入参（lodging selection_id / offer 定位）→ BookingService.create_quote（TTL/指纹）→ 返回确认卡 payload（金额互检信息/幂等键/截止时间）。
- `POST /api/travel/v2/trips/{trip_id}/bookings/{booking_intent_id}/confirm`：显式确认 → revalidate → executor 幂等执行 → 订单状态。
- `GET  /api/travel/v2/trips/{trip_id}/bookings/{booking_intent_id}`：状态查询（IN_DOUBT 呈现白名单话术）。
- 鉴权：沿用 travel_v2 租户/属主 fail-closed（booking/authorization.py 已有同型校验，改为引用 trips 的 identity context）；**全程参数绑定 SQL**（store.py 已参数化，新增查询同样）。
- 聊天入口：V2 对话中预订意图由 ai-edits clarify 返回指引卡片（点卡片走 REST 确认门）——不在域图内挂预订节点（最小改动；后续可选在 MODIFY 分支加路由）。

### 4.3 保留清单（原样不动）
executor / store / state / identity / revalidate / authorization / webhook / reconciliation / recovery + tasks/travel_booking_tasks.py + celery beat `travel-booking-recovery` + providers/travel/booking/{fake,contracts}.py + shared/idempotency、shared/provider_idempotency + 052 四表。

### 4.4 booking 侧联动清单
- [ ] 删 booking/{register,graph_builder,graph_node,graph_state}.py
- [ ] domains/__init__.py:19；booking_prefilter.py；prefilter_chain 链项；continuation.py；booking_pending_resolver.py；agent_roundtable.py
- [ ] tests/orchestration/test_domain_semantic_consistency.py:61-70,399-400、test_entry_mode_handoff.py:51,71、router 两个 guard、test_registry_overview_api.py、test_runtime_registry.py、test_domain_registration.py 的 booking 断言
- [ ] tests/travel/booking/ 中 routing 相关用例（test_booking_routing.py 108 行）改写为 REST 路由测试；lifecycle/state_and_identity/d2_two_hop/manual_resolve_ledger 保留（import 路径若受 graph 删除影响仅调整引用）
- [ ] evaluation：runners/travel_booking.py、MODULE_KINDS、domain_contracts、datasets/travel-booking/ 的处置同 commerce（runner 若仍有价值改打 REST）
- [ ] docs：README、idempotency-runbook.md:199-201（指向的手册用例仍在）、ci-evaluation-environment.md:29-30
- [ ] scripts/e2e_travel_booking.py 改打 REST 或删
- [ ] `.github/scripts/rag_smoke_path_filter.py:154` 的 052 白名单（保留，因 052 不动）

## 5. U 系列：架构统一 + 真实数据接入（结合原型验收）

- **U1 数据源层**：AmapPOIProvider 实现（接口 backend/providers/travel/poi.py:27-45）+ 链账 poi.search=("amap_live","seed")；POI 门票/时长落 PlaceFacts；`travel.hotel.search` ToolSpec 注册（implementation=D1 hotel_search_service，live 态接高德商户，rollinggo 就绪后切）。
- **U2 图节点**：supervisor TravelStage 加 `lodging`（weather 后 budget 前）+ decide() + graph_builder 注册 travel_lodging_expert（services/lodging_service.py）；auxiliary_tasks 扩展 query_food（memory 偏好过滤）与返程衔接校验；stage_targets() 结构回归。
- **U3 V2 服务**：auto-optimize 端点（聚合查询→择优建议→确认后批量走既有 selections）；B 系列 REST 端点 + V2 工作台确认门 UI。
- **U4 Skill 层**：注册 travel.food_search / travel.hotel_search / travel.booking_quote（无状态包装）；rag_smoke 清单与 path_filter 连带。
- **U5 外部依赖**：rollinggo MCP（能力中心登记 + GrantRequest.product_key=travel）——**BLOCKED**；12306 灰度开启维持披露。
- 每步用原型场景点测验收（场景→智能体映射：AGENTS-DESIGN 第二节）。

## 6. 子智能体能力 × 数据契约测试矩阵（「测试返回的数据，结合真实数据」）

> 契约测试形态：①**真实样本录制**（live 冒烟录制一次返回，脱敏后固化为 fixture，CI 回放断言结构）②**fail-closed 断言**（缺字段/金额类型/枚举越界必须拒绝，不得吞错降级为假成功）③**开关三态**（off/fake/live 各断言一次）。SQL 一律参数绑定。

| 子智能体 | 能力 | Skill | Tool | MCP/Provider | 数据源真实状态 | 契约测试 |
|---|---|---|---|---|---|---|
| POI Curator | 景点候选+票价/时长/坐标 | travel.poi_search（已有） | travel.search_poi、travel.poi.detail | AmapPOIProvider（**新增**）→seed | ✅ 高德真实可用（AMAP_KEY） | 真实高德返回样本录制→PlaceFacts 映射断言；seed 回退断言 |
| Route & Schedule | 动线+交通耗时 | - | travel.calculate_route、route.optimizer | tencent_live→local_estimate | ✅ 真实 | 现有用例回归 |
| Lodging | 档位匹配+库存报价 | travel.hotel_search（新） | travel.hotel.search（新） | 高德商户（真实）→ rollinggo MCP（BLOCKED） | ⚠️ 高德真实可测；rollinggo 等权限 | 高德商户样本→normalize 三道门断言（收编件）；rollinggo 样本待接 |
| Dining Scout | 上下文+偏好推荐 | travel.food_search（新） | map.merchant.search、memory.search | 高德直连 | ✅ 真实 | 真实返回+偏好过滤断言（不辣/清淡） |
| Intercity Scout | 返程车次+衔接校验 | - | travel.train.search、travel.flight.search（契约位） | 12306 MCP（默认关）、rollinggo flight | ⚠️ MCP 已接默认关 | MCP 响应样本 fixture 回放；开关关闭→如实披露断言 |
| Weather & Risk | 预报/降水预案/公告 | - | travel.weather.query、travel.notice.search（契约位） | tencent→qweather | ✅ 真实 | 现有用例回归；notice 维持契约位断言 |
| Budget | 档位核算/降档建议 | - | travel.calculate_budget | -（纯计算） | ✅ | 现有用例回归 |
| Trip Editor | 对话编辑全操作 | -（LLM 键 v2.trip_ai_edit） | TripEditOperationV2（域内） | - | ✅（数据即 TripDocument） | 现有 ai-edit 测试回归 |
| **Booking Executor（收编）** | Quote→确认→幂等执行→对账 | travel.booking_quote（新包装） | REST 三段端点 | provider off/fake/live（rollinggo BLOCKED） | ⚠️ fake 全链可测；live 待 rollinggo | 保留 booking 现有 5 个测试文件回归 + 新 REST 路由测试 + IN_DOUBT 话术断言 |
| Auto Optimizer | 聚合择优+批量选入建议 | - | 复用以上查询 | 组合 | 组合态 | 建议清单结构快照 + 批量选入幂等断言 |

## 7. 执行顺序（下一会话）

1. 重建 worktree → 2. D 系列（3.1→3.2→3.3→3.4，独立 commit）→ 3. B 系列（4.1 删壳→4.2 REST→4.4 联动，独立 commit）→ 4. U1→U2→U3（各独立 commit）→ 5. U4/U5。每步跑定向测试（travel/orchestration/registry 域）+ 原型场景点测。

## 8. 全局验收

- [ ] `grep -r "travel_commerce|travel_booking" backend/ frontend-admin/src docs README.md` 仅剩：booking 包本体、travel_v2 booking 端点、052 迁移、recovery 任务、env 变量 TRAVEL_BOOKING_PROVIDER（域图注册/预滤器/续填/圆桌/评测零残留）
- [ ] travel 域图节点 12+1（lodging），stage_targets 结构回归通过
- [ ] 契约测试矩阵 §6 全部落位（真实样本 fixture 入库脱敏）
- [ ] 原型场景抽样全过：三轮澄清 / 生成含住宿档位 / food 偏好过滤 / 返程车次 / 自动优选 / 确认门 fake 全链
- [ ] V2 API 行为回归（search 三态 / ai-edits / selections 幂等）+ booking REST 三段集成测试

## 9. 风险与回滚

- 删 commerce 断 booking → 「先收割后删壳」规避；每步独立 commit 可回滚。
- booking REST 化丢失聊天预订入口 → V2 对话以指引卡承接（§4.2）；若需图内预订，后续在 MODIFY 分支加路由（另行决策）。
- 真实数据测试依赖网络/key → 高德 key 走 env；录制样本脱敏入库后 CI 离线回放；缺 key 环境 skip（显式标注）。
