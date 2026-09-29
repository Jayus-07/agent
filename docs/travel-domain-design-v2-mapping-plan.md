# 旅游域 Agent 化设计方案 v2 —— 现有代码映射表 × 差距分析 × 文件级改造计划

> **状态更新（2026-09-29）**：用户已对本文 §6 裁量点拍板（折叠确认 / 不引 OR-Tools / kind 参数化确认），并将架构收敛为 **7-Agent 版**——目标架构、Agent 职责、LangGraph 节点与迁移卡以 `docs/travel-domain-agent-runtime-design.md`（v3）为准；本文保留作 v2→v3 的差异裁决依据与差距分析底稿（其 §1 裁决表、§3 差距分析继续有效）。
>
> 输入：用户 2026-09-29《旅游规划助手 Domain Runtime 重构设计方案》（下称 **v2 方案**，三层 19 组件版）。
> 基线：commit `03a8eb3`。前置文档：`docs/travel-domain-current-audit.md`（审计，文件/行号以它为准）、`docs/travel-domain-production-design.md`（v1 设计，Planning 9 + Transaction 3）、`docs/travel-domain-refactor-full.md`（合并评审稿）。
> 本文只做三件事：**映射表 / 差距分析 / 文件级改造计划**，不含任何代码改动。所有「文件:行号」引用以审计文档核对过的真实代码为准。
> 裁决原则（沿 v1，本文一切取舍标准）：**稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。**

---

## 0. 结论先行

1. **v2 方案约 70% 与 v1 设计重合**（Supervisor 意图层、Requirement 两层、五专家 Agent 化、quality_gate、Provider 七态、checkpoint 复用、Phase 化迁移），这部分直接沿用 v1 执行卡。
2. **v2 真正的新增量只有 6 项**：Preference/Clarification/Memory 三个 Conversation 组件、Planner/Optimizer 拆分、OR-Tools 路径优化、统一 Tool 信封契约、Report 多格式（PDF/Map）、Price Monitor。本文逐项给出映射与采纳级别。
3. **v2 有 4 处与仓库铁律/冻结资产冲突**，全部有解法但不照字面执行：validator 来源检查（零 IO 红线）、Tool 更名（契约冻结）、替换 travel_prefilter（主图守护测试钉住）、OR-Tools（依赖引入流程）。见 §1 裁决表 R1-R4。
4. 最大风险不在架构在**数据面**：v2 的 Destination Research / event.search / 票价事实 / 城市级泛化，全部卡在「3 城 31 条种子 + 无城市级 live POI 源」，这是采购/供应商事项，不是重构能解决的——计划里显式标 BLOCKED，不许用假装数据填充。

---

## 1. v2 → v1 总裁决表（差异项）

> 「重合项」不列，直接按 v1 执行卡走。仅列 v2 相对 v1 的**增量/冲突**项。

| # | v2 方案条目 | 裁决 | 理由与落点 |
|---|---|---|---|
| A1 | Clarification Agent 独立成节点 | **折叠采纳** | 追问逻辑（`SLOT_QUESTIONS`，`models/brief.py:52-59`）与槽位状态强耦合（缺什么问什么是抽取的函数，不是独立意图）。拆成独立图节点 = 每轮多一跳 + 追问状态机与 requirement 状态机跨节点同步，纯增风险。落地为 `requirement_agent` 内的 clarify 阶段（对外语义不变），Phase 2。若用户坚持独立节点，追加为 `planning/clarification_agent.py` 薄节点（+1 图节点，成本见裁量点 §6-A） |
| A2 | Preference Agent 独立成节点 | **折叠采纳** | v2 给的例子（"小众"→crowd:low/style:deep）本质是槽位抽取的第二段输出，与 requirement LLM 二层是**同一次 LLM 调用**。独立节点 = 同一上下文调两次模型。落地为 requirement LLM 二层的 `PreferenceProfile` 输出段 + `travel/memory/` 负偏好持久化，Phase 2/3。同理可拆但默认不拆（§6-B） |
| A3 | Memory Agent 独立成节点 | **折叠采纳** | 偏好读写已是软失败内联函数（`tools/travel/preferences.py`，162 行，绝不挡规划）。做成图节点反而把「软失败」变成「节点失败」需引入降级分支。落地为 `travel/memory/` 模块 + requirement/destination 两处消费点，Phase 3 迁入（与 v1 一致）。不做图节点（§6-C） |
| A4 | Itinerary Planner 与 Route Optimization 拆两个 Agent | **合并采纳（单 Agent 两阶段）** | 现 `experts/transit.py` 的 `rebuild_days` = 排班装配与地理排序本就是同一函数链（最近邻 → 骨架分配 → 时刻排程），拆开要在 state 里新增中间产物（候选序列 vs 定稿排程）并加一跳校验。先在 route_agent 内部保持 `assemble → optimize` 两阶段结构（便于未来拆），不拆节点（§6-D）。OR-Tools 见 R4 |
| A5 | Tool 统一信封（request_id/tenant_id/user_id/trace_id + status 枚举 + source/updated_at） | **改造采纳** | status 枚举与 Provider 七态对齐：v2 六态 ⊂ 现有七态（SUCCESS/NOT_FOUND/TIMEOUT/RATE_LIMITED/UNAVAILABLE/INVALID_RESPONSE/UNAUTHORIZED）+ DISABLED，**不新造枚举**，工具层信封引用 `ProviderStatus`。trace 上下文走 `core/contracts.py` 的 TravelContext 对象（request_id/tenant_id/user_id/trace_id 一次构造全链透传）。source/updated_at 进 CandidatePOI 契约字段。Phase 1 定契约、Phase 3 起逐工具接入 |
| A6 | Tool 层级化命名（travel.poi.search / travel.map.route / travel.hotel.book…） | **降级采纳** | **已注册 capability `travel.poi_search` 冻结**（`orchestration/router/capabilities.yaml:857`，改 = 破坏性契约变更）。新 Tool 一律按 v2 层级命名（travel.get_weather 缺位补位时直接叫 `travel.weather.query` 等）；存量 4 个 Domain Tool 名（search_poi/calculate_route/calculate_budget/retrieve_knowledge）Phase 8 前不动，Phase 8 收口时评估加别名（不删旧名） |
| A7 | Report 多格式（Markdown/PDF/ICS/Map） | **部分采纳** | Markdown=现状；ICS=现状但有 D1 P1 缺陷（中文目的地 500，`app/api/routes/travel.py:201-206` 缺 RFC 6266）——**D1 独立小修，不等待重构，先行提交**；Map=deeplink/静态图链接（commerce deeplink 白名单机制可复用），Phase 7；PDF=需引渲染依赖（reportlab/weasyprint 类），走依赖评审后再排期，默认 P2 缓办 |
| A8 | Price Monitor Agent | **降级采纳（P2）** | 真新增能力，且仓库已有它的锚点：`booking/revalidate.py:33-36` 的 PRICE_CHANGED 钩子**当前无消费方**。但依赖通知通道（notification.send）与周期任务预算，且 fake-only 供应商下监控的是假价格——真实价值为零。Phase 6 只接 PRICE_CHANGED 消费 + admin 可见；真实监控等真实供应商签约后启动（同 BLOCKED 逻辑） |
| A9 | Research 并行（POI/Food/Weather/Event 同时拉取） | **改造采纳（provider 级并行，不改图序）** | 图序保持串行（v2 自己也要求 POI→Route→Budget→Validator 串行）。并行发生在 poi/destination agent **内部**对 Provider 的调用层——`live/` 已有专用线程池 + single-flight，天然支持扇出；图节点级 Send 并行收益为负（审计 §2：数据依赖 poi→transit→weather）。Phase 3 实现 |
| A10 | Supervisor 3s 超时 + safe_report 降级 | **采纳（被更大预算覆盖）** | 现 supervisor 是纯规则 decide()（亚毫秒级），3s 天然满足；降级「返回已有规划」= v1 的整图 30s deadline 强制 REPORT 机制（输出已有 itinerary + 披露）。按 v1 的 Agent 10s / 整图 30s 两层执行，不单设 3s 配置项 |
| R1 | Validator 升级：真实性验证 / 来源检查 / 用户偏好匹配 | **约束下采纳** | **validator 零 LLM 零 IO 是红线**（旅游域 Evidence Gate）。解法：三项全部做成**对 state 携带字段的纯规则判定**——真实性 = `POI_UNVERIFIED` warning（v1 已计划）；来源检查 = CandidatePOI 契约强制携带 `source/verification_status/updated_at`（数据进 state 在前，判定在后，validator 仍零 IO），source 缺失/过期计 warning；偏好匹配 = 新增 WARNING 级 axis（pace vs 单日负载已有 `PACE_TOO_INTENSE` 承担大头，新增的是 avoid_categories 命中检查——"不购物"行程里出现购物类 POI 计 warning）。三项全部 warning 起步，不设 error，防止主观项阻塞交付。Phase 3 |
| R2 | 替换 travel_prefilter（正则→语义） | **降级采纳** | prefilter 在主图 `router_node.py:341-540` 优先级链里，受 `tests/orchestration/graph/test_router_prefilter_order.py` 守护，**机制不可换**。且语义兜底已存在：prefilter 未命中会落到三层 Router（rule→vector→LLM），travel 是 `domain_router.py` 既有标签。真实缺口是①词表（D3："再去福州玩两天"/"N天游"）②LLM 层 travel 标签的召回无评测。做法：Phase 2 补词表 + 把 travel 加入 router 评测集实测 LLM 层召回，用数据决定是否需要补规则，**不引入 prefilter 级 LLM** |
| R3 | 「重点替换 reporter」 | **修正为改造** | reporter（纯模板 Markdown 渲染 + 诚实披露）没有"替换"价值，Phase 3 平移 + weather_status/quality 结论字段渲染（v1 已含）。v2 要求的多格式输出是新 Tool 面（A7），不是换 reporter |
| R4 | Route Optimization 引入 OR-Tools/TSP/VRP | **降级采纳（P2 + 依赖评审）** | 全局规则：不引入未要求的依赖须先问。OR-Tools 是重依赖（打包体积/许可证/容器镜像）。顺序：Phase 3 先在 `route.optimizer` Tool 接口后面做**确定性 2-opt 改进**（零依赖，最近邻 + 局部反转变 improvment，金标可验）；OR-Tools 作为 optional extra 待 2-opt 不达标再立项。接口（`route.optimizer`：pois+constraints → optimized_route/distance/travel_time）Phase 3 一次定好，实现可换 |
| R5 | Requirement 用 Qwen3-8B | **采纳但改锚定方式** | 模型凭据/绑定 **DB 唯一来源**（.env 无模型行，模型治理已收口）。设计按「角色」引用（cheap-slot-extraction 档位），具体绑哪个模型走 DB model governance 注册与评测，不写死 Qwen3-8B 进代码/配置。flag `TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 off 不变 |
| R6 | TripBrief 增加 `people:{adult, child}` | **采纳** | 实测确认 `TravelBrief.party_size` 是单一整数（含本人），D4（"2个大人"排成1人）的根因之一。新增 `adults/children` 可选字段，`party_size` 保留为派生兼容字段（=adults+children，缺省回退旧解析），Phase 2 随 LLM 二层一起落。指纹口径扩字段需同步 `brief_fingerprint` 白名单与 slot 评测集 |

---

## 2. 现有代码映射表（文件级）

> 按 v2 三层逐组件映射。处置：**保留**（不动）/ **平移**（git mv + shim）/ **改造**（原地改）/ **新增**。行数为基线实测。

### 2.1 Travel Supervisor Agent（域主 Agent）

| v2 要求 | 现有代码 | 处置 → 目标 |
|---|---|---|
| 意图判断（travel intent / 已有 session 进入） | 入口三层：`TravelPendingResolver`（cancel > avoid_patch > 值型补槽 > new_run）→ `ContinuationResolver` → 域内 checkpoint（`orchestration/graph/router_node.py:341-540` + `travel_graph_node.py`） | **保留**。v2 的 trigger 语义与现有三层续跑一一对应：新会话→new_run；"酒店换便宜一点"→avoid_patch/指纹变化=MODIFY；"明天下雨怎么办"→QUERY（Phase 1 起 action 枚举显式化） |
| Agent 调度 + 状态管理 + 局部重规划 | `travel/supervisor.py`（260 行，decide() 15 条规则 + Command(goto)） | **改造** → `travel/core/supervisor.py`：stage 机原样保留，新增意图层 `action ∈ {PLAN,MODIFY,QUERY,BOOK,CANCEL}`（Phase 1 只立枚举零行为变化，Phase 5/6 收编 BOOK） |
| step 限制 / checkpoint / repair 机制保留 | `TRAVEL_MAX_STEPS=14` / 修复≤2 轮 / `repair_stalled` / PostgresSaver 三级降级 | **保留**（生产资产，审计 §9） |
| Tool 权限仅 travel.state.read/update + agent.dispatch | LangGraph Command(goto) + state 读写即等价机制 | **保留**（无需新建 Tool，写进 agent_base 契约注释即可） |
| 超时 3s / 降级 safe_report | 无超时；无 safe_report | **新增**：整图 30s deadline（supervisor 每次放行前检查，超限强制 REPORT 输出已有规划+披露）——v1 §8 已设计，A10 裁决覆盖 3s |

### 2.2 Conversation Layer

| v2 组件 | 现有代码 | 处置 → 目标 |
|---|---|---|
| **Requirement Agent**（NL→TripBrief） | `travel/slot_filler.py`（844 行，纯规则：13 槽 + 指纹 + planning_reset + 追问）；`models/brief.py`（契约 + 词表）；偏好预填已接 | **改造+拆分** → `travel/planning/requirement_agent.py`（规则层全量保留为第一层）+ LLM Structured Output 第二层（flag off，仅 required 槽缺失/低置信触发，destination 限白名单防幻觉，零重试失败回落规则+追问）。规则层词表同步补：D3/D4/D5 + 「两个人/带孩子/二次元/不购物/不爬山」+ **adult/child 拆分（R6）** |
| TripBrief schema（origin/destination/days/date/people/budget/pace/interest/constraints） | `TravelBrief`：destination/origin/start_date/days/party_size/budget_cny/pace/preferences/diet/lodging… | **改造**：新增 adults/children（R6）、avoid_categories（负偏好）、crowd_preference（A2）；`brief_fingerprint` 槽位白名单同步扩展（指纹口径变更必须有测试钉住） |
| 评测（槽位 95% / 人数 99% / 否定 98%） | 无 slot 评测集（金标 34 条是行程质量不是抽槽） | **新增**：`evaluation/datasets/travel/slot/cases.jsonl` ≥50 例（D3/D4/D5 口语 + 必过集），runner 报分槽位指标，LLM 关/开双跑口径。Phase 2 |
| **Clarification Agent** | slot_filler 追问循环（`SLOT_QUESTIONS` + REQUIRED_SLOTS=("destination","days")） | **折叠**进 requirement_agent clarify 阶段（裁决 A1） |
| **Preference Agent** | 无独立实现；散件：`PREFERENCE_KEYWORDS` 7 类、`DIET_KEYWORDS`、preferences upsert | **折叠**进 requirement LLM 二层输出 PreferenceProfile 段 + `travel/memory/` 负偏好扩展（裁决 A2）；词表扩展加「二次元/动漫/汉服/露营/音乐节」等新类别进 `PREFERENCE_KEYWORDS`（Phase 2，随评测集） |
| **Memory Agent** | `tools/travel/preferences.py`（162 行，travel_preferences 表运行时建表）+ checkpoint 执行态 + ConversationContext 摘要 | **平移改造** → `travel/memory/`（preferences.py 迁入 + avoid 负偏好 + interest 扩类别）。不做图节点（裁决 A3）。v2 的 travel_preferences/current_trip 两级 = 现有「偏好表 + checkpoint」已满足，缺的只是字段 |

### 2.3 Planning Layer

| v2 组件 | 现有代码 | 处置 → 目标 |
|---|---|---|
| **Destination Research Agent**（景点/美食/活动/节日/攻略 + event.search + knowledge.search + Redis 缓存 city+keyword TTL 30min） | `travel/recommend.py`（推荐榜，已接线追问+REST）+ `tools/travel/poi.py`（173 行纯函数检索）+ `tools/travel/knowledge.py`（54 行 RAG，无超时）+ `providers/travel/live/tencent.py` Place | **改造组合** → `travel/planning/destination_agent.py`（城市归一/不支持城市明示/推荐榜，v1 已拆）+ **research 扩展**：knowledge.search 补 4s 超时；event.search = 新 Provider 契约位（`travel/providers/events/`，**无真实源前 implemented=False 如实披露**，RAG 节事信息兜底）；缓存复用 `live/cache.py` 分数据 TTL（POI 24h/检索 30min 落配置）。**数据面 BLOCKED 见 §3** |
| **POI Discovery Agent**（候选池禁幻觉） | `travel/experts/poi.py`（候选池打分上限 60 + 骨架分配 + must_go 三态）+ `tools/travel/poi_seed.py`（3 城 31 条） | **平移改造** → `planning/poi_agent.py`：逻辑平移，CandidatePOI 契约强制携带 `source/verification_status/updated_at`（R1 数据前提）；**POI 只能来自 Provider/种子/RAG，禁止 LLM 幻想** 铁律保留 |
| **Itinerary Planner Agent** | `experts/transit.py`（最近邻排程 + 午餐占位 + `rebuild_days` 唯一重排 + 版本盖章） | **平移改造** → `planning/route_agent.py`（transit 改名 route），内部 assemble→optimize 两阶段（裁决 A4）；`rebuild_days` 唯一重排实现不动 |
| **Route Optimization Agent**（算法非 LLM：最近邻→OR-Tools/TSP） | transit 最近邻；`tools/travel/routing.py`（185 行 estimate_leg + route_km 纯函数） | **改造** → `route.optimizer` Domain Tool 接口（Phase 3 定签名），实现先 2-opt 确定性改进，OR-Tools optional extra 待评审（裁决 R4）。路线数据面：腾讯 transit live 通道 + 本地估算兜底已有 |
| **Weather Adaptation Agent**（雨天 Plan B） | `experts/weather.py`（坏天气换点，必去永不换 + 预报窗口披露）+ 腾讯主/和风备 `FallbackWeatherProvider` | **平移改造** → `planning/weather_agent.py` + 三态输出 available/stale/unavailable（映射七态×Freshness，失败不阻塞，reporter 按态渲染，顺带修 D6）。`travel.weather.query` Tool 薄封装补位（现状缺位，expert 直调 Provider） |
| **Budget Agent**（预测/降预算/超预算优化） | `experts/budget.py`（四分项只算不判）+ `tools/travel/cost.py`（76 行 CNY 城市档位） | **平移** → `planning/budget_agent.py`。「降预算方案/超预算优化」= 排程约束反馈（budget 超 → route_agent 降档重排），Phase 3 起经 quality_gate budget 轴已有闭环；`currency.exchange`/`price.search` Tool = P2（CNY 单币种现状下无消费方，登记不实现） |
| **Risk Agent**（签证/安全/天气/健康 + risk.kb.search/notice.search） | `experts/risk.py`（RAG 原文摘录刻意不用 ask() + 来源警告 + 免责 + knowledge_refs） | **平移** → `planning/risk_agent.py`。`risk.kb.search` = 现有 `retrieve_travel_knowledge` 改名注册；notice.search（官方公告源）= 新 Provider 契约位 P2（同 event 无源不假装） |
| **Report Agent**（多格式） | `travel/reporter.py`（Markdown 模板）+ `app/api/routes/travel.py`（plan/ics/feedback/preferences/recommend） | **改造** → `planning/reporter_agent.py` + weather_status/quality 渲染；ICS 修 D1（**先行独立提交**）；Map=deeplink Phase 7；PDF=P2 依赖评审（裁决 A7）。`calendar.export`/`document.generate` Tool 名预留，ICS 即 calendar.export 的第一实现 |
| （v2 未列，v1 保留）Quality Gate + Repair | `travel/validator.py`（655 行六轴）+ `travel/repair.py`（kept_required/防震荡） | **平移改造** → `planning/quality_gate.py`（三态 PASS/WARNING/REJECT + R1 三项新检查）+ `planning/repair.py`（原样，生产资产）。validator 零 LLM 零 IO 红线不动 |

### 2.4 Transaction Layer

| v2 组件 | 现有代码 | 处置 → 目标 |
|---|---|---|
| **Hotel / Flight / Ticket Agent**（生命周期 search→quote→confirm→book→cancel） | `travel/commerce/`（18 文件：比价/归一/排序/deeplink，fake 源）+ `providers/travel/live/fake_commerce.py` + ticket 仅 `facts.py` implemented=False | **参数化采纳（不按垂直拆节点）**：commerce 子图整体平移为 `commerce/commerce_agent.py`，垂直维度（hotel/flight/ticket）= 意图 kind 参数而非独立 Agent（同一状态机/归一门/幂等路径，拆三个节点只复制风险）；ticket 在 facts 契约实现前不开放交易面。真实供应商 = BLOCKED_BY_EXTERNAL_PROVIDER 照实登记（裁决 §6-F） |
| **Booking Agent**（保留核心 + confirmation conversation 修 D2） | `travel/booking/`（18 文件：八态状态机/幂等账本/三模型契约/revalidate）+ celery beat `travel_booking_recovery_scan` | **平移**（状态机/账本/重验价零改动）→ `booking/booking_agent.py`；**D2 修复** = `TravelSessionState.booking_intent` 挂起槽（Phase 5）+ booking 域内 pending 续跑，两跳对话不再依赖重新正则命中 |
| **Price Monitor Agent** | PRICE_CHANGED 钩子无消费方（`booking/revalidate.py:33-36`） | **P2**（裁决 A8）：Phase 6 只接钩子消费 + admin 可见；通知通道与真实监控等真实供应商 |
| **Recovery Agent**（booking IN_DOUBT / payment timeout / provider error） | `booking/recovery.py` + `booking/reconciliation.py`（reconcile_order/manual_resolve）+ `booking/webhook.py`（ingest_webhook）+ beat 扫描 | **改造收口** → payment_agent 职责内（v1 命名重组）：**补生产断头**——ingest_webhook/reconcile_order/manual_resolve 三者当前无任何 HTTP/admin API 入口，Phase 6 补 admin 端点（管理端鉴权 + `security/tool_approval.ensure_approved()` 写操作审批门） |

### 2.5 Tool Registry（travel.\* 统一命名）与契约

| v2 Tool | 现状 | 处置 |
|---|---|---|
| travel.poi.search / travel.poi.detail | `tools/travel/poi.py`（search）+ tencent Place（detail 补全 unverified） | 存量 `travel.poi_search` capability 冻结；Domain Tool 内部名平移，detail 走 Provider 契约 |
| travel.map.route / travel.map.distance | `tools/travel/routing.py` + `live_map.py`（366 行） | 平移（estimate_leg/route_km 签名冻结，route_km 保持纯函数） |
| travel.weather.query | **缺位**（expert 直调 Provider） | **新增薄封装**（Phase 3/4），weather_agent 与 REST 共用 |
| travel.currency.exchange | 无 | P2 登记（无消费方不实现） |
| travel.flight.search / travel.train.search | fake flight 源 / train 无 | flight 走 commerce kind=flight（fake 如实）；**train 全新外部依赖 = P2 登记不做** |
| travel.hotel.search/quote/book | commerce（search/rank/deeplink）+ booking（quote/order） | 平移，经 commerce_agent/booking_agent，不直接暴露 Tool 给主图 |
| travel.ticket.search/book | facts implemented=False | **不做**（数据面 BLOCKED，开放即造假） |
| travel.memory.search / travel.memory.save | preferences.py upsert/read | 迁 `travel/memory/` 后对齐命名 |
| 统一信封（A5） | Provider 七态 + Freshness 已有；Tool 层无统一信封 | `core/contracts.py` TravelContext（request_id/tenant_id/user_id/trace_id）+ status 引用 ProviderStatus；status 枚举**不新造**（v2 六态 ⊂ 七态+DISABLED，审计已论证） |

### 2.6 并发策略 / Memory / 最终图

| v2 要求 | 现状 | 处置 |
|---|---|---|
| Research 可并行（POI/Food/Weather/Event） | 五专家串行（graph_builder 静态边） | provider 调用层并行（live/ 线程池 + single-flight），图序不变（裁决 A9） |
| POI→Route→Budget→Validator 串行 | poi→transit→weather→budget/risk→validate 固定序 | 保留（一致） |
| Memory：travel_preferences + current_trip | 偏好表 + checkpoint 已是这两级 | 字段扩展即达标（avoid/crowd/adults-children） |
| 最终 LangGraph（Research Parallel 段） | 10 节点图 | 节点数不膨胀（折叠裁决后 planning 9 个子 Agent 与 v1 相同），Research 并行在节点内部。**不动 builder.py，域图经 `register.py` → `domains/__init__.py` 自注册自动布线** |

---

## 3. 差距分析（v2 视角重排）

### P0（不解决则 v2 目标不成立）

| 差距 | 证据 | 归属 |
|---|---|---|
| 理解层零 LLM，口语/新类别漏抓 | D4（slot_filler.py:113-117）、D5（brief.py PACE_KEYWORDS 无「节奏慢一点」字面）、词表仅 7 类无二次元/汉服/露营；adult/child 无拆分 | Phase 2（LLM 二层 + 词表 + R6） |
| 数据面：城市不可泛化、无真实事实 | 3 城 31 种子坐标自声明示例值；城市级 POI 候选无 live 源（providers/travel/poi.py:103-114 显式返空）；ticket.facts implemented=False；event/notice 无源 | **采购/供应商事项**，Phase 4 落契约位并 BLOCKED 登记；重构不许用假数据填充 |
| 路由口语漏检 | D3（travel_prefilter.py:27-35 词表 + 完成态无常驻语义）；LLM 层 travel 召回无评测数据 | Phase 2 词表 + router 评测；Phase 5 booking_intent 修完成态 |

### P1（v2 核心体验，Phase 3-6 覆盖）

| 差距 | 证据 | 归属 |
|---|---|---|
| Agent 无统一契约（超时/降级声明） | run_expert_safely 不限时、无降级字段 | Phase 1 agent_base + Phase 3 接入（10s/30s） |
| 预订两跳断（D2） | booking 无域内挂起；booking_prefilter 单消息正则 | Phase 5 booking_intent 挂起槽 |
| booking 生产接线断头 | webhook/reconcile/manual_resolve 无 HTTP/admin 入口，IN_DOUBT 闭环在生产不可达 | Phase 6 admin API + 审批门 |
| D1 ICS 500 / D6 天气文案失真 | travel.py:201-206；experts/risk.py 静态文案 | D1 先行独立修；D6 随 weather 三态（Phase 3） |
| supervisor 无意图层 | 只有 stage 机 | Phase 1 枚举（零行为）→ Phase 5/6 收编 BOOK |

### P2（增强项，逐项可裁）

OR-Tools（R4）、PDF 导出、currency/train/ticket tool、event/notice 真实源、Price Monitor 完整体、QUERY 意图的知识库单步问答、偏好匹配轴升级。全部「登记契约位、flag 关闭、不假装可用」。

### 结构性债（迁移顺带修，审计 §12）

`reschedule_after_repair` 死代码、`booking/reporter.py:44` quote['nights'] 幽灵引用、`_quote_expired` 两份、槽位组装三份、预订正则两份、`.env TRAVEL_USE_LIVE_MAP=false` 未还原（备份 d:/tmp/env.backup-20260928-travel-eval）。Phase 8 清理，不新增。

---

## 4. 文件级改造计划（Phase 1-8 执行卡）

> 通用纪律（每张卡默认生效，不再重复）：不动主图 builder.py；域图自注册链（register.py → domains/\_\_init\_\_.py）不断；G1-G4（capabilities.yaml 唯一事实源，新 Domain Tool 不注册主图 capability，`travel.poi_search` 唯一注册项不变）；git mv + 旧路径 shim；每卡独立 commit 且 **pathspec 双重限定**（`git add -A -- <paths>` + `git commit -- <paths>`）；测试局部跑必须 `--no-cov`；回归门 = `cd backend && python -m pytest tests/travel/ tests/orchestration/ -q --no-cov` + 四个一致性测试 + 金标 `tests/travel/test_quality_golden.py`。
>
> 目标目录树（与 v1 一致，v2 三层映射其上）：`travel/{core,planning,commerce,booking,providers,tools,memory,evaluation}/`。Conversation Layer 不设独立目录（裁决 A1-A3），由 planning/requirement_agent + memory/ 承载。

**Phase 0.5（可立即执行，不等重构）**：D1 ICS 修复（`app/api/routes/travel.py:201-206` 加 RFC 6266 `filename*=UTF-8''`）+ `.env TRAVEL_USE_LIVE_MAP=true` 还原。各独立 commit，附带回归测试各 1 条。

**Phase 1 建立边界 + 契约定型**（纯新增，零行为变化）
- 新增：`travel/core/`（`agent_base.py` BaseAgent 契约：name/input/output/timeout_s=10/degradation/telemetry 五要素；`events.py` 统一事件出口；`contracts.py` **TravelContext 信封**（A5）+ CandidatePOI 契约（source/verification_status/updated_at）+ TripBrief v2 字段定义（adults/children/avoid_categories/crowd_preference，**只定义 flag 关**））
- 新增：`travel/planning/`、`travel/memory/`、`travel/evaluation/`、`travel/tools/` 骨架（\_\_init\_\_.py）
- 新增：`core/supervisor.py` action 枚举 `{PLAN,MODIFY,QUERY,BOOK,CANCEL}` + 现有入口→action 映射表（等价归入，零行为）；supervisor deadline 检查骨架（不启用）
- 新增：域边界守护测试（锁定目录映射，对标 `test_domain_semantic_consistency.py` 风格）
- 文档：四层规范旅游域章节按「域主 Agent 调度子 Agent」口径更新（G4 台账登记 v2 折叠裁决 A1-A3）
- 验收：全量 travel 测试绿；无行为 diff（e2e 回放对比）；回滚 = revert 单 commit

**Phase 2 Requirement + Conversation 面落地**
- git mv：`travel/slot_filler.py` → `travel/planning/requirement_agent.py`（旧路径 shim）；推荐逻辑拆 `planning/destination_agent.py`（recommend.py 消费方同步改 import）
- 改造：D3/D4/D5 词表补丁（slot_filler 规则层 + brief.py）；`PREFERENCE_KEYWORDS` 扩类别（二次元/动漫/汉服/露营/音乐节/亲子已有）；**adults/children 拆分（R6）** + party_size 兼容派生；avoid_categories 负偏好解析（"不购物/不爬山"）
- 新增：LLM 二层框架（`TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 off；模型按角色锚定走 DB governance，R5）；clarify 阶段显式化（裁决 A1）
- 新增：`evaluation/datasets/travel/slot/cases.jsonl` ≥50 例 + slot 评测 runner（分槽指标：槽位 ≥95% / 人数 ≥99% / 否定 ≥98%，LLM 关/开双跑）；router 评测集补 travel 口语用例实测 LLM 层召回（R2）
- 验收：slot_filler 49 用例改 import 后绿；slot 评测规则层基线落盘；金标不回退

**Phase 3 Planning Agents 逐个平移 + Agent 契约生效**
- git mv（**每个 Agent 独立 commit**）：experts/{poi→poi_agent, transit→route_agent, weather→weather_agent, budget→budget_agent, risk→risk_agent}；validator→`planning/quality_gate.py`；repair→`planning/repair.py`；reporter→`planning/reporter_agent.py`
- 改造：BaseAgent 契约接入（超时 10s：run_expert_safely 包线程+future；weather 三态；deadline 30s 启用——supervisor 每次放行前检查，超限强制 REPORT）
- 改造：quality_gate 三态化 + 新检查项 `POI_UNVERIFIED`（warning）/ `SOURCE_STALE`（warning，R1 来源检查=state 字段判定零 IO）/ `PREFERENCE_VIOLATION`（warning，avoid 命中）
- 改造：research 并行（A9，destination/poi agent 内部 provider 扇出）；`travel.weather.query` Tool 薄封装；`route.optimizer` Tool 接口 + 2-opt 确定性实现（R4 第一阶段）
- git mv：`tools/travel/preferences.py` → `travel/memory/`（+ avoid 负偏好持久化，裁决 A3）
- 改造：TravelSessionState 新字段 trip_id/confirmed_items/history（flag `TRAVEL_SESSION_STATE_V2`；checkpoint 可序列化纪律不变，`new_travel_graph_input()` 不预置产物）
- 验收：每步测试绿；金标不回退；超时注入测试（fake 慢 provider 验证 10s/30s 降级不挂死）；D6 修复验证

**Phase 4 Provider 归位 + 数据面契约位**
- git mv：`backend/providers/travel/` → `travel/providers/`（旧命名空间 shim；先确认 `backend/providers/` 无其他域占用）；`live/` 更名 `runtime/` 冻结平移
- 配置：POI TTL 24h / route 30min / weather 10min；weather 超时 6s→5s
- 新增（契约位 only）：`providers/events/`（event.search 契约 + implemented=False 如实披露，RAG 兜底）；`providers/notice/` 同款 P2 占位；城市级 POI 候选源 = **BLOCKED_BY_EXTERNAL_PROVIDER 登记**（采购事项，不假装）
- 验收：provider_layer 30 用例 + travel-provider 探针 8/8；真实腾讯探针 phase A 过；联网金标不回退

**Phase 5 Commerce 收编 + D2 修复**
- 平移：commerce 子图 → `commerce/commerce_agent.py`（fail-closed 归一门/确定性排序/deeplink 白名单冻结；hotel/flight = kind 参数，裁决 §6-F）
- 改造：意图入口收编进 supervisor BOOK action（flag `TRAVEL_UNIFIED_INTENT`）；`TravelSessionState.booking_intent` 挂起槽 → **D2 两跳对话修复**
- 验收：commerce 79 用例绿；H/F 评测 runner 不回退；两跳对话实验通过

**Phase 6 Booking / Payment / Recovery 收口**
- 平移：booking 子图（状态机/账本/重验价**零改动**）
- 改造：payment_agent 命名重组（confirm_and_execute/reconciliation/webhook 收口）+ Recovery 职责显式化（recovery.py + beat 扫描归入）
- **补生产断头（新增 admin API，独立 commit）**：webhook 接收入口 + IN_DOUBT 对账/人工裁决端点（管理端鉴权 + `ensure_approved` 审批门）
- 改造：PRICE_CHANGED 钩子接消费方 + admin 可见（Price Monitor 最小闭环，真实监控 BLOCKED 待供应商）
- 验收：booking 39 用例绿；B1-B20 生产门 T1-T12=0；webhook HMAC 实测；审批门生效验证

**Phase 7 Report 多格式 + 增强项（可裁）**
- Map 导出（deeplink 复用 commerce 白名单机制）；`calendar.export`=ICS（Phase 0.5 已修）对齐命名；PDF 依赖评审通过才排入
- QUERY 意图启用（"鼓浪屿门票多少"→ poi/knowledge 单步，不出整案）
- 验收：不回退三项门（金标/HF/B）

**Phase 8 E2E 收口**
- 删 shim；死代码清理（§3 结构性债清单）；存量 Domain Tool 命名别名评估（A6）
- 全量回归（分块，遵守多会话并发纪律）+ `e2e_demo.py` + 联网金标（阈值不放松）+ 实机 SSE 20 轮走查（D1-D6 回归）
- 文档：AGENTS.md 旅游域段落 / 四层规范 / README 系统规模口径同步；`.env` 检查单核验

---

## 5. 明确不做（防复杂度失控，v2 增量裁决汇总）

- Clarification/Preference/Memory **不设独立图节点**（折叠，A1-A3；用户否决折叠则按 §6-A/B/C 追加，成本已写明）
- Hotel/Flight/Ticket **不拆三个 Agent**（kind 参数化，§6-F）
- Planner/Optimizer **不拆两个节点**（单 Agent 两阶段，A4）
- 不引入 prefilter 级 LLM、不给 POI/Route/Budget/Risk 加 LLM、supervisor 不做 LLM 意图分类
- 不新造 status 枚举、不改已注册 capability 名、不动主图 builder、不动 booking 冻结层
- 无真实数据源的能力（event/notice/train/ticket 交易/城市级 POI 候选）**登记 BLOCKED 不假装实现**
- 不新增数据库表（复用 checkpoint + 偏好 JSONB，新表 0 张）

## 6. 待用户拍板的裁量点

| # | 问题 | 建议 |
|---|---|---|
| A | Clarification/Preference/Memory 三个 Conversation 组件接受折叠吗？ | 建议折叠（省 3 个图节点与 2 次 LLM 调用；对外能力不变）。若坚持独立，Phase 2/3 各 +1 节点，追加超时/降级契约与测试 |
| B | TripBrief v2 字段（adults/children/avoid_categories/crowd_preference）进入指纹口径，会使老会话首轮触发一次重排，接受吗？ | 建议接受（一次性、只影响 flag 开启后的新轮次） |
| C | OR-Tools 是否立项依赖评审？ | 建议先 2-opt（零依赖），金标实测不达标再立项 |
| D | PDF 导出引入渲染依赖吗？ | 建议 Phase 7 再议，Map/ICS 已覆盖旅行社交享场景 |
| E | ticket/event/notice 无源能力，接受「契约位 + BLOCKED 登记」吗？ | 建议接受（有契约无实现，供应商签约即插） |
| F | Hotel/Flight/Ticket 按 kind 参数化而非三 Agent，接受吗？ | 建议接受（同一状态机复制三份只放大风险） |
