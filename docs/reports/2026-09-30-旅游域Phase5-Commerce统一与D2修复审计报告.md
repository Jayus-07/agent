# Phase 5 迁移审计报告 — Commerce 统一入口 + D2 两跳断修复 + 审批统一模型（只读，未改代码）

> 依施工军规：Phase 5 施工前先审计，**确认决策点前不改任何代码**。基线：Phase 4（`7f35529` / `a80a594` / `9a0f3e4`）。
> 设计依据：v3 §6 Phase 5 卡（Commerce 统一 + D2 修复）+ v4 §6（Human Approval 统一模型）+ v4 §10 Phase 5 注入项（BOOKING 审批统一外壳、booking_intent 并入 pending_approval）。
> 结论先行：**D2 两跳断的根因已在路由层精确定位——预订/比价两域的 prefilter 命中从不回写会话路由上下文（`_ROUTE_MODE_DOMAIN` 只登记三个域），子图反问后的短句（"下周一"）既拉不回也无人续填。修复可以在路由层最小闭环（不动冻结的 booking 确认链/状态机，不加新表）；v3 原案"booking_intent 进 TravelSessionState + BOOK 收编 supervisor"是更大的路由行为变更，建议 flag-off 基建本阶段落、路由切换单独评审。冻结面（确认链 cfp/TTL/金额互检、八态状态机、幂等账本、fail-closed 纪律）全程零接触。**

---

## 一、现状三入口地图（全量核实）

```
用户消息 → router_node（prefilter_chain.run_domain_prefilters）
  优先级：客服 > 旅游 > 选品 > 预订 > 商务 →（全部未命中）主 Router
  ├─ try_booking_prefilter（单条正则）→ route_mode="travel_booking" → booking 子图
  │    START → travel_booking_resolver → travel_booking_executor → END（无 checkpointer）
  │    resolver：detect_booking_action（confirm>status>new>unknown 正则）
  │              + 复用 commerce/extract 的日期/城市抽取 → 参数不足时出 clarification
  │    executor：BookingService（quote/confirm/status）+ reporter
  ├─ try_commerce_prefilter（单条正则，STOP K6）→ route_mode="travel_commerce" → commerce 子图
  │    START → travel_commerce_slot_filler → travel_commerce_executor → END（无 checkpointer）
  │    搜酒店/查机票库存比价（fail-closed 纪律冻结）
  └─ try_travel_prefilter → route_mode="travel" → travel 规划主图（有 checkpointer）
```

**跨轮恢复现状**：规划域有完整三通道（TravelPendingResolver cancel>avoid_patch>补槽>new_run + ContinuationResolver 活跃域拉回 + 域内 checkpoint）；**booking/commerce 两子图零跨轮机制**（设计如此："订单事实全在 PG，图状态不承载业务事实"——但澄清期的**参数**不在 PG，无人记住）。

**确认链（冻结面，实读核实）**：`BookingService.confirm` → `confirmation_fingerprint`（cfp 绑定）→ 指纹不一致拒 → `_quote_expired`（报价 TTL）→ 过期即 `confirmation_expired` 落事件 + 订单转 IN_DOUBT 通道；金额/币种/provider 全部服务端重读，不信任客户端。八态状态机 + CAS + 幂等账本（052 四表）原样。

## 二、D2 两跳断的精确机理（三缺口叠加）

用户实测："帮我预订大阪的酒店" → 命中 booking 子图 → 反问"哪天入住？" → 用户答"10月3日" → **掉域**。逐环节定位：

| # | 缺口 | 位置 | 实证 |
|---|---|---|---|
| G1 | **prefilter 命中不回写路由上下文**：`_ROUTE_MODE_DOMAIN` 只登记 travel/customer_service/selection_funnel 三个键，"travel_booking"/"travel_commerce" → `domain=None` → `mark_domain_turn` 早退（`if not session_id or not domain: return`）→ `active_domain` 永远为空 | `orchestration/graph/routing/prefilter_chain.py:20-25` | ContinuationResolver 第一条规则就是"无活跃任务 → 永不判定为延续" |
| G2 | **子图 clarify 不回写 pending**：booking 子图产出 `booking_clarification`（state 字段）后就 END；`_mark_route_from_update` 只认 update 里的 `_clarify.question`（CS 语义），booking/commerce 的反问从不进 `pending_question` | `travel/booking/graph_node.py` + `prefilter_chain.py:29-36` | 下一轮 Context Assembler 无从知道"上一轮问了什么" |
| G3 | **无挂起参数续填档**：即使句子被拉回 booking 子图，`detect_booking_action("10月3日")` = unknown → 再反问一遍（死循环式体验）；缺"上一轮在等什么参数"的结构化挂起 | `travel/booking/graph_builder.py::detect_booking_action` | booking_action 枚举 new/confirm/status/unknown 无 pending 档 |

**修复选项**：

- **方案 A（推荐）：路由层最小闭环**。G1 补两个域映射（+3 行）；G2 两个子图适配器在执行后按 `booking_status=="clarify"` 回写 `mark_domain_turn(domain=..., pending_question=...)`（软失败纪律同款）；G3 新增**结构化挂起 `booking_intent {kind, missing_slots, collected}`** 存 ConversationContext（routing_context 扩展字段，原子 mutation 已有，**新表 0 张**）+ booking resolver 增 pending 续填档（挂起存在 → 先试续填 → 填齐走原 new 流，语义与首跑完全一致）。全程不动冻结层，回归面=booking 39 + commerce 79 + 路由守护测试。
- **方案 B（v3 原案）：booking_intent 进 TravelSessionState**。要求预订请求改道进 travel 规划主图（checkpointer 在那边），booking 子图降为内部服务——即"BOOK 意图收编 supervisor"的完整版。改动大：动 supervisor、动路由、动两子图入口，回归面含主链路由；收益是意图层统一。**建议作为 TRAVEL_UNIFIED_INTENT 的远期形态，不与 D2 修复捆绑**。

## 三、目标结构（v3/v4 对照与落点）

| v3/v4 要求 | 现状 | Phase 5 落点（建议） |
|---|---|---|
| commerce 子图入口收敛 `commerce_agent.py`（kind=hotel/flight/ticket 参数 + provider adapter 面） | 子图两节点+service 层完整（冻结内部模块） | **薄封装**：`travel/agents/commerce_agent.py` 统一入口（kind 参数分发到既有 commerce/booking service 层），Agent 纪律同 Phase 3（零 providers import/零 state 读写）；子图节点改调 Agent，内部模块零改动 |
| BOOK 意图收编 supervisor（flag `TRAVEL_UNIFIED_INTENT`） | `core/actions.py` 已留位（SupervisorAction.BOOK + ENTRY_ACTION_MAPPING 注释"Phase 5 追加"）；supervisor 无意图层 | **flag-off 基建**：判定函数 + 映射登记 + 测试，**路由切换（booking_prefilter 改道进 travel 主图）延后单独评审**——那是路由行为变更，不该和 D2 修复捆绑 |
| booking_intent 并入 pending_approval 统一模型（v4 §6） | 无 HumanApprovalRequest 契约、无 pending_approval state 字段 | 见决策点 D3——D2 走方案 A 后，BOOKING 挂起已由 ConversationContext 承载；v4 自己明确「BOOKING 审批不占用 plan lifecycle（交易域状态机自治）」。**pending_approval（规划域审批：MUST_GO_CONFLICT/PLAN_CHANGE/BUDGET_EXCEED）与 BOOKING 是两个域的挂起，不该强行合成一个存储**；统一的是「类型枚举+结构+裁决顺序」这一层契约 |
| BOOKING 审批统一外壳（现有确认链不动） | 确认链完整冻结 | 类型登记进审批契约（type=BOOKING 指向现有确认链），零新机制 |
| ticket 交易面 | facts 契约 implemented=False（BLOCKED） | 维持不开放（等外部供应商，见 §八） |

## 四、v4 注入遗留盘点（Phase 3 未做项的归属建议）

| v4 §10 注入项 | 状态 | 建议 |
|---|---|---|
| supervisor stage 推进顺带写 `plan_status`（四态自环与推进） | **未做**——`core/plan_lifecycle.py` 零消费方（只 core/__init__ 提及） | 随 Phase 5 落（supervisor 反正要动的话）或独立小 commit；纯加法零风险 |
| 必去冲突→`pending_approval(MUST_GO_CONFLICT)`→WAITING_CONFIRMATION | 未做 | **建议延 Phase 6/8**（与 validator SOURCE_STALE 渲染、assistant 同属"交付面"）；除非 D3/D4 拍板本阶段落 |
| `travel_llm_calls_total` 计数器 | 未核实（quality_metrics 未查到该指标） | 独立小项，随任一阶段顺带 |
| research freshness 评测集 | 用户已拍板延后 | 保持延后 |

## 五、冻结面清单（Phase 5 全程零接触）

booking 八态状态机+CAS+幂等账本+052 四表｜确认链 cfp 绑定/TTL/金额互检｜commerce fail-closed 纪律（Decimal+ISO4217/sha256 指纹/确定性排序/deeplink 白名单）｜providers 七态契约｜checkpoint 跨轮契约｜主图 builder 9 节点｜STOP F run_expert_safely/遥测｜544+ 测试基线（commerce 79 + booking 39 = 118 交易域用例零回退为验收门）。

## 六、实施顺序（Commit A/B/C，各自测试+提交）

| Commit | 内容 | 验收门 |
|---|---|---|
| **A：D2 路由层最小闭环** | ① `_ROUTE_MODE_DOMAIN` 补 travel_booking/travel_commerce；② 两个子图适配器执行后回写 `mark_domain_turn`（含 pending_question）；③ `booking_intent` 结构化挂起进 routing_context（扩展字段，向后兼容 .get）；④ booking resolver 增 pending 续填档（挂起→续填→填齐走原 new 流）；⑤ 守护测试（两跳对话回归：反问日期→"10月3日"→续填成功） | booking 39 + commerce 79 全绿 + test_router_prefilter_order + pending_resolver 测试 + 新增两跳对话用例 |
| **B：审批契约 + flag-off 基建** | ① `core/contracts.py` 加法新增 HumanApprovalRequest 结构（类型枚举五类；BOOKING 类型只登记指向现有确认链）；② `pending_approval` state 字段（flag 组，flag off 不接线）；③ supervisor BOOK 判定 + ENTRY_ACTION_MAPPING 登记（flag `TRAVEL_UNIFIED_INTENT` off=零行为）；④ （若 D5 确认）plan_status 顺带写 | core_contracts 既有用例零回退 + 新契约测试 + travel 分块 |
| **C：commerce_agent 收口 + 收口测试** | ① `travel/agents/commerce_agent.py` 薄封装（kind 分发，Agent 纪律静态断言扩展）；② 两子图节点改调 Agent（编排/遥测逐字保留）；③ 边界契约测试（Agent 不 import providers、冻结域不被越界 import）+ Completion Report | commerce 79 + booking 39 + 全量 travel 分块 + 一致性四测试 + lock --check |

## 七、待确认决策点

| # | 决策点 | 推荐 |
|---|---|---|
| **D1** | **D2 修复路径**：方案 A 路由层最小闭环（ConversationContext 挂起，新表 0 张）vs 方案 B v3 原案（booking_intent 进 travel 主图 state + 改道） | **方案 A**；方案 B 作为 TRAVEL_UNIFIED_INTENT 远期形态 |
| **D2** | BOOK 收编 supervisor：本阶段只落 flag-off 基建（判定+映射+测试，不开路由）vs 完整切换 | **flag-off 基建**；路由切换单独评审（那是行为变更） |
| **D3** | HumanApprovalRequest 契约：本阶段落「契约+枚举+BOOKING 类型登记」（结构进 contracts.py 加法，pending_approval 字段 flag off 不接线）vs 完整审批模型（含 resolver approval 档） | **契约+登记先行**；resolver approval 档与 MUST_GO_CONFLICT 同批（规划域审批真实消费时） |
| **D4** | MUST_GO_CONFLICT 路径（v4 Phase 3 注入遗留）：随 Phase 5 vs 延 Phase 6/8 | **延后**（交付面，与 assistant/渲染同批） |
| **D5** | plan_status 顺带写（v4 Phase 3 注入遗留）：随 Phase 5 supervisor 触达面顺带落 vs 独立小 commit | **独立小 commit**（避免与 D2 修复混批） |

## 八、外部依赖（等用户处理，不阻塞本阶段）

| 项 | 需要什么 | 现状 | 办好后接线点 |
|---|---|---|---|
| 真实酒店供应商 | 供应商合同 + API 凭据 | fake-only（`HOTEL_PROVIDER_SCOPE=OUT`，mode=live 恒 DISABLED，绝不以 fake 冒充生产） | `providers/travel/live/` hotel adapter 契约位已留，接入即走七态 |
| 真实机票供应商 | 同上 | 同上 | 同上 |
| ticket 票务事实源 | 腾讯 WebService 无此字段（J0-4 实测），需第三方票务源 | 契约冻结 unknown | `capabilities.py::ticket.facts` |
| 城市级 POI live 源 | 采购事项 | BLOCKED 台账 | poi 链头追加（Router 链账已备） |

**Phase 5 本体零新增外部依赖**（交易面 fake-only 照旧，fake 走与真实同一套执行流）。

## 九、本轮已核实事实清单

1. 路由优先级与四连 prefilter（prefilter_chain.py:231-296）；booking_prefilter 单条正则 + CS/行程让路词表；
2. `_ROUTE_MODE_DOMAIN` 缺 travel_booking/travel_commerce 两键（G1 实证，20-25 行）；`mark_domain_turn` 早退条件（routing_context.py:95）；
3. booking 子图两节点无 checkpointer；`detect_booking_action` 四值正则；参数抽取复用 commerce/extract；
4. 确认链：cfp 绑定 → `_quote_expired` → `confirmation_expired` 事件（service.py:102-188），全部服务端重读；
5. commerce 抽取器 `detect_commerce_intent`/`extract_hotel_params`/`extract_flight_params`（extract.py:53-147）；
6. `core/actions.py`：SupervisorAction 五枚举已冻结、BOOK 注释"Phase 5 追加（flag 控制）"；
7. `core/plan_lifecycle.py` 存在但零消费方（v4 Phase 3 注入遗留实证）；
8. `core/contracts.py` 无 HumanApprovalRequest（Phase 1 只落 TravelContext/Evidence）；
9. TravelPendingResolver 五档顺序 cancel>avoid_patch>补槽>new_run（travel_pending_resolver.py，Phase 5 若走 approval 档在此扩展）；
10. ContinuationResolver 只认 active_domain + `_try_continuation` 只特判 travel 域（continuation.py:60-70）；
11. 测试基数：commerce+booking 合计 118 用例；prefilter 顺序有守护测试钉住。
