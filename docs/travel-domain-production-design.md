# 企业级 Travel Agent Domain 生产设计（Travel Domain 重构目标态）

> 状态：设计稿 v1（2026-09-29）。前置审计见 `docs/travel-domain-current-audit.md`（Phase 0）。
> 四条优先级（本文档一切取舍的裁决标准）：**稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。**

---

## 0. 范围、原则与术语决议

**范围**：`backend/travel/` 域内重排 + 理解层升级 + 数据面接入。**不改动**主图 builder（9 核心节点冻结）、不动 `shared/provider_idempotency.py` 冻结层、不重写 booking 状态机/commerce 归一门/validator 六轴/repair 防震荡/checkpoint 跨轮契约（审计 §9 生产资产清单）。

**术语决议（2026-09-29 用户拍板，G4 台账登记项）**：旅游域内统一叫 Agent——**TravelSupervisor 是域的主 Agent（调度者），域内各职能节点是子 Agent（sub-agent），共同被主 Agent 调用**；主图视角下整个旅游域是被上层编排调用的一个域单元。落地形态 = LangGraph 域图节点 + 统一 BaseAgent 契约（命名、输入输出契约、超时预算、降级策略、遥测五要素）。既有四层规范中"勿把所有节点统称 Agent"的旧口径按本决议演进：Phase 1 更新该规范旅游域章节为"域主 Agent 调度子 Agent"模型；capability 面不变（`travel.poi_search` 唯一注册项维持）。现状代码 `experts/` 的"专家"称谓是历史名，Phase 3 随目录迁移改为 `*_agent.py`。

**分层原则**（用户原则 → 落地规则）：
- Agent 负责推理与流程控制：读 state → 决策 → 写 state，禁止 Agent 互相 import 调用（一律 supervisor 分发）；
- Tool 负责确定性能力：`travel.*` 纯函数/薄封装，独立可测试；
- Provider 负责外部数据：禁止 Agent/Tool 直接处理 HTTP 异常（七态契约唯一出口）；
- Validator（Quality Gate）只判定不修改；Memory 只存跨 trip 长期偏好；Booking 域负责交易安全（幂等/状态机/重验价不动）。

## 1. 目标架构图

```
                          ┌───────────────────────────────────────────────┐
主图 router（不动）        │              Travel Domain                    │
route_mode="travel" ────→ │  travel/core/supervisor.py  TravelSupervisor  │
route_mode="travel_*" ──→ │  意图层: PLAN|MODIFY|QUERY|BOOK|CANCEL        │
                          │  调度层: 现有 decide() stage 机（原样保留）     │
                          └───────┬───────────────────────────┬───────────┘
                                  │ action ∈ {PLAN,MODIFY,QUERY}│ action ∈ {BOOK} / 交易意图
                                  ▼                             ▼
              ┌──────────────── Planning Domain ─────────┐  ┌── Transaction Domain ──┐
              │ requirement_agent   （规则+LLM 两层）      │  │ commerce_agent         │
              │ destination_agent   （城市解析+推荐）      │  │  (比价/深链, 现子图平移) │
              │ poi_agent           （候选池，禁幻觉）      │  │ booking_agent          │
              │ route_agent         （排程，现 transit）   │  │  (幂等下单, 现子图平移) │
              │ weather_agent       （三态，失败不阻塞）   │  │ payment_agent          │
              │ budget_agent                             │  │  (确认/收口/对账入口)   │
              │ risk_agent                               │  └────────────────────────┘
              │ repair              （quality gate 执行侧）│
              │ quality_gate        （现 validator 升格）  │
              │ reporter                                 │
              └──────────────────────────────────────────┘
共享底座：core/{state, contracts, events, agent_base}   tools/（travel.* Domain Tool）
        providers/{poi,map,weather,hotel,flight}（七态契约层原样迁入）   memory/（长期偏好）   evaluation/
```

调用纪律：`Supervisor → Agent → Tool → Provider` 单向；跨域只经 supervisor action 分发；Agent 间数据传递只走 state 键（现状纪律延续）。

## 2. Agent 职责

### 2.1 TravelSupervisor（`core/supervisor.py`）

现有 `decide()` stage 机**原样保留**（护栏/决策顺序/`repair_stalled` 一律不动），新增**入口意图层**：

```json
{"action": "PLAN | MODIFY | QUERY | BOOK | CANCEL"}
```

| action | 判定来源 | 去向 |
|---|---|---|
| PLAN | prefilter 命中 / TravelSessionState 无 plan | 规划链（requirement → … → reporter） |
| MODIFY | pending_resolver 的 avoid_patch / Continuation / 指纹变化 | 规划链（planning_reset 语义不变，产出新 plan_version） |
| QUERY | 纯查询意图（"鼓浪屿门票多少"→ poi 检索/知识库，不出整案） | poi_agent 单步 / knowledge 检索（Phase 3 起支持） |
| BOOK | commerce/booking 意图（现独立 prefilter 语义收编） | Transaction Domain |
| CANCEL | `is_cancel_run_query`（现逻辑平移） | 取消当前 run（CANCEL_TRAVEL_RUN 原子 mutation 保留） |

迁移要点：Phase 1 只立 action 枚举与映射表（现有全部入口**等价归入**对应 action，零行为变化）；Phase 5/6 才把 booking/commerce 的入口判定从独立 prefilter 收编为 supervisor 分发，**TravelSessionState 增加挂起的 booking 意图**（修 D2：反问日期后下一轮不再依赖重新正则命中）。判定规则全部规则化（确定性优先），不引入 LLM 意图分类。

### 2.2 Planning Domain 九 Agent

| Agent | 现状映射 | 变更 |
|---|---|---|
| **requirement_agent** | slot_filler 规则层全量保留 | **两层抽取**：第一层现有规则（含日期遮蔽等事故修复，原样）；第二层 LLM Structured Output（`TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 off）——仅当规则层 required 槽缺失或低置信时触发；LLM 的 destination 只能在城市白名单/候选池内产出（防幻觉），数值字段范围校验，失败/超时回落规则结果+追问。必须覆盖："两个人"（party_size=2）、"带孩子"（pace→relaxed + 偏好亲子）、"慢节奏"（已有）、"不购物"（avoid+偏好负标签 shopping）、"不爬山"（负标签 outdoor/hiking）。词表缺口 D3/D4/D5 在规则层同步补 |
| **destination_agent** | slot_filler 的城市解析 + recommend.py | 独立成 Agent：城市归一（白名单/别名）、不支持城市明示、追问时推荐榜（两消费点不变） |
| **poi_agent** | experts/poi.py | 平移 + 契约化。**铁律：POI 只能来自 Provider/种子库/RAG，禁止 LLM 幻想景点**——输出 CandidatePOI（现 Poi 契约改名的别名期），source/verification_status 强制携带 |
| **route_agent** | experts/transit.py | 平移改名（语义修正：它做的是地理排序+时刻排程+路线），`rebuild_days` 唯一重排实现不变 |
| **weather_agent** | experts/weather.py | 平移 + **三态输出**：`available`（SUCCESS 且 fresh）/ `stale`（stale-if-error 命中）/ `unavailable`（七态失败或 DISABLED）——映射 ProviderResult.status × Freshness，零新概念。失败绝不阻塞规划，reporter 按 weather_status 渲染（**顺带修 D6**：risk 免责文案按 weather_status 裁剪） |
| **budget_agent** | experts/budget.py | 原样平移（只算不判） |
| **risk_agent** | experts/risk.py | 原样平移（RAG 原文摘录，不用 ask()） |
| **repair** | repair.py | 原样平移（目标树未单列——它是 quality_gate 的修复执行侧，生产资产冻结） |
| **quality_gate** | validator.py | 六轴逻辑原样平移；对外结论统一三态：`PASS`（无 error）/ `WARNING`（仅 warning，放行+披露）/ `REJECT`（有 error，触发 repair）。现有 errors/warnings/decision_required 三级是三态的内部实现，`passed` 语义不变。**新增**：`POI_UNVERIFIED` 检查项（腾讯补全的 unverified POI 计 warning，不 error）——对应"数据：虚假 POI"（池外 POI 已由现有 POI_NOT_IN_CANDIDATES error 覆盖） |
| **reporter** | reporter.py | 平移 + weather_status/quality 结论字段渲染 |

### 2.3 Transaction Domain 三 Agent

- **commerce_agent**：现 commerce 子图平移（2 节点内部结构不变，fail-closed 归一门/确定性排序/deeplink 白名单冻结）。
- **booking_agent**：现 booking 子图平移（八态状态机/幂等账本/三模型契约/重验价**一律不动**）。
- **payment_agent**：**不是新实现**——是现有 `confirm_and_execute`（确认门+执行+收口）与 `reconciliation`/`webhook` 的 Agent 化命名重组，职责：确认校验（cfp 绑定/TTL/金额 Decimal 互检）→ 幂等执行 → 结局收口（SUCCEEDED/NOT_SENT/REJECTED/UNKNOWN→IN_DOUBT）。**同时补生产接线缺口**：webhook 接收与对账/人工裁决的 admin API 入口（Phase 6，详见 §12）。

## 3. Tool 列表（Domain Tool，`travel/tools/`）

规则：旅游内部确定性能力=Domain Tool；第三方=Provider；只有跨系统共享能力考虑 MCP（当前无——`/api/map/*` 已覆盖前端共享面，MCP 维持 2 server 不增）。

| Tool | 现状 | 目标 |
|---|---|---|
| `travel.search_poi` | `tools/travel/poi.py`（Skill 底座已在用） | 平移，签名冻结 |
| `travel.calculate_route` | `tools/travel/routing.py`（estimate_leg/route_km） | 平移，route_km 保持纯函数（O(n²) 不走网络） |
| `travel.get_weather` | **缺位**（expert 直调 Provider） | 新增薄封装（包装 Provider forecast_payload + 三态归一），weather_agent 与 REST `/api/map/weather` 共用 |
| `travel.calculate_budget` | `tools/travel/cost.py` | 平移 |
| `travel.retrieve_knowledge` | `tools/travel/knowledge.py` | 平移 + 补 4s 显式预算 |
| `travel.preferences` | `tools/travel/preferences.py` | 迁 `travel/memory/`（§10） |
| `travel.resolve_place` / `travel.live_leg` | `tools/travel/live_map.py` | 平移（归入 map provider 传输面） |

Tool 契约三规沿用仓库规范：错误 JSON 可读可重试、「查不到」与「查不了」分开、独立可测试；一律不 `@tool` 注册主图（capability 面不变，`travel.poi_search` 唯一）。

## 4. Provider 列表（`travel/providers/`，七态契约层原样迁入）

| 子目录 | Provider | 状态 |
|---|---|---|
| `poi/` | SeedPOIProvider（唯一候选源）+ TencentPlaceProvider（must_go 补全，unverified 标注） | 保留；**Phase 4+ 扩展点：真实城市级候选源**（待供应商调研，契约已留好——实现 `search(city)` 即插） |
| `map/` | TencentTransitProvider（live 通道）+ LocalEstimate（兜底） | 保留；`.env TRAVEL_USE_LIVE_MAP` 还原为 true（Phase 4 验收项） |
| `weather/` | TencentWeatherProvider（主）+ QWeatherProvider（备，FallbackWeatherProvider 组合） | 保留（f4f9628 刚冻结） |
| `hotel/` | FakeHotelSearchProvider；真实适配器位 | fake 保留；live 仍 BLOCKED_BY_EXTERNAL_PROVIDER（不因重构假装解决） |
| `flight/` | FakeFlightSearchProvider；真实适配器位 | 同上 |
| `booking/` | 三 profile fake + 契约 | 冻结平移 |
| `runtime/`（live/ 更名） | result/contracts/cache/quota/resilience/telemetry/errors/health | **冻结平移**，TTL 调整见 §11 |

规范不变：Agent/Tool 禁止自行处理 HTTP 异常，一切经七态 `ProviderResult`；能力账 `capabilities.py` 随迁移更新路径。

## 5. State 设计（TravelSessionState）

**决策：不新造存储。** TravelSessionState = 图 state 的显式 schema（现 TravelGraphState 的重组 + 补字段），持久化继续走 PostgresSaver checkpoint（三域共表、TTL 7 天、可序列化纪律不变），run 摘要继续同步 ConversationContext（graceful reconstruction 基底不变）。理由：现有跨轮契约已通过三次事故修复并有 18+20 个测试钉住，重造存储是纯风险无收益。

```python
# core/state.py（新增字段加粗）
TravelSessionState:
  trip_id: str              # 新增。uuid7，NEW_RUN 时生成；同会话多 trip 可区分；进 trace/run 摘要
  brief: TravelBrief        # 含新增负偏好标签（avoid_categories: ["shopping","hiking"]）
  brief_fingerprint / brief_change_reason / brief_changed_fields   # 原样（指纹口径不变）
  current_plan: Itinerary   # 原样（版本链 stamp_version 不动——天然满足"每次修改生成新版本，不覆盖旧计划"）
  confirmed_items: list     # 新增。用户裁决结构化沉淀：[{poi_id, name, decision: keep|drop, source: user_decision|must_go, decided_at}]
  plan_version: int         # 从 current_plan.plan_version 提升（读写唯一入口）
  history: list             # 新增。版本摘要 [{plan_version, brief_version, change_reason, created_at, quality: PASS|WARNING|REJECT}]（不含全文，控制 checkpoint 体积）
  # —— 以下全部原样 ——
  candidates/day_plan/itinerary/validation/repair_*/notes/knowledge_refs
  stage/step_count/expert_history/supervisor_decision/persistence_status
  user_message/user_id/session_id/conversation_id/travel_route/reconstruct_brief
  booking_intent: dict|None # 新增（Phase 5）。挂起的交易意图 {kind: hotel|flight, slots, quote_ref}——修 D2 的结构载体
```

契约纪律：`new_travel_graph_input()` 只放本轮输入不预置产物默认值（事故教训保留）；读一律 `.get()`；Pydantic 契约（brief/poi/itinerary/validation）整体迁 `core/contracts.py`，`computed_field` 出网纪律不变。

## 6. 数据库设计

**新增 0 张表**。现状 7 组表全部保留（审计 §6）：checkpoint 三表、booking 四表（052）、`ai.idempotency_records`、`travel_preferences`、feedback 表。

- `travel_preferences` 扩展不改表：`preferences JSONB` 内加负偏好（`avoid_categories`），upsert 逻辑扩展（Phase 3，向后兼容——JSONB 自由字段）。
- booking 表/幂等表/immutable trigger 一律不动。
- 明确不做：trip 历史表（history 在 checkpoint 内、ConversationContext 摘要已覆盖查询面；确有"列出我的行程"产品需求时再立表，届时 trip_id 已是现成主键）。

## 7. 异常策略

| 层 | 策略 |
|---|---|
| Provider | 七态唯一出口（现状）；UNAVAILABLE/RATE_LIMITED/TIMEOUT → 上层降级，**绝不用 fake 冒充 live、绝不编造数据** |
| Tool | 返回 `{"error": ...}` 结构化错误，「查不到」（NOT_FOUND，可结束）与「查不了」（UNAVAILABLE，可降级）分开（现状规范延续） |
| Agent | `run_expert_safely` 扩展：异常→`status=failed` 不穿透（现状）+ **超时→`status=degraded`**（新增，§8）；每个 Agent 声明降级行为（如 weather=继续规划、knowledge=只留免责） |
| 规划链 | 任一增强型 Agent 降级不阻塞出单；quality_gate REJECT → repair（≤2 轮）→ 仍 REJECT 则如实输出+披露未解决项；候选池空/城市不支持 → 如实说明 |
| 交易域 | 维持现状 fail-closed：UNKNOWN→IN_DOUBT 绝不猜成败、无法复核禁止假定可购、webhook 迟到/找不到订单→quarantine |
| 全局 | 禁止 `except Exception: pass`；软失败必须留 notes/事件（现状纪律） |

## 8. 超时策略

| 层 | 预算 | 实现方式 | 超时行为 |
|---|---|---|---|
| 单 Tool（Provider 调用） | 3-5s | 已有：place 3s / route 4s / **weather 6s→5s**（Phase 4 调 `TRAVEL_WEATHER_TIMEOUT_S`）；knowledge 补 4s 显式预算 | 七态 TIMEOUT → 降级 |
| 单 Agent | **10s** | 新增：`BaseAgent` 契约带 `timeout_s`，`run_expert_safely` 包 `asyncio.wait_for`（标注：五专家现为同步函数，需包线程执行或改造 async——Phase 3 实现时择一，优先线程+future 不改函数签名） | `status=degraded` + note「{agent} 超时降级」，链路继续 |
| 整次规划 | **30s** | 新增：图入口注入 `deadline_at`（monotonic），supervisor `decide()` 在每次放行前检查：超限→强制 REPORT（输出已生成部分+披露「受时间预算限制」） | 部分成果 + 如实披露，绝不静默截断 |
| REST `/api/travel/plan` | 现前端 55s > 后端 30s | 不变（30s 内必返回，55s 只是前端兜底） | — |

说明：30s 与现有 `TRAVEL_MAX_STEPS=14`/recursion_limit 是两层护栏（步数护栏防逻辑死循环、deadline 防单步拖慢累积），互不替代。

## 9. Retry 策略

| 对象 | 策略 |
|---|---|
| Provider 网络调用 | 现状保留：传输层重试 1 次退避 0.4s + 3 次连续失败熔断 60s + stale-if-error（600s 宽限）；**不加重试**（单调用预算 3-5s 内已无空间，多重重试只会撞 Agent 10s 上限） |
| 规划链内 repair | 现状保留：≤2 轮 + `repair_stalled` 防震荡（防震荡逻辑是事故资产，禁止"优化"掉） |
| 业务终态 | booking 业务终态异常不重试（仓库规范）；可重入仅 NOT_SENT/not_found_safe_to_retry（现状） |
| 会话级 | 用户重发/重试走 NEW_RUN/继续语义（现有 pending_resolver 判定），不做自动整案重跑 |
| LLM（requirement 第二层） | **零重试**：失败即回落规则层+追问（LLM 是增益不是依赖） |

## 10. Memory 策略

- **存**（跨 trip 长期）：偏好标签（preferences JSONB，含新增负偏好 avoid_categories）、pace、diet、lodging、transport、origin。写入时机不变（用户显式表达才写，upsert 不覆盖未表达字段）。
- **不存**（单次临时信息）：目的地/天数/预算/日期等 brief 槽位、具体行程、booking 订单事实（在 PG 订单表）、对话原文。
- **读取**：跨轮首轮预填（现状）；LLM requirement 第二层把预填偏好作为上下文（不改变"显式表达优先"合并序）。
- **位置**：`travel/memory/`（preferences.py 迁入 + 负偏好扩展）。
- 边界不变：偏好读写失败软降级绝不挡规划；预填发生在指纹计算之前（防每轮重排抖动的事故教训保留）。

## 11. 缓存（Redis，`providers/` runtime 层现状调整）

| 数据 | 现状 TTL | 目标 TTL | 说明 |
|---|---|---|---|
| POI（种子/候选池） | place 600s | **24h** | 种子与解析点位变化极慢；负缓存（NOT_FOUND）维持 60s |
| 路线 | 120s | **30min** | 用户要求；通勤估算兜底永远可用 |
| 天气 | 300s | **10min** | 恰好一致，落配置 |
| 价格（commerce offer） | 120-180s | **1-5min**（hotel_avail 120s / price 180s / flight 120s 维持） | 已在区间内 |
| stale-if-error 宽限 | 600s | 不变 | — |

全部经现有 `runtime/cache.py` 分数据 TTL 机制，改配置不改代码。

## 12. 测试方案

- **回归门（每阶段必跑）**：`cd backend && python -m pytest tests/travel/ tests/orchestration/ -q --no-cov` + 四个一致性测试（`test_registry_consistency.py` `test_layer_consistency.py` `test_adr0001_dual_registry_merge.py` + base_output）+ 金标 `tests/travel/test_quality_golden.py`。
- **阶段专项**：Phase 1 加域边界守护测试（锁定新目录映射，防新文件再堆回根目录——对标现有 `test_domain_semantic_consistency.py` 风格）；Phase 2 加 LLM 抽槽评测集 `evaluation/datasets/travel/slot/cases.jsonl`（≥50 例，含 D3/D4/D5 口语 + "两个人/带孩子/慢节奏/不购物/不爬山"必过集；LLM 关/开双跑口径）；Phase 3 加超时注入测试（fake 慢 provider 验证 10s/30s 降级不挂死）；Phase 5/6 复用四套评测 runner（B1-B20 生产门 T1-T12=0 不许回退）。
- **纪律**：只 mock 外部边界（provider/LLM/clock）；新功能带回归测试；强断言；测试 import 与生产 import 同 commit 更新。
- **Phase 7 收口**：全量回归（分块，遵守多会话并发纪律）+ `e2e_demo.py` + 联网金标 `python -m backend.evaluation travel`（阈值不放松）+ 实机 SSE 20 轮走查（含 D1-D3 回归验证）。

## 13. 上线方案

1. **全程 flag 灰度**：`TRAVEL_REQUIREMENT_LLM_ENABLED`（默认 off）、`TRAVEL_SESSION_STATE_V2`（Phase 3，控制新字段启用）、`TRAVEL_UNIFIED_INTENT`（Phase 5，控制 booking/commerce 入口收编）；任一异常按 flag 回退，旧行为全程保留到 Phase 7。
2. **顺序**：先规划域（Phase 2/3，纯域内）→ Provider 归位（Phase 4）→ 交易域（Phase 5/6，有 flag）→ 全量验收（Phase 7）。每阶段独立 commit（pathspec 双重限定），阶段内测试绿才进下一阶段。
3. **回滚**：每阶段 = 一组可独立 revert 的 commit；shim 层保证旧 import 在 Phase 7 前始终可用；flag 关闭即回旧行为；数据面零迁移（新表 0 张，回滚无数据风险）。
4. **上线检查单**：`.env` 还原 `TRAVEL_USE_LIVE_MAP=true`；`/health` travel_providers 组件 healthy；金标联网跑 ≥0.98 硬约束；D1-D3 实机复测通过；`docs/2026-09-16-四层规范` 旅游域章节与 AGENTS.md 旅游域段落同步更新（Phase 7 文档任务）。

## 14. 迁移阶段计划（Phase 1-7 执行卡）

> Phase 0（审计）已完成 = 本文档 + 审计文档。

| Phase | 改动 | 验收 | 回滚 |
|---|---|---|---|
| **1 建立边界** | 建 `core/ planning/ tools/ memory/ evaluation/ providers/` 骨架 + `core/agent_base.py`（BaseAgent 契约）+ `core/events.py`（事件出口统一）+ supervisor action 枚举与现有入口映射表（零行为变化）+ 域边界守护测试 + 四层规范/本文档术语对齐 | 全量 travel 测试绿；守护测试通过；无行为 diff（对比 e2e 回放） | revert 单 commit（纯新增文件+规范文档） |
| **2 Requirement Agent** | slot_filler 规则层 git mv → `planning/requirement_agent.py`（旧路径 shim）；destination_agent 拆分；D3/D4/D5 词表补丁；负偏好字段与映射（"不购物/不爬山"）；LLM 第二层（flag off，仅框架+评测集） | slot_filler 49 用例绿（改 import 后）；slot 评测集规则层基线落盘；金标不回退 | revert commit；shim 保证旧 import 不断 |
| **3 Planning Agents** | 五专家+validator→quality_gate+repair+reporter 逐个 git mv（**每个 agent 独立 commit**）；BaseAgent 契约接入（超时 10s/降级声明/weather 三态字段）；整图 deadline 30s；TravelSessionState 新字段（trip_id/confirmed_items/history，flag 控制）；memory/ 迁入+负偏好 | 每步测试绿；金标不回退；超时注入测试过；D6 修复验证 | 逐 commit revert |
| **4 Provider 归位** | `backend/providers/travel/` → `travel/providers/`（git mv，旧命名空间 shim；先确认 `backend/providers/` 无其他域占用）；weather TTL 10min/route 30min/POI 24h；weather 超时 6s→5s；`.env TRAVEL_USE_LIVE_MAP=true` 还原；`travel.get_weather` Tool 补位 | provider_layer 30 用例 + travel-provider 探针 8/8；真实腾讯探针 phase A 过；金标联网跑不回退 | revert + `.env` 回退 |
| **5 Commerce 收编** | commerce 子图平移 `commerce/commerce_agent.py`；意图入口收编进 supervisor（flag `TRAVEL_UNIFIED_INtent`）；booking_intent 挂起槽（修 D2 结构部分） | commerce 79 用例绿；H/F 评测 runner 不回退；两跳对话实验通过 | flag off + revert |
| **6 Booking 收编** | booking 子图平移（状态机/账本**零改动**）；payment_agent 命名重组（confirm/reconcile/webhook 收口）；**补 admin API**：webhook 接收入口 + IN_DOUBT 对账/人工裁决端点（走管理端鉴权 + 写操作审批门 `ensure_approved`） | booking 39 用例绿；B1-B20 生产门 T1-T12=0；webhook HMAC 实测；审批门生效验证 | flag off + revert（补的 API 端点独立 commit） |
| **7 E2E 收口** | 删 shim；死代码清理（reschedule_after_repair/quote['nights']/Phase7 预留契约）；全量回归+联网金标+实机走查；AGENTS.md/四层规范/README 口径更新；`.env` 检查单核验 | §13 检查单全绿；全量测试分块跑完（遵守并发纪律） | 不适用（收尾阶段） |

**明确不做**（防复杂度失控）：不引入新存储/新消息队列/新框架；不加 Planner LLM 重排（candidate_plans 维持占位）；不给 POI/Route/Budget/Risk 加 LLM（确定性优先）；不动主图 builder；不动 booking 冻结层语义；不因重构假装解决真实供应商 BLOCKED（hotel/flight live 仍是待签约事项，非本次范围）。
