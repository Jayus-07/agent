# 旅游域 Agent Runtime 目标设计 v3（7-Agent 收敛版）

> **状态更新（2026-09-29）**：现行冻结版 = `docs/travel-domain-agent-runtime-design-v4.md`（v4 在本文基础上冻结企业级治理层：TravelPlan 生命周期 / Plan Version 管理 / Evidence 事实可信模型 / Tool Governance / Human Approval 统一模型 / Evaluation 与 Cost-Latency Budget / ProviderRouter）。本文 §1/§2/§3/§5/§7 架构与执行卡被 v4 §10 承接继续有效，执行时以 v4 + 本文组合阅读。
>
> 状态：设计稿 v3（2026-09-29）。前置：`docs/travel-domain-current-audit.md`（审计，文件/行号以它为准）、`docs/travel-domain-design-v2-mapping-plan.md`（v2 映射与裁决，其架构章节被本文取代，差异裁决 A1-A3/R4/R6 等继续有效）。
> 用户拍板（2026-09-29）：不保留"五专家规则引擎"形态，升级为企业级 Travel Agent Runtime；**不拆过细 Agent，不复制 Hotel/Flight/Ticket 三套 Agent**；五专家能力迁移为 Agent 内部 Service/Tool；Memory 横向能力不设节点；Route Optimization 轻量算法（不引 OR-Tools）。
> 四优先级不变：**稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。**
> 硬约束：validator / repair / provider 七态契约 / booking 状态机 / checkpoint 跨轮契约 / 544 测试基线 **零删改核心逻辑**（只允许加法式扩展）；禁止大规模推倒重写。

---

## 1. 新架构图

```
主图 router（不动，route_mode="travel" / "travel_*"）
        │
        ▼
┌────────────────────────────────────────────────────────────────────┐
│ ① Travel Supervisor Agent（域主 Agent，travel/core/supervisor.py）   │
│    意图层：action = PLAN | MODIFY | QUERY | BOOK | CANCEL            │
│    调度层：现有 decide() stage 机原样保留 + 整图 30s deadline + 护栏   │
└──────┬───────────────┬──────────────────┬─────────────────────────┘
       │ PLAN / MODIFY │ QUERY            │ BOOK / CANCEL
       ▼               ▼                  ▼
┌──── 规划链（串行，节点间经 supervisor） ────┐   ┌─────────────┐  ┌──────────────┐
│ ② Requirement Agent                      │   │ ⑦ Travel    │  │ ⑥ Commerce   │
│    规则层全量保留 + LLM 结构化抽取(flag off)│   │ Assistant   │  │ Agent        │
│    clarify 内置 / 调用 Memory 预填与保存   │   │ Agent       │  │ kind 参数：   │
│ ③ Research Agent                         │   │ 行程问答/查询 │  │ hotel/flight │
│    候选池构建 + 天气三态 + 知识/风险摘录    │   │ 只引事实     │  │ /ticket      │
│ ④ Planning Agent                         │   └─────────────┘  │ 统一状态机，  │
│    must_go 解析 + 日程骨架分配             │                    │ 垂直=provider │
│ ⑤ Optimization Agent                     │                    │ adapter      │
│    排程 + 2-opt 路径优化 + 坏天气换点      │                    │ (fake 如实)   │
│    + 四分项预算 + 版本盖章                 │                    └──────────────┘
└──────────────────────────────────────────┘
门禁层（非 Agent 层，生产资产原样保留）：
    travel_validator（六轴 + 新增 3 项 warning 检查）→ travel_repair（≤2 轮 + 防震荡）→ travel_reporter
横向能力：Memory（travel/memory/ 模块，无图节点）← Requirement / Assistant / Commerce 调用
共享底座：core/{agent_base, contracts, events, supervisor}
         services/（五专家能力 service 化）  tools/（travel.* Domain Tool）  providers/（七态契约层冻结迁入）
```

要点：**Agent 层 7 个**；validator/repair/reporter 是门禁/执行/渲染**基础设施节点**，不算 Agent、不改名不搬逻辑；图节点总数 10 → 10（五专家 5 节点收敛为 research/planning/optimization 3 节点，新增 assistant/commerce 2 节点）。

---

## 2. 旧代码映射（文件级）

### 2.1 五专家 → Agent 内部 Service（不直接暴露为 Agent）

| 现有代码 | 能力拆解 | 去向（service） | 消费 Agent |
|---|---|---|---|
| `experts/poi.py`（候选池打分上限 60） | 候选池构建/打分 | `services/candidate_pool.py` | ③ Research |
| `experts/poi.py`（骨架分配 + must_go 三态解析，`planning.py` names_match 契约） | 日程骨架 | `services/skeleton.py` | ④ Planning |
| `experts/transit.py`（最近邻排程 + 午餐占位 + `rebuild_days` 唯一重排 + 版本盖章） | 排程与重排 | `services/scheduler.py`（rebuild_days 原样保留） | ⑤ Optimization |
| `experts/weather.py`（坏天气换点，必去永不换 + 预报窗口披露） | 天气适应 Plan B | `services/adaptation.py` | ⑤ Optimization |
| `experts/budget.py`（四分项只算不判） | 预算估算 | `services/estimator.py` | ⑤ Optimization |
| `experts/risk.py`（RAG 原文摘录 + 来源警告 + 免责 + knowledge_refs） | 知识/风险数据准备 | `services/knowledge_notes.py` | ③ Research |
| `experts/base.py`（`TravelExpertResult` + `run_expert_safely`） | 统一执行框架 | 升格进 `core/agent_base.py`（加超时/降级字段，异常不穿透语义保留） | 全体 |

拆分方式：git mv + 函数级搬运，**打分/排程/换点/估算算法本体一行不改**；每个 service 独立 commit 可回滚。

### 2.2 其余存量映射

| 现有代码 | 去向 |
|---|---|
| `slot_filler.py`（844 行） | git mv → `planning/requirement_agent.py`（② Requirement Agent，规则层=第一层兜底） |
| `supervisor.py`（260 行 decide() 15 规则） | git mv → `core/supervisor.py`（① 域主 Agent；stage 机保留，新增意图层与 deadline 检查） |
| `recommend.py` + slot_filler 城市归一 | → `services/destination.py`，由 ③ Research 承载（追问推荐榜与 REST `/travel/recommend` 两个消费点不变） |
| `validator.py`（655 行） | **原位保留**（不迁不改名），仅加 3 项 warning 检查（§3 门禁层） |
| `repair.py` | **原位保留**，零改动 |
| `reporter.py` | 原位保留，加 weather_status/quality 结论字段渲染 |
| `graph_state.py` + `models/` | 保留，新增字段（trip_id/confirmed_items/history/booking_intent，flag 控制） |
| `commerce/`（18 文件）+ `booking/`（18 文件） | 内部服务整体保留（fail-closed 归一门 / 八态状态机 / 幂等账本冻结）；对外入口收敛为 ⑥ Commerce Agent |
| `providers/travel/`（约 3391 行） | Phase 4 git mv → `travel/providers/`（七态契约层冻结平移）；Hotel/Flight/Ticket = provider adapter 位 |
| `tools/travel/`（7 文件） | 保留 4 个冻结 Tool + 新增见 §4 |
| `tools/travel/preferences.py` | 迁 `travel/memory/`（横向能力，含负偏好） |
| `travel_prefilter.py`（orchestration 层） | **不替换**（守护测试钉住），词表补丁 + router LLM 层 travel 召回评测 |
| `booking/recovery.py` / `reconciliation.py` / `webhook.py` | 保留为 ⑥ Commerce 内部服务；Phase 7 补 admin API 生产断头 |

---

## 3. Agent 职责边界（7 个）

> 统一契约（`core/agent_base.py`）：name / 输入输出契约 / `timeout_s` / 降级策略声明 / 遥测五要素。子 Agent 互相禁止调用，数据传递只走共享 state；`Supervisor → Agent → Tool/Service → Provider` 单向。

| # | Agent | 职责 | 输入 → 输出 | 工具/服务权限 | LLM 策略 | 超时/降级 |
|---|---|---|---|---|---|---|
| ① | **Travel Supervisor**（域主） | 意图判定（PLAN/MODIFY/QUERY/BOOK/CANCEL）、stage 推进、Agent 调度、护栏执行、deadline 检查 | 本轮输入 + state → Command(goto) | state 读写 + agent.dispatch（LangGraph 机制，不设真 Tool） | **无**（规则化意图，确定性优先） | 受整图 30s 约束；超限强制 REPORT（输出已有规划+披露）= safe_report |
| ② | **Requirement** | 自然语言 → TripBrief：13 槽 + **adults/children 拆分** + 节奏 + 偏好（含负偏好 avoid）；缺失追问（clarify 内置）；指纹/planning_reset；偏好预填与保存（调 Memory） | user_message + existing_brief + memory 预填 → TravelBrief + clarify 问题 | `travel.memory.search/save`；services/destination（城市归一） | **两层**：规则层全量保留（第一层兜底）+ LLM 结构化抽取（`TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 off；仅 required 槽缺失/低置信触发；destination 限白名单防幻觉；LLM 调用预算 4s，**零重试**） | 10s；LLM 失败→回落规则+追问 |
| ③ | **Research** | 城市归一/推荐榜；候选池构建（打分上限 60）；天气获取（三态 available/stale/unavailable）；知识/风险摘录（RAG 原文）；活动/公告=契约位（无源如实披露） | brief → candidates + weather_status + knowledge_refs + risk_notes | `travel.search_poi`、`travel.weather.query`、`travel.retrieve_knowledge`、`travel.poi.detail`；services/{candidate_pool, destination, knowledge_notes} | **无**（检索与打分全确定性） | **15s**（多次 provider 调用，内部经 live/ 线程池并行扇出）；候选池空→如实说明；天气失败→unavailable 继续规划；知识失败→只留免责 |
| ④ | **Planning** | must_go 三态解析；日程骨架分配（哪天放什么）；负重偏好约束落到骨架 | brief + candidates → day_plan 骨架 | services/skeleton；state 读 | **无** | 10s；失败→failed 不穿透 |
| ⑤ | **Optimization** | 地理排序（最近邻 + **2-opt 轻量改进**）；时刻排程（`rebuild_days` 唯一重排）；午餐占位；坏天气换点（Plan B，必去永不换）；四分项预算估算；版本盖章 stamp_version | 骨架 + weather_status → Itinerary（定稿） | `route.optimizer`、`travel.calculate_route`、`travel.calculate_budget`；services/{scheduler, adaptation, estimator} | **无** | **15s**（排程+换点内部迭代）；失败→failed 不穿透，supervisor 兜底输出骨架级成果+披露 |
| ⑥ | **Commerce** | 交易唯一入口：比价/深链（现 commerce 流）+ 下单/取消/确认（现 booking 流）；**Hotel/Flight/Ticket = kind 参数 + provider adapter**，不拆三个 Agent | booking_intent / user_message → quote/deeplink/订单终态 | services = 现 commerce+booking 全部内部模块（状态机/幂等/重验价冻结）；provider adapter 位 hotel/flight/ticket（fake 如实） | **无** | 交易操作走现有确认门/幂等账本；**fail-closed**：UNKNOWN→IN_DOUBT 绝不猜成败 |
| ⑦ | **Travel Assistant** | 行程问答与查询（QUERY 意图）："明天下雨怎么办""鼓浪屿门票多少""我第 2 天干嘛"；调 Memory 召回偏好 | user_message + state → 事实型回答 | state 读、`travel.weather.query`、`travel.search_poi`/`travel.poi.detail`、`travel.retrieve_knowledge`、`travel.memory.search` | 模板组装优先（确定性）；LLM 润色 flag 默认 off；**只准引用 state/RAG 事实，禁止编造**（无数据如实说"没有这个信息"） | 8s；失败→模板直答 |

**Memory（横向能力，非 Agent，无图节点）**：`travel/memory/` 模块 = preferences（长期偏好 + 负偏好 + 扩展类别：二次元/动漫/汉服/露营/音乐节）+ checkpoint 执行态 + ConversationContext 摘要。消费方：② Requirement（预填/保存）、⑦ Assistant（召回）、⑥ Commerce（lodging 偏好可选）。软失败纪律不变：读写失败绝不挡主流程。v2 的 Preference Agent 职责内置于 ②（用户已拍板折叠）。

**门禁层（非 Agent，保留资产）**：`travel_validator` 六轴 + 新增 3 项 warning（`POI_UNVERIFIED` / `SOURCE_STALE`——CandidatePOI 携带 source/verification_status/updated_at，validator 仍零 LLM 零 IO / `PREFERENCE_VIOLATION`——avoid 命中）；三态结论 PASS/WARNING/REJECT。`travel_repair` 零改动。

---

## 4. Tool 清单（travel.* Domain Tool，`travel/tools/`）

> 规则不变：内部确定性能力 = Domain Tool（不注册主图 capability，`travel.poi_search` 唯一注册项冻结）；第三方 = Provider（七态唯一出口）；跨系统共享才考虑 MCP（当前无）。统一信封：`core/contracts.py::TravelContext`（request_id/tenant_id/user_id/trace_id）+ status 引用 Provider 七态（**不新造枚举**）。

| Tool | 状态 | 说明 |
|---|---|---|
| `travel.search_poi` | 已有冻结 | `tools/travel/poi.py` 平移，签名不动 |
| `travel.poi.detail` | 新增（Phase 4） | 腾讯 Place 补全（must_go 点位），unverified 标注透传 |
| `travel.weather.query` | **缺位补位**（Phase 3） | 薄封装 Provider forecast + 三态归一；③⑦ 与 REST 共用 |
| `travel.calculate_route` | 已有冻结 | estimate_leg/route_km 纯函数，签名不动 |
| `route.optimizer` | 新增（Phase 3） | 接口：pois+constraints → optimized_route/distance/travel_time；实现=最近邻+2-opt（**不引 OR-Tools**，接口可换实现） |
| `travel.calculate_budget` | 已有冻结 | cost.py 平移 |
| `travel.retrieve_knowledge` | 已有，补 4s 超时（Phase 3） | RAG 封装 |
| `travel.memory.search` / `travel.memory.save` | 新增命名（Phase 2/3） | `travel/memory/` 模块的对外入口 |
| `calendar.export`（ICS） | 已有（D1 修复后） | Phase 0.5 先修 RFC 6266 |
| `map.link`（Map 导出） | 新增（Phase 6） | deeplink 复用 commerce 白名单机制 |
| `travel.currency.exchange` / `travel.train.search` / `travel.ticket.*` / event / notice | **契约位 only** | 无真实源，implemented=False 如实披露；BLOCKED_BY_EXTERNAL_PROVIDER 登记，禁止假装实现 |

PDF 导出：暂缓（需渲染依赖评审，Map/ICS 已覆盖分享场景）。

---

## 5. LangGraph 节点设计

### 5.1 节点表

| 节点名 | 类型 | 包裹/来源 | checkpointer |
|---|---|---|---|
| `travel_requirement` | Agent ② | slot_filler 平移 | ✓（随规划子图） |
| `travel_supervisor` | Agent ① | supervisor 平移 + 意图层 + deadline | ✓ |
| `travel_research` | Agent ③ | 新增（包 services/candidate_pool + destination + knowledge_notes） | ✓ |
| `travel_planning` | Agent ④ | 新增（包 services/skeleton） | ✓ |
| `travel_optimization` | Agent ⑤ | 新增（包 services/{scheduler, adaptation, estimator}） | ✓ |
| `travel_validator` | 门禁 | **原样**（+3 warning） | ✓ |
| `travel_repair` | 门禁 | **原样** | ✓ |
| `travel_reporter` | 渲染 | 原样（+字段渲染） | ✓ |
| `travel_assistant` | Agent ⑦ | 新增（QUERY 入口） | ✓ |
| `travel_commerce` | Agent ⑥ | 现 commerce 子图入口节点收敛 + booking 意图挂接 | 交易域维持现状（订单事实在 PG） |

### 5.2 拓扑

```
START → travel_requirement → travel_supervisor
supervisor ─Command(goto)─→ travel_research ──┐
                          → travel_planning ─┤  各节点静态边回 supervisor，
                          → travel_optimization ┘  decide() 依 stage 推进
                          → travel_validator →(REJECT)→ travel_repair → 回 supervisor
                          → travel_reporter → END
QUERY   → travel_assistant → END
BOOK    → travel_commerce（内部接现有 commerce/booking 流程与幂等门）→ END
CANCEL  → CANCEL_TRAVEL_RUN 原子 mutation（现逻辑平移）→ END
```

### 5.3 supervisor stage 机重映射（唯一的行为面改动点）

| 旧 stage（decide() 15 规则口径） | 新 stage | 说明 |
|---|---|---|
| poi（候选+骨架） | research → planning | 一拆二，数据流不变 |
| transit（排程） | optimization | service 化，rebuild_days 不动 |
| weather（换点） | optimization（内部 Plan B） | 触发条件不变（坏天气 + weather_status） |
| budget / risk | optimization（估算）/ research（摘录前移） | **budget 估算时机不变**（仍在排程后，通勤项数据源一致，保 `budget_silent_over` 金标） |
| validate / repair / report | validate / repair / report | 不变 |

- 护栏语义全部保留：`TRAVEL_MAX_STEPS=14`、修复≤2 轮、`repair_stalled`、专家失败无产物（Agent 失败 status=failed 不穿透）。
- 遥测兼容：`expert_history` → `agent_history` 新字段，旧字段保留一个版本（双写）供 Grafana/评测 runner 平滑切换。
- checkpointer 纪律不变：thread = `travel:{tenant}:{user}:{conv}`；`new_travel_graph_input()` 只放本轮输入不预置产物；读一律 `.get()`；state 键 dict 进 dict 出；`brief_fingerprint` 槽位白名单扩展（adults/children/avoid）随 flag。
- 入口不动：prefilter → 域图；续跑三层（TravelPendingResolver → ContinuationResolver → checkpoint）不变；Phase 5 起 BOOK 意图收编（flag `TRAVEL_UNIFIED_INTENT`）+ `booking_intent` 挂起槽修 D2。

---

## 6. Migration 步骤（Phase 0.5 + 1-8 执行卡）

> 每卡默认纪律：不动主图 builder.py；G1-G4；git mv + 旧路径 shim；每阶段独立 commit 且 pathspec 双重限定（`git add -A -- <paths>` + `git commit -- <paths>`）；局部测试 `--no-cov`；回归门 = `cd backend && python -m pytest tests/travel/ tests/orchestration/ -q --no-cov` + 四个一致性测试 + 金标 `tests/travel/test_quality_golden.py`（阈值不放松）。

| Phase | 改动（文件级） | 验收 | 回滚 |
|---|---|---|---|
| **0.5 可先行** | ① `app/api/routes/travel.py:201-206` ICS Content-Disposition 加 RFC 6266 `filename*=UTF-8''`（修 D1）+ 回归测试；② `.env` 还原 `TRAVEL_USE_LIVE_MAP=true`（备份在 d:/tmp/env.backup-20260928-travel-eval） | ICS 中文目的地 200；provider 探针过 | 各自独立 revert |
| **1 建边界**（纯新增零行为） | 新增 `core/`（agent_base 契约=expert base 升格：timeout_s/degradation/遥测；contracts：TravelContext + CandidatePOI(source/verification_status/updated_at) + TripBrief v2 字段 adults/children/avoid_categories **只定义 flag 关**；events 统一出口；supervisor action 枚举 + 现有入口→action 等价映射表）；新增 `services/ planning/ memory/ evaluation/` 骨架；域边界守护测试；四层规范按 7-Agent 口径更新（G4 登记） | 全量 travel 测试绿；无行为 diff（e2e 回放） | revert 单 commit |
| **2 Requirement Agent** | git mv `slot_filler.py` → `planning/requirement_agent.py`（shim）；D3/D4/D5 词表补丁；adults/children 拆分（party_size 派生兼容）；负偏好解析（"不购物/不爬山"）；`PREFERENCE_KEYWORDS` 扩类别；LLM 二层框架（flag off，模型按角色走 DB governance）；`memory/` 模块建立（preferences.py 迁入 + avoid 持久化）；slot 评测集 `evaluation/datasets/travel/slot/cases.jsonl` ≥50 例 + runner（槽位 ≥95% / 人数 ≥99% / 否定 ≥98%，关/开双跑）；router 评测补 travel 口语（R2） | slot_filler 49 用例改 import 后绿；slot 规则层基线落盘；金标不回退 | revert；shim 保旧 import |
| **3 五专家 service 化 + 三 Agent 包裹**（最大卡，拆 5 commit） | ① git mv experts → `services/{candidate_pool, skeleton, scheduler, adaptation, estimator, knowledge_notes, destination}.py`（算法本体零改动，base 升格 agent_base）；② 新增 3 个 Agent 节点（research/planning/optimization）接线 graph_builder；③ supervisor decide() stage 重映射（§5.3 表）+ `agent_history` 双写；④ validator 加 3 项 warning + 三态结论字段；⑤ Tool 补位（`travel.weather.query`、`route.optimizer` 2-opt 实现）+ knowledge 4s 超时 + deadline 30s 启用 + agent 超时（research/optimization 15s、其余 10s）+ reporter 字段渲染；城市归一/recommend 迁 Research（两个消费点同步改） | 每步回归门绿；金标不回退；超时注入测试（fake 慢 provider 验证降级不挂死）；D6 修复验证；遥测双写对账 | 逐 commit revert |
| **4 Provider 归位 + 数据契约位** | git mv `backend/providers/travel/` → `travel/providers/`（shim；先确认命名空间无占用）；live/ 更名 runtime/ 冻结平移；TTL：POI 24h / route 30min / weather 10min；weather 超时 6s→5s；`travel.poi.detail` Tool；`providers/events/`、`providers/notice/` 契约位（implemented=False 如实）；城市级 POI 候选源 = BLOCKED_BY_EXTERNAL_PROVIDER 登记（采购事项） | provider_layer 30 用例 + 探针 8/8；真实腾讯探针 phase A 过；联网金标不回退 | revert + .env 回退 |
| **5 Commerce 统一 + D2 修复** | commerce 子图入口收敛 `commerce/commerce_agent.py`（kind=hotel/flight/ticket 参数 + provider adapter 面，内部模块冻结）；BOOK 意图收编 supervisor（flag `TRAVEL_UNIFIED_INTENT`）；`TravelSessionState.booking_intent` 挂起槽（修 D2 两跳断）；ticket 在 facts 契约实现前不开放交易面 | commerce 79 用例绿；H/F 评测不回退；两跳对话实验通过 | flag off + revert |
| **6 Travel Assistant Agent + 报告面** | 新增 `planning/assistant_agent.py`（QUERY 接线：state 事实 + 模板直答，LLM flag off）；`map.link` 导出（deeplink 白名单复用）；ICS 对齐 `calendar.export` 命名 | QUERY 用例集（天气/门票/行程问答）测试绿；规划链零回退 | revert 单 commit |
| **7 Payment/Recovery 收口** | payment 职责收口（confirm/reconcile/webhook 归 Commerce 内部显式化）；**补 admin API 生产断头**（webhook 接收 + IN_DOUBT 对账/人工裁决端点，管理端鉴权 + `ensure_approved` 审批门，独立 commit）；PRICE_CHANGED 钩子接消费方（真实监控 BLOCKED 待供应商） | booking 39 用例绿；B1-B20 生产门 T1-T12=0；webhook HMAC 实测；审批门生效验证 | flag off + revert（API 端点独立 revert） |
| **8 E2E 收口** | 删 shim；死代码清理（reschedule_after_repair / quote['nights'] / Phase7 预留契约 / 重复实现收敛）；全量回归（分块，守多会话纪律）+ `e2e_demo.py` + 联网金标 + 实机 SSE 20 轮走查（D1-D6 回归）；AGENTS.md / 四层规范 / README 口径更新；.env 检查单核验 | 全部门禁绿 + 检查单全过 | 不适用 |

数据库：**新表 0 张**（checkpoint + travel_preferences JSONB 扩展复用）；flag 灰度三件套：`TRAVEL_REQUIREMENT_LLM_ENABLED` / `TRAVEL_SESSION_STATE_V2` / `TRAVEL_UNIFIED_INTENT`。

---

## 7. 风险评估

| # | 风险 | 级 | 缓解 |
|---|---|---|---|
| R1 | 五专家 5 节点收敛为 3 节点，supervisor stage 机与 expert_history 遥测形态变化，评测 runner/Grafana 可能断 | 高 | §5.3 重映射表逐条对照；`agent_history` 与 `expert_history` **双写一个版本**；四套评测 runner 全跑对账后才切读 |
| R2 | Research/Optimization 单节点内聚多步 provider 调用，10s 超时可能不够 | 中 | 两节点预算 15s + 内部经 live/ 线程池并行扇出；超时注入测试钉住降级路径（degraded 继续规划，不挂死） |
| R3 | budget 估算若被移动时机会改通勤项数据源，触发 `budget_silent_over` 金标回退 | 中 | **明确不移动**：估算仍在排程后（Optimization 内部最后一步），数据流与现状逐字节一致 |
| R4 | Assistant 幻觉（编造门票价格/天气） | 中 | 只准引用 state/RAG 事实 + 来源标注；LLM 润色 flag 默认 off；无数据如实说没有 |
| R5 | LLM 抽槽把"猜"带进 P0 槽位（日期/人数猜错=行程作废） | 中 | 规则层永远兜底；LLM 仅补规则缺失槽且过数值范围/白名单校验；slot 评测集 95/99/98 硬指标关/开双跑；零重试快速回落 |
| R6 | TripBrief 指纹口径扩展使存量会话首轮触发一次性重排 | 低 | 随 flag 分批；变更仅一次；评测确认无反复抖动 |
| R7 | booking_intent 跨子图状态增大 checkpoint 体积/序列化风险 | 低 | intent 只存 {kind, slots, quote_ref} 小对象；可序列化纪律测试覆盖 |
| R8 | 数据面（城市级 POI/event/notice/真实酒店机票）被进度压力用假数据填充 | 高 | 契约位 + implemented=False + BLOCKED 台账；评测 runner 断言"无源能力不得返回看似真实的数据"；这是采购事项，重构不背 |
| R9 | 多会话并行改同一批文件冲突 | 中 | 每阶段 pathspec 双重限定提交；开工前查 git status；全量测试期间不改 backend/ 不动容器 |

---

## 8. 用户禁止事项对照

| 禁止 | 落实 |
|---|---|
| 删除 validator/repair/provider/booking 核心代码 | validator 原位原零改（只加 3 项 warning）；repair 零改动；provider 七态层冻结平移；booking 状态机/幂等/重验价零改动——全部只允许加法式扩展 |
| 大规模推倒重写 | 全部 git mv + shim + 函数级搬运；图节点数 10→10；每阶段可独立 revert；旧行为经 flag 保留到 Phase 8 |
