# 旅游域企业级 Agent 化重构 · Phase 0 完整交付（现状审计 × 目标设计 合并稿）

> **用途**：自包含评审稿，可脱离代码仓库独立阅读；文中 `文件:行号` 引用指向仓库 `backend/` 下真实代码（基线 commit `03a8eb3`，2026-09-29），供逐条核对。
> **构成**：本稿 = `docs/travel-domain-current-audit.md`（审计单行本）+ `docs/travel-domain-production-design.md`（设计单行本）合并而成，内容一致、术语统一。
> **四条优先级**（一切取舍的裁决标准，来自重构任务书）：**稳定性 > Agent 数量；确定性 > 模型自由生成；数据真实性 > 内容丰富；不重复实现既有生产资产。**

---

## 阅读指南

| 章节 | 内容 |
|---|---|
| §0 | 术语表与系统背景（self-contained 必读） |
| §1 | 重构任务书摘要 + 需求覆盖对照表 |
| 第一部分（§2-§13） | 现状审计：目录 / 节点 / Agent / Tool / Provider / 数据库 / Memory / 测试 / 可保留资产 / 迁移映射 / 缺陷债 / 结论 |
| 第二部分（§14-§27） | 目标设计：边界 / 架构图 / Agent 职责 / Tool / Provider / State / 数据库 / 异常 / 超时 / Retry / Memory / 缓存 / 测试 / 上线 |
| 第三部分（§28-§30) | 迁移计划 Phase 0-7 执行卡 / 明确不做 / 请评审者重点确认的裁量点 |

**需求覆盖对照表（任务书 13 节 ↔ 本稿）**：

| 任务书要求 | 本稿位置 |
|---|---|
| 一 目标架构（Supervisor / Planning 9 Agent / Transaction 3 Agent） | §15 架构图、§16 Agent 职责 |
| 二 执行前审计（10 项盘点 + 重点检查 6 项生产资产） | §2-§13（生产资产=§10） |
| 三 Domain Boundary 拆分（禁止单 Graph 堆能力） | §14 范围与边界、§28 Phase 1 |
| 四 Agent 设计（Supervisor 意图层 / Requirement 两层 / POI 禁幻觉 / Route / Weather 三态） | §16 |
| 五 Tool 设计（Domain Tool / Provider / MCP 边界） | §17 |
| 六 Provider 统一规范（统一状态码，禁 Agent 处理 HTTP） | §18 |
| 七 多轮会话（TravelSessionState / 版本不覆盖） | §19 |
| 八 Quality Gate（时间/空间/用户约束/数据 → PASS/WARNING/REJECT） | §16.3 |
| 九 异常与稳定性（Tool 3-5s / Agent 10s / 整规划 30s / 降级披露） | §21/§22 |
| 十 缓存（Redis TTL） | §25 |
| 十一 Memory（长期偏好，不存临时信息） | §24 |
| 十二 迁移策略（Phase 0-7，每阶段 commit/测试/文档/回滚） | §28 |
| 十三 验收标准（production-design 文档 12 项内容） | 本稿第二部分即该文档，覆盖对照见 §1 阅读指南 |

---

## §0 术语表与系统背景

### 0.1 术语表（2026-09-29 用户拍板口径）

| 术语 | 含义 |
|---|---|
| **主 Agent** | 最上层的编排者。平台主图（router + GraphRunner）按意图把请求分发给各业务域；整个旅游域在主图视角下是被主 Agent 调用的一个域单元 |
| **域主 Agent** | 业务域内的调度者。旅游域即 **TravelSupervisor**：管理旅游生命周期（PLAN/MODIFY/QUERY/BOOK/CANCEL 意图判定）、判断当前阶段、调度子 Agent、处理多轮修改 |
| **子 Agent** | 域内各职能 Agent（requirement/destination/poi/route/weather/budget/risk/quality_gate/reporter/commerce/booking/payment…），共同被域主 Agent 调用；**禁止子 Agent 互相调用**，数据传递只走共享 state |
| **Agent 落地形态** | LangGraph 域图节点 + 统一 BaseAgent 契约（命名、输入输出契约、超时预算、降级策略、遥测五要素）。不是引入新框架 |
| **Domain Tool** | 旅游内部确定性能力（`travel.search_poi`、`travel.calculate_route`…），纯函数或薄封装，独立可测试 |
| **Provider** | 外部数据适配层。统一七态状态码返回，Agent/Tool 禁止自行处理 HTTP 异常 |
| **Quality Gate** | 质量门（现 validator 升格）：只判定不修改，输出 PASS/WARNING/REJECT |
| **TravelSessionState** | 旅游会话状态（trip_id/brief/current_plan/confirmed_items/plan_version/history） |
| **七态契约** | Provider 返回语义：SUCCESS/NOT_FOUND/UNAVAILABLE/RATE_LIMITED/INVALID_RESPONSE/TIMEOUT/UNAUTHORIZED + DISABLED |
| **生产资产** | 已被实测事故锤炼、有测试门禁咬合的实现，重构**冻结平移、禁止重复实现**（清单见 §10） |

> 与仓库旧文档的关系：旧四层规范有"勿把所有节点统称 Agent"口径，**用户已裁决按本表口径演进**——旧规范中"专家/expert"仅作为历史代码目录名（`experts/`）保留引用，Phase 3 随目录迁移改为 `*_agent.py`。

### 0.2 系统背景（self-contained）

- **平台**：电商 RAG + Multi-Agent 平台。Backend = FastAPI + LangGraph；Frontend = Next.js 14（用户端 :3100）；DB = PostgreSQL 双库（`agent_business` 业务 + `agent_memory` 元数据，本机容器映射 5433）；AI = DeepSeek（langchain-openai 兼容接口）；异步 = Celery + Redis；网关 = APISIX :9080 唯一入口。
- **主图**：`POST /chat/stream`（SSE 流式）→ GraphRunner → 入口门禁 → Router 预过滤链（优先级：客服 > 旅游 > 选品 > 预订 > 商务 > 三层主 Router，全零 LLM 正则）→ 按 `route_mode` 分发到对应域图。主图核心节点 9 个冻结不动。
- **域图机制**：每个业务域通过 `domains/__init__.py` 自注册进 `domain_graph_registry`，主图 builder 自动发现布线——新增/改域图零改 builder。旅游域有三个注册键：`travel`（规划）、`travel_commerce`（比价，子流）、`travel_booking`（预订，子流）。
- **checkpointer**：主图/客服/旅游三域共用 PostgresSaver 三表（checkpoints / checkpoint_blobs / checkpoint_writes），thread 前缀隔离（旅游 = `travel:{tenant}:{user}:{conv}`），TTL 7 天清理。
- **仓库铁律**（重构全程遵守）：G1 声明式注册启动期派生 fail-fast；G2 单一事实源禁止手抄派生量；G3 谁定义谁注册；G4 例外登记台账。局部跑测试必须 `--no-cov`。多会话并行：提交必须双重路径限定。

---

## §1 重构任务书摘要

**目标架构**：

```
Travel Domain
Travel Supervisor（域主 Agent：生命周期 / 阶段判定 / 调度 / 多轮修改；意图输出 PLAN|MODIFY|QUERY|BOOK|CANCEL）
 ├── Planning Domain：Requirement / Destination / POI Recommendation / Route Planning /
 │                    Weather / Budget / Risk / Quality Gate / Report（9 子 Agent）
 └── Transaction Domain：Commerce / Booking / Payment-Confirmation（3 子 Agent）
```

**六原则**：Agent 负责推理和流程控制；Tool 负责确定性能力；Provider 负责外部数据；Validator 负责质量控制；Memory 负责用户长期偏好；Booking 负责交易安全。

**关键约束**：不直接删除重写（已存在部分实现，设计边界混乱）；分 Phase 0-7 渐进迁移，每阶段 commit/测试/文档/回滚方案；执行前先审计。

---

# 第一部分 现状审计（Phase 0）

## §2 当前目录结构

```
backend/travel/                      # 域根（全部能力堆在一层，仅 commerce/booking 有子目录 —— 这正是"边界混乱"的实态）
├── register.py                      # 域图自注册 + install_travel_providers() 接线
├── graph_builder.py                 # 规划主图拓扑（10 节点）+ checkpointer 三级降级
├── graph_state.py                   # TravelGraphState + new_travel_graph_input + 指纹/planning_reset
├── travel_graph_node.py             # 主图适配器（thread 命名 / resume 三态 / run 同步）
├── slot_filler.py                   # 槽位抽取（844 行，纯规则零 LLM）
├── supervisor.py                    # 调度状态机（decide() 15 条规则）
├── planning.py                      # must_go 解析契约（names_match 唯一事实源）
├── repair.py                        # 局部修复器（kept_required / 防震荡）
├── validator.py                     # 四轴校验 + coverage + pool（655 行）
├── reporter.py                      # Markdown 渲染（纯模板零 LLM）
├── recommend.py                     # 目的地推荐（已接线：追问 + GET /travel/recommend）
├── quality_metrics.py               # Prometheus + 结构化质量事件
├── timeutil.py                      # "HH:MM" 分钟算术
├── models/                          # 数据契约（brief/poi/itinerary/validation/candidate_plan/graph_result）
├── experts/                         # 五"专家"（历史称谓；目标态=子 Agent：base/poi/transit/weather/budget/risk）
├── commerce/                        # 比价子图（18 文件）
└── booking/                         # 预订子图（18 文件）

backend/providers/travel/            # Provider 层（现位于 travel/ 域外 —— 目标态迁入）
├── __init__.py / poi.py / transit.py / facts.py        # 业务消费面
└── live/                            # Provider 运行时（约 2900 行）
    ├── result.py                    # 七态 ProviderStatus + Freshness + ProviderResult
    ├── contracts.py / capabilities.py / errors.py      # 契约 / 能力账 / 错误映射
    ├── cache.py / quota.py / resilience.py / telemetry.py / health.py   # 共享基建
    ├── tencent.py                   # 腾讯 LBS 适配器（529 行：Place/Route/Weather 主源）
    ├── qweather.py                  # 和风备用源（2026-09-29 f4f9628 落地，240 行）
    ├── commerce_contracts.py / commerce_adapter_base.py / fake_commerce.py
    └── booking/                     # booking provider 契约 + fake（339 行）

backend/tools/travel/                # Domain Tool 层（1203 行）
├── poi.py / poi_seed.py             # POI 检索纯函数 + 3 城 31 条种子
├── routing.py / live_map.py         # 通勤估算 + 腾讯实时路线
├── cost.py                          # 预算估算（CNY 口径）
├── knowledge.py                     # RAG 检索封装（无显式超时）
└── preferences.py                   # 偏好持久化（travel_preferences 表）

backend/skills/travel_poi/           # 主图 Skill（capability=travel.poi_search，唯一注册项）
backend/app/api/routes/travel.py     # REST：plan/ics/feedback/preferences/recommend
backend/app/api/routes/maps.py       # REST：18 个地图能力端点（域外共享）
```

前端：`frontend/src/app/travel/page.tsx`（535 行，独立表单页，REST 直连不走 SSE）；聊天通路对旅游帧零特殊处理（reporter 输出普通 Markdown delta）。

## §3 当前 LangGraph 节点

**规划主图（10 节点，唯一有 checkpointer 的子图）**：

```
START → travel_slot_filler → travel_supervisor
supervisor ─Command(goto)─→ poi / transit / weather / budget / risk / validator / repair（各自静态边回 supervisor）
           ─→ travel_reporter → END
```

- 调度顺序固定：poi → transit → weather → budget → risk → validate →（error 则 repair，上限 2 轮）→ report。
- 五个子 Agent **串行**非并行（`graph_builder.py:14-18`：数据依赖 poi→transit→weather→budget/risk，可并行收益有限）。
- 四条终止护栏：`TRAVEL_MAX_STEPS=14` / 修复上限 2 轮 / `repair_stalled`（error 签名 2 轮不变）/ 子 Agent 失败无产物。
- checkpointer 三级降级（`graph_builder.py:135-202`）：postgres（默认）→ MemorySaver（degraded）→ 无；.env `TRAVEL_CHECKPOINTER_ENABLED=true` 已开。

**commerce 子图（2 节点，无 checkpointer）**：`travel_commerce_slot_filler → travel_commerce_executor`（mode=fake）。

**booking 子图（2 节点，无 checkpointer，订单事实全在 PG）**：`travel_booking_resolver → travel_booking_executor`（provider=fake_booking_native）。celery beat 注册恢复扫描 `travel_booking_recovery_scan`。

**入口路由**（`orchestration/graph/router_node.py:341-540`，全零 LLM）：多轮续跑靠三层：`TravelPendingResolver`（cancel > avoid_patch > 值型补槽 > new_run）→ `ContinuationResolver` → 域内 checkpoint 续跑。

## §4 当前 Agent 现状

现状没有独立 "Agent 层"——域图节点即"专家"（历史称谓，目标态=子 Agent）。`experts/base.py` 有统一执行框架（`TravelExpertResult` + `run_expert_safely` 异常不穿透），但**无超时控制、无统一输入输出契约声明、无降级策略字段**——与目标架构差距最大的一点。

| 现状（历史称谓） | 位置 | LLM | 职责（目标态称谓） |
|---|---|---|---|
| slot_filler | `travel/slot_filler.py` | 无（纯规则；`config/travel.py:62-66` 写明 P1 才引 LLM 兜底，未实现） | 13 槽抽取 + 指纹检测 + planning_reset + 追问（→ requirement 子 Agent 规则层 + destination 子 Agent） |
| supervisor | `travel/supervisor.py` | 无（确定性状态机） | stage 推进 + 护栏（→ 域主 Agent 的调度层；**缺意图层**） |
| poi 专家 | `travel/experts/poi.py` | 无 | 候选池打分（上限 60）+ 骨架分配 + must_go 三态（→ poi 子 Agent） |
| transit 专家 | `travel/experts/transit.py` | 无 | 最近邻排程 + 午餐占位 + 版本盖章（→ route 子 Agent） |
| weather 专家 | `travel/experts/weather.py` | 无 | 坏天气换点（必去永不换）+ 预报窗口披露（→ weather 子 Agent） |
| budget 专家 | `travel/experts/budget.py` | 无 | 四分项估算、只算不判（→ budget 子 Agent） |
| risk 专家 | `travel/experts/risk.py` | 无（RAG 原文摘录，刻意不用 ask()） | 来源警告 + 免责 + knowledge_refs（→ risk 子 Agent） |
| validator | `travel/validator.py` | 无（纯规则零 IO） | 四轴 + coverage + pool + 三级分治（→ quality_gate） |
| repair | `travel/repair.py` | 无 | 违反码确定性动作 + kept_required + 防震荡（保留，quality gate 的修复执行侧） |
| reporter | `travel/reporter.py` | 无（模板渲染） | Markdown + 诚实披露（→ report 子 Agent） |

主图能力面：`travel.plan` 刻意不注册 Skill（有状态多步流程归域图）；仅 `travel.poi_search` 注册。

## §5 当前 Tool（7 文件 1203 行）

| Tool | 职责 | 外部依赖 |
|---|---|---|
| `poi.py`（173 行） | `search_poi` 纯函数检索（打分：必去×1000+标签×10+rating） | 无（读种子） |
| `poi_seed.py`（123 行） | 3 城 31 条种子（福州 11/厦门 10/杭州 10），**坐标/营业时间/票价自声明为示例值** | 无 |
| `routing.py`（185 行） | `estimate_leg`：live provider 优先→本地估算回落（haversine×1.35÷速度模型） | 腾讯（注入式） |
| `live_map.py`（366 行） | 腾讯 LBS 传输封装：5QPS 节流 + 3 次失败熔断 60s + 6 线程预热（4s 预算） | 腾讯 HTTP |
| `cost.py`（76 行） | 四分项预算（门票/餐饮/住宿/通勤），CNY | 无 |
| `knowledge.py`（54 行） | RAG 检索（kb_id=travel，top_k=3），**无显式超时** | RAG pipeline |
| `preferences.py`（162 行） | 偏好 upsert/读取（软失败绝不挡规划） | PG |

与目标命名 `travel.search_poi` / `travel.calculate_route` / `travel.get_weather` / `travel.calculate_budget` 的映射：前三者已有对应实现（`poi.py`/`routing.py`/`cost.py`）；**`travel.get_weather` 缺位**——天气现为子 Agent 直调 Provider，无独立 Tool。

## §6 当前 Provider（约 3391 行）

- **七态契约**（`live/result.py:28-38`）：SUCCESS / NOT_FOUND / UNAVAILABLE / RATE_LIMITED / INVALID_RESPONSE / TIMEOUT / UNAUTHORIZED + 第八态 DISABLED（配置关闭≠故障，不触发降级）。底层异常→状态单一映射 `live/errors.py:34-55`。Freshness 四值：live/cached/stale/unknown。
- **共享基建**：分数据 TTL 缓存（place 600s / route 120s / weather 300s / price 120-180s；Redis 两级 + stale-if-error 600s 宽限；仅 NOT_FOUND 负缓存 60s）；Redis INCR 日预算软停（跨午夜自恢复，三个预算 env 默认 0=不限）；single-flight；专用线程池；超时预算 place 3s / route 4s / weather 6s。
- **适配器**：腾讯 LBS（place/route/weather 主源）、和风 QWeather（备用源，主败才降级、双败保留主因）、FakeHotel/Flight（显式测试数据源，走与真实同一套执行流）。
- **关键缺口**：① 城市级 POI 候选检索**无 live 源**（`providers/travel/poi.py:103-114` 显式返回空——腾讯不支持逐城全量+营业时间），种子是唯一来源；② `ticket.facts`（票价/营业时间核实）implemented=False（腾讯 WebService 无此字段）；③ `TRAVEL_USE_LIVE_MAP=false`（.env:235，09-28 测评后未还原）——**当前实际跑纯本地估算通勤**。

## §7 当前数据库表

| 表 | migration | 用途 |
|---|---|---|
| `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` | PostgresSaver 自建 | 主图/客服/旅游三域共用的图执行态（TTL 7 天） |
| `travel.booking_quotes` | 052 | Quote 不可变事实快照（DB 级 immutable trigger） |
| `travel.booking_orders` | 052 | 八态订单（CAS + status_version 单调） |
| `travel.booking_webhook_inbox` | 052 | webhook 去重收件箱 |
| `travel.booking_events` | 052 | append-only 审计账 |
| `ai.idempotency_records` | 031 建 / 047 补列 | 全局副作用幂等账本（booking 复用） |
| `travel_preferences` | **运行时自动建表**（`preferences.py:54-64`，与 feedback 同款先例） | 用户偏好（user_id 主键 + origin/preferences JSONB/pace/diet/lodging/transport） |
| `travel_feedback`（同类） | 运行时自动建表 | 行程单反馈 |

## §8 当前 Memory 使用方式

- **旅游长期偏好**（已有）：slot_filler 每轮 upsert 用户显式表达的偏好（只写表达过的字段）；跨轮首轮预填（显式表达优先）；**预填在指纹计算之前**（防每轮重排抖动）；读写失败全部软降级。
- **图执行态记忆**：PostgresSaver checkpoint + `brief_fingerprint`（9 槽 sha1）+ `planning_reset()`（指纹变化/NEW_RUN/降级时清产物保 brief）+ 版本链（`plan_version/parent_plan_version/brief_version/change_reason`）。
- **会话摘要记忆**：`_sync_travel_run` 把 run 身份/阶段/pending 写进 ConversationContext（服务端 seq CAS 事件账），作为无 checkpoint 时的 graceful reconstruction 基底。
- **缺口**：无 trip_id 实体（同会话多 trip 无法区分历史）；confirmed_items（用户裁决）未结构化沉淀；偏好不含负偏好（"不购物/不爬山"只能当轮生效）。

## §9 当前测试覆盖

- `backend/tests/travel/`：35 文件 **约 544 个测试函数**（slot_filler 49 / travel_graph 38 / validator 32 / provider_layer 30 / scenarios 23 / providers 23 / repair 19 / versioning 18 / checkpointer 18 / qweather_backup 15 …；commerce/ 79 函数；booking/ 39 函数——真 PG 5433，PG 不可达 skip 不假绿）。
- **金标门禁**（`tests/travel/test_quality_golden.py:38-49` 阈值冻结）：valid_poi_rate=1.0、must_go_coverage=1.0、avoid_violation=0、duplicate=0、day_count_accuracy=1.0、hard_constraint≥0.98、unsupported_fact=0、budget_silent_over=0、单日在途≤150min、单日负载≤780min；数据集 `evaluation/datasets/travel/cases.jsonl` 34 条。
- **评测 runner 四套**：travel（联网金标）/ travel-commerce（H1-H12、F1-F12）/ travel-booking（B1-B20 探针，生产门 T1-T12=0）/ travel-provider（8 探针离线）。
- skip 全部是"PG 不可达不假绿"守卫，**无 xfail**。

## §10 当前可保留资产（生产资产，重构禁止重复实现）

| 资产 | 位置 | 为什么是生产资产 |
|---|---|---|
| **validator 四轴 + pool + coverage** | `travel/validator.py` | 六轴规则全部有实测事故背书；34 金标门禁与之咬合 |
| **repair 局部修复 + kept_required + 防震荡** | `travel/repair.py` | `repair_stalled` 分支是死循环事故（撞 recursion_limit、用户侧行程丢失）换来的 |
| **provider 七态契约 + 共享基建** | `providers/travel/live/` | 缓存/配额/熔断/single-flight/stale-if-error 全部实机验证过（联网金标 33/34） |
| **checkpoint 跨轮契约** | `graph_state.py`（fingerprint/planning_reset）+ thread 命名 | 三次实测事故修复（预置默认值清空成果 / 旧行程当新需求 / 跨租户串行程） |
| **booking 状态机 + 幂等账本 + 三模型契约** | `travel/booking/` + `shared/provider_idempotency.py` | 交易核心冻结（八态 CAS 状态机、`ai.idempotency_records` 复用、UNKNOWN→IN_DOUBT 绝不猜成败），052 四表 + immutable trigger |
| **commerce fail-closed 纪律** | `travel/commerce/normalize.py` + `identity.py` + `ranking.py` | Decimal+ISO4217、sha256 指纹、确定性排序、deeplink 白名单，与评测生产门咬合 |
| **版本链** | `models/itinerary.py` stamp_version | 每次修改生成新版本不覆盖旧版（已满足任务书 §七） |
| **测试基线** | 544 用例 + 34 金标 + 4 评测 runner | 重构的安全网本体 |
| **和风备用源** | `live/qweather.py` + FallbackWeatherProvider | 刚落地冻结 |

## §11 当前需要迁移部分（现状 → 目标映射）

| 现状 | 问题 | 目标位置 |
|---|---|---|
| `travel/slot_filler.py` | 纯词表上限锁死（D3/D4/D5 口语漏抓）；844 行混抽取+指纹+追问+推荐 | `planning/requirement_agent.py`（规则层）+ LLM Structured Output 第二层；推荐拆 `destination_agent.py` |
| `travel/experts/*.py` | 无统一 Agent 契约（无超时/无降级字段） | `planning/{poi,route,weather,budget,risk}_agent.py`（transit 改名 route）+ `core/` 统一 BaseAgent |
| `travel/validator.py` | 命名不含 Gate 语义；unverified POI 无独立检查项 | `planning/quality_gate.py`（逻辑平移 + 三态结论 + POI_UNVERIFIED warning） |
| `travel/supervisor.py` + `travel_graph_node.py` | 只管规划 stage，无意图层；booking/commerce 入口游离（D2 两跳断根因） | `core/supervisor.py`（意图分发 + stage 调度两层） |
| `travel/graph_state.py` + `models/` | 无 trip_id/confirmed_items/history 结构化沉淀 | `core/state.py` + `core/contracts.py` |
| `backend/providers/travel/` | 位置在域外 | `travel/providers/{poi,map,weather,hotel,flight}/`（Phase 4） |
| `tools/travel/` | 缺 `travel.get_weather`；knowledge 无超时 | `travel/tools/`（命名对齐）+ weather tool 补位 |
| `tools/travel/preferences.py` | 不支持负偏好持久化 | `travel/memory/`（扩展 avoid 类偏好） |
| `commerce/`、`booking/` | 入口不统一、无挂起恢复 | `commerce/commerce_agent.py`、`booking/booking_agent.py` + payment 子 Agent 收口（Phase 5/6） |
| 散落遥测 | 出口分散 | `core/events.py` 统一事件出口 |

## §12 已知缺陷与债

**实测 7 缺陷**（2026-09-28 实机测评，总判定 45~50%）：

| # | 级 | 缺陷 | 精确位置 |
|---|---|---|---|
| D1 | P1 | ICS 导出 500（中文目的地必现） | `app/api/routes/travel.py:201-206` Content-Disposition 内嵌中文，缺 RFC 6266 `filename*=UTF-8''` |
| D2 | P1 | 预订对话两跳必断（"帮我订福州酒店"→反问日期→答"10月20日入住"掉出预订域） | booking 无域内挂起恢复；`booking_prefilter.py` 单消息正则 |
| D3 | P1 | "再去福州玩两天""N天游"漏路由；完成后"取消吧"接不住 | `travel_prefilter.py:27-35` 词表缺口 + 域完成态无常驻语义 |
| D4 | P2 | "2个大人"排成 1 人 | `slot_filler.py:113-117` 词表 |
| D5 | P2 | "节奏慢一点"不生效 | `models/brief.py:46-47` 词表 |
| D6 | P2 | 关天气后仍声称"使用了天气预报" | `experts/risk.py` 静态文案未按 provider 状态裁剪 |
| D7 | P2 | /travel 页无鉴墙（未登录可 plan/导出/反馈） | `resolve_identity` 允许匿名 |

**结构性缺口**（目标设计逐项覆盖）：
- 子 Agent 无单节点超时（`run_expert_safely` 不限时）、整图无 deadline（只靠 recursion_limit=25+）、RAG 检索无显式预算；
- booking 生产接线断头：`webhook.ingest_webhook` / `reconciliation.reconcile_order` / `manual_resolve` **无任何 HTTP/admin API 入口**（只有测试与评测 runner 调用）——IN_DOUBT 解除闭环在生产依赖不存在的入口；
- `candidate_plans` 纯占位无实现；PRICE_CHANGED 替代 Quote 钩子无消费方（`revalidate.py:33-36`）；
- 死代码：`reschedule_after_repair`（transit.py:308-323 无调用方）、`booking/reporter.py:44` 引用不存在的 `quote['nights']`、`providers/travel/booking/contracts.py` Phase7 预留；
- 重复：`_quote_expired` 两份、stale 容忍转换两份、槽位组装三份、预订正则两份；
- `.env` 未还原：`TRAVEL_USE_LIVE_MAP=false`（备份 `d:/tmp/env.backup-20260928-travel-eval`）。

## §13 审计结论

工程质量本身是高的（分层干净、fail-fast、诚实披露、544 用例），问题集中在**数据面**（3 城 31 条种子 POI，坐标自称示例值）与**交易面**（fake-only 供应商，真实供应商双 BLOCKED——BLOCKED_BY_EXTERNAL_PROVIDER，不因重构假装解决）。重构的关键动作是"重排边界 + 换理解层 + 接数据源"，而不是重写可靠性骨架。

---

# 第二部分 目标架构设计

## §14 范围、原则与边界

**范围**：`backend/travel/` 域内重排 + 理解层升级 + 数据面接入。**不改动**：主图 builder（9 核心节点冻结）、`shared/provider_idempotency.py` 冻结层、booking 状态机 / commerce 归一门 / validator 六轴 / repair 防震荡 / checkpoint 跨轮契约（§10 生产资产清单）。

**分层原则**（任务书六原则 → 落地规则）：
- 域主 Agent（TravelSupervisor）负责推理与流程控制：意图判定 + 阶段推进 + 调度；**子 Agent 互相禁止调用**，一律 Supervisor 分发，数据传递只走共享 state；
- Domain Tool 负责确定性能力：`travel.*` 纯函数/薄封装，独立可测试；
- Provider 负责外部数据：禁止 Agent/Tool 直接处理 HTTP 异常（七态契约唯一出口）；
- Quality Gate 只判定不修改；Memory 只存跨 trip 长期偏好；Booking 域负责交易安全（幂等/状态机/重验价不动）。

**目标目录**（任务书 §三 → 落地）：

```
travel/
├── core/            # supervisor.py（域主 Agent）、state.py、contracts.py、events.py、agent_base.py
├── planning/        # requirement_agent / destination_agent / poi_agent / route_agent /
│                    # weather_agent / budget_agent / risk_agent / quality_gate / repair / reporter
├── commerce/        # commerce_agent（现子图平移）
├── booking/         # booking_agent（现子图平移）
├── providers/       # poi/ map/ weather/ hotel/ flight/ runtime/（现 backend/providers/travel 迁入）
├── tools/           # search_poi / calculate_route / get_weather / calculate_budget / retrieve_knowledge
├── memory/          # preferences（+负偏好扩展）
└── evaluation/      # 数据集与 runner 接口
```

## §15 目标架构图

```
                          ┌───────────────────────────────────────────────┐
主图主 Agent（不动）       │              Travel Domain                    │
route_mode="travel" ────→ │  travel/core/supervisor.py  域主 Agent        │
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
        providers/（七态契约层原样迁入）   memory/（长期偏好）   evaluation/
```

调用纪律：`域主 Agent → 子 Agent → Tool → Provider` 单向；跨域只经域主 Agent action 分发；子 Agent 间数据传递只走 state 键。

## §16 Agent 职责

### 16.1 TravelSupervisor（域主 Agent，`core/supervisor.py`）

现有 `decide()` stage 机**原样保留**（护栏/决策顺序/`repair_stalled` 一律不动），新增**入口意图层**：

```json
{"action": "PLAN | MODIFY | QUERY | BOOK | CANCEL"}
```

| action | 判定来源 | 去向 |
|---|---|---|
| PLAN | prefilter 命中 / TravelSessionState 无 plan | 规划链（requirement → … → reporter） |
| MODIFY | pending_resolver 的 avoid_patch / Continuation / 指纹变化 | 规划链（planning_reset 语义不变，产出新 plan_version） |
| QUERY | 纯查询意图（"鼓浪屿门票多少"→ poi 检索/知识库，不出整案） | poi_agent 单步 / knowledge 检索 |
| BOOK | commerce/booking 意图（现独立 prefilter 语义收编） | Transaction Domain |
| CANCEL | `is_cancel_run_query`（现逻辑平移） | 取消当前 run（CANCEL_TRAVEL_RUN 原子 mutation 保留） |

迁移要点：Phase 1 只立 action 枚举与映射表（现有全部入口**等价归入**对应 action，零行为变化）；Phase 5/6 才把 booking/commerce 入口判定收编为域主 Agent 分发，**TravelSessionState 增加挂起的 booking 意图**（修 D2）。判定规则全部规则化（确定性优先），不引入 LLM 意图分类。

### 16.2 Planning Domain 九子 Agent

| 子 Agent | 现状映射 | 变更 |
|---|---|---|
| **requirement_agent** | slot_filler 规则层全量保留 | **两层抽取**：第一层现有规则（含日期遮蔽等事故修复，原样）；第二层 LLM Structured Output（`TRAVEL_REQUIREMENT_LLM_ENABLED` 默认 off）——仅当规则层 required 槽缺失或低置信时触发；LLM 的 destination 只能在城市白名单/候选池内产出（防幻觉），数值字段范围校验，失败/超时回落规则结果+追问。必须覆盖："两个人"（party_size=2）、"带孩子"（pace→relaxed + 偏好亲子）、"慢节奏"（已有）、"不购物"（avoid+负标签 shopping）、"不爬山"（负标签 outdoor/hiking）。词表缺口 D3/D4/D5 在规则层同步补 |
| **destination_agent** | slot_filler 的城市解析 + recommend.py | 独立成子 Agent：城市归一（白名单/别名）、不支持城市明示、追问时推荐榜 |
| **poi_agent** | experts/poi.py | 平移 + 契约化。**铁律：POI 只能来自 Provider/种子库/RAG，禁止 LLM 幻想景点**——输出 CandidatePOI（现 Poi 契约改名别名期），source/verification_status 强制携带 |
| **route_agent** | experts/transit.py | 平移改名（语义修正：地理排序+时刻排程+路线），`rebuild_days` 唯一重排实现不变 |
| **weather_agent** | experts/weather.py | 平移 + **三态输出**：`available`（SUCCESS 且 fresh）/ `stale`（stale-if-error 命中）/ `unavailable`（七态失败或 DISABLED）。失败绝不阻塞规划，reporter 按 weather_status 渲染（**顺带修 D6**） |
| **budget_agent** | experts/budget.py | 原样平移（只算不判） |
| **risk_agent** | experts/risk.py | 原样平移（RAG 原文摘录，不用 ask()） |
| **repair** | repair.py | 原样平移（quality gate 的修复执行侧，生产资产冻结） |
| **quality_gate** | validator.py | 六轴逻辑原样平移；对外结论统一三态：`PASS`（无 error）/ `WARNING`（仅 warning，放行+披露）/ `REJECT`（有 error，触发 repair）。现有 errors/warnings/decision_required 三级是三态的内部实现。**新增**：`POI_UNVERIFIED` 检查项（腾讯补全的 unverified POI 计 warning）——对应任务书"数据：虚假 POI"（池外 POI 已由现有 POI_NOT_IN_CANDIDATES error 覆盖） |
| **reporter** | reporter.py | 平移 + weather_status/quality 结论字段渲染 |

### 16.3 Quality Gate 检查项（任务书 §八 → 落地）

| 任务书要求 | 现状覆盖 | 结论分级 |
|---|---|---|
| 时间：超营业时间 | TIME_CLOSED / TIME_CLOSED_WEEKDAY（必去项→decision_required，否则 error） | REJECT（error）/ 用户裁决（decision_required） |
| 时间：单日时间过长 | PACE_TOO_INTENSE / TIME_DAY_OVERRUN / GEO_SCATTER | REJECT / WARNING |
| 空间：距离异常 | GEO_FAR_LEG（单段 ≥105min error） | REJECT / WARNING |
| 用户约束：必去丢失 | MUST_GO_MISSING（coverage，warning）+ 修复侧 kept_required 保护 | WARNING + 披露 |
| 数据：虚假 POI | POI_NOT_IN_CANDIDATES（error）+ 新增 POI_UNVERIFIED（warning） | REJECT / WARNING |

`passed`（无 error）语义不变 → PASS；decision_required 机制（interrupt 默认关）保留。

### 16.4 Transaction Domain 三子 Agent

- **commerce_agent**：现 commerce 子图平移（2 节点内部结构不变，fail-closed 归一门/确定性排序/deeplink 白名单冻结）。
- **booking_agent**：现 booking 子图平移（八态状态机/幂等账本/三模型契约/重验价**一律不动**）。
- **payment_agent**：**不是新实现**——现有 `confirm_and_execute`（确认门+执行+收口）与 `reconciliation`/`webhook` 的 Agent 化命名重组：确认校验（cfp 绑定/TTL/金额 Decimal 互检）→ 幂等执行 → 结局收口（SUCCEEDED/NOT_SENT/REJECTED/UNKNOWN→IN_DOUBT）。**同时补生产接线缺口**：webhook 接收与对账/人工裁决的 admin API 入口（Phase 6）。

## §17 Tool 设计

规则：旅游内部确定性能力=Domain Tool；第三方=Provider；只有跨系统共享能力考虑 MCP（当前无——`/api/map/*` 已覆盖前端共享面，MCP 维持 2 server 不增）。

| Tool | 现状 | 目标 |
|---|---|---|
| `travel.search_poi` | `tools/travel/poi.py`（Skill 底座已在用） | 平移，签名冻结 |
| `travel.calculate_route` | `tools/travel/routing.py` | 平移，route_km 保持纯函数（O(n²) 不走网络） |
| `travel.get_weather` | **缺位** | 新增薄封装（包装 Provider forecast_payload + 三态归一），weather_agent 与 REST 共用 |
| `travel.calculate_budget` | `tools/travel/cost.py` | 平移 |
| `travel.retrieve_knowledge` | `tools/travel/knowledge.py` | 平移 + 补 4s 显式预算 |
| `travel.preferences` | `tools/travel/preferences.py` | 迁 `travel/memory/` |
| `travel.resolve_place` / `travel.live_leg` | `tools/travel/live_map.py` | 平移（归入 map provider 传输面） |

Tool 契约三规沿用仓库规范：错误 JSON 可读可重试、「查不到」与「查不了」分开、独立可测试；一律不注册主图 capability（面不变）。

## §18 Provider 统一规范

所有外部服务统一经 Provider Layer 返回七态（`ProviderResult`），Agent/Tool **禁止自行处理 HTTP 异常**：

```
SUCCESS / NOT_FOUND / TIMEOUT / RATE_LIMITED / UNAUTHORIZED / UNAVAILABLE
+ INVALID_RESPONSE（响应存在但校验失败——脏数据防护，实测态）
+ DISABLED（配置关闭——与故障区分，不触发降级）
```

> 与任务书 §六 的六态关系：任务书六态是本规范的子集；多出的 INVALID_RESPONSE（脏数据拒绝）与 DISABLED（配置关≠故障）是实测锤炼出的增量，保留不砍。

Provider 列表（`travel/providers/`，七态契约层原样迁入）：

| 子目录 | Provider | 状态 |
|---|---|---|
| `poi/` | SeedPOIProvider（唯一候选源）+ TencentPlaceProvider（must_go 补全，unverified 标注） | 保留；**扩展点：真实城市级候选源**（待供应商调研，契约已留好——实现 `search(city)` 即插） |
| `map/` | TencentTransitProvider（live 通道）+ LocalEstimate（兜底） | 保留；`.env TRAVEL_USE_LIVE_MAP` 还原 true（Phase 4 验收项） |
| `weather/` | TencentWeatherProvider（主）+ QWeatherProvider（备，FallbackWeatherProvider 组合） | 保留（刚冻结） |
| `hotel/` | FakeHotelSearchProvider；真实适配器位 | fake 保留；live 仍 BLOCKED_BY_EXTERNAL_PROVIDER（不因重构假装解决） |
| `flight/` | FakeFlightSearchProvider；真实适配器位 | 同上 |
| `booking/` | 三 profile fake + 契约 | 冻结平移 |
| `runtime/`（live/ 更名） | result/contracts/cache/quota/resilience/telemetry/errors/health | **冻结平移**，TTL 调整见 §25 |

## §19 State 设计（TravelSessionState）

**决策：不新造存储。** TravelSessionState = 图 state 的显式 schema（现 TravelGraphState 重组 + 补字段），持久化继续走 PostgresSaver checkpoint（三域共表、TTL 7 天、可序列化纪律不变），run 摘要继续同步 ConversationContext。理由：现有跨轮契约已通过三次事故修复并有 38 个测试钉住，重造存储是纯风险无收益。

```python
# core/state.py（新增字段加粗）
TravelSessionState:
  trip_id: str              # 新增。uuid7，NEW_RUN 时生成；同会话多 trip 可区分；进 trace/run 摘要
  brief: TravelBrief        # 含新增负偏好标签（avoid_categories: ["shopping","hiking"]）
  brief_fingerprint / brief_change_reason / brief_changed_fields   # 原样（指纹口径不变）
  current_plan: Itinerary   # 原样（版本链 stamp_version 不动——天然满足"每次修改生成新版本，不覆盖旧计划"）
  confirmed_items: list     # 新增。用户裁决结构化沉淀：[{poi_id, name, decision: keep|drop, source, decided_at}]
  plan_version: int         # 从 current_plan.plan_version 提升（读写唯一入口）
  history: list             # 新增。版本摘要 [{plan_version, brief_version, change_reason, created_at, quality}]（不含全文，控制 checkpoint 体积）
  # —— 以下全部原样 ——
  candidates/day_plan/itinerary/validation/repair_*/notes/knowledge_refs
  stage/step_count/expert_history/supervisor_decision/persistence_status
  user_message/user_id/session_id/conversation_id/travel_route/reconstruct_brief
  booking_intent: dict|None # 新增（Phase 5）。挂起的交易意图 {kind, slots, quote_ref}——修 D2 的结构载体
```

多轮会话验证场景（任务书 §七）：第一次"杭州5天"（PLAN，建 trip）→ 第二次"不要购物"（MODIFY：avoid 更新→指纹变化→新 plan_version）→ 第三次"改3天"（MODIFY：days 更新→新版本，旧版本在 history 可溯）。

契约纪律：`new_travel_graph_input()` 只放本轮输入不预置产物默认值（事故教训保留）；读一律 `.get()`；Pydantic 契约整体迁 `core/contracts.py`。

## §20 数据库设计

**新增 0 张表**。现状 7 组表全部保留（§7）：checkpoint 三表、booking 四表（052）、`ai.idempotency_records`、`travel_preferences`、feedback 表。

- `travel_preferences` 扩展不改表：`preferences JSONB` 内加负偏好（`avoid_categories`），upsert 逻辑扩展（Phase 3，向后兼容）。
- booking 表/幂等表/immutable trigger 一律不动。
- 明确不做：trip 历史表（history 在 checkpoint 内、ConversationContext 摘要已覆盖查询面；确有"列出我的行程"产品需求时再立表，届时 trip_id 已是现成主键）。

## §21 异常策略

| 层 | 策略 |
|---|---|
| Provider | 七态唯一出口；UNAVAILABLE/RATE_LIMITED/TIMEOUT → 上层降级，**绝不用 fake 冒充 live、绝不编造数据** |
| Tool | 返回 `{"error": ...}` 结构化错误，「查不到」（NOT_FOUND，可结束）与「查不了」（UNAVAILABLE，可降级）分开 |
| 子 Agent | `run_expert_safely` 扩展：异常→`status=failed` 不穿透（现状）+ **超时→`status=degraded`**（新增，§22）；每个 Agent 声明降级行为（如 weather=继续规划、knowledge=只留免责） |
| 规划链 | 任一增强型子 Agent 降级不阻塞出单；quality_gate REJECT → repair（≤2 轮）→ 仍 REJECT 则如实输出+披露未解决项；候选池空/城市不支持 → 如实说明 |
| 交易域 | 维持现状 fail-closed：UNKNOWN→IN_DOUBT 绝不猜成败、无法复核禁止假定可购、webhook 迟到/找不到订单→quarantine |
| 全局 | 禁止 `except Exception: pass`；软失败必须留 notes/事件 |

## §22 超时策略

| 层 | 预算 | 实现方式 | 超时行为 |
|---|---|---|---|
| 单 Tool（Provider 调用） | 3-5s | 已有：place 3s / route 4s / **weather 6s→5s**（Phase 4 调）；knowledge 补 4s | 七态 TIMEOUT → 降级 |
| 单子 Agent | **10s** | 新增：BaseAgent 契约带 `timeout_s`，`run_expert_safely` 包 `asyncio.wait_for`（五个子 Agent 现为同步函数，Phase 3 实现时包线程+future，不改函数签名） | `status=degraded` + note「{agent} 超时降级」，链路继续 |
| 整次规划 | **30s** | 新增：图入口注入 `deadline_at`（monotonic），域主 Agent 每次放行前检查：超限→强制 REPORT（输出已生成部分+披露「受时间预算限制」） | 部分成果 + 如实披露，绝不静默截断 |
| REST `/api/travel/plan` | 前端 55s 兜底 | 不变（30s 内必返回） | — |

30s 与现有 `TRAVEL_MAX_STEPS=14`/recursion_limit 是两层护栏（步数防逻辑死循环、deadline 防单步拖慢累积），互不替代。

## §23 Retry 策略

| 对象 | 策略 |
|---|---|
| Provider 网络调用 | 现状保留：传输层重试 1 次退避 0.4s + 3 次连续失败熔断 60s + stale-if-error（600s 宽限）；**不加重试**（单调用预算 3-5s 内无空间，多重重试只会撞子 Agent 10s 上限） |
| 规划链内 repair | ≤2 轮 + `repair_stalled` 防震荡（事故资产，禁止"优化"掉） |
| 业务终态 | booking 业务终态异常不重试；可重入仅 NOT_SENT/not_found_safe_to_retry |
| 会话级 | 用户重发/重试走 NEW_RUN/继续语义，不做自动整案重跑 |
| LLM（requirement 第二层） | **零重试**：失败即回落规则层+追问（LLM 是增益不是依赖） |

## §24 Memory 策略

- **存**（跨 trip 长期）：偏好标签（含新增负偏好 avoid_categories）、pace、diet、lodging、transport、origin。写入时机不变（用户显式表达才写，upsert 不覆盖未表达字段）。
- **不存**（单次临时信息）：目的地/天数/预算/日期等 brief 槽位、具体行程、booking 订单事实（在 PG 订单表）、对话原文。
- **读取**：跨轮首轮预填（现状）；LLM requirement 第二层把预填偏好作为上下文（不改变"显式表达优先"合并序）。
- **位置**：`travel/memory/`。边界不变：偏好读写失败软降级绝不挡规划；预填发生在指纹计算之前（防每轮重排抖动）。

## §25 缓存（Redis）

| 数据 | 现状 TTL | 目标 TTL | 说明 |
|---|---|---|---|
| POI（种子/候选池） | place 600s | **24h** | 种子与解析点位变化极慢；负缓存（NOT_FOUND）维持 60s |
| 路线 | 120s | **30min** | 任务书要求；通勤估算兜底永远可用 |
| 天气 | 300s | **10min** | 恰好一致，落配置 |
| 价格（commerce offer） | 120-180s | **1-5min**（120/180s 维持） | 已在区间内 |
| stale-if-error 宽限 | 600s | 不变 | — |

全部经现有 runtime 层分数据 TTL 机制，改配置不改代码。

## §26 测试方案

- **回归门（每阶段必跑）**：`cd backend && python -m pytest tests/travel/ tests/orchestration/ -q --no-cov` + 四个一致性测试 + 金标 `tests/travel/test_quality_golden.py`。
- **阶段专项**：Phase 1 域边界守护测试（锁定新目录映射）；Phase 2 LLM 抽槽评测集 `evaluation/datasets/travel/slot/cases.jsonl`（≥50 例，含 D3/D4/D5 口语 + "两个人/带孩子/慢节奏/不购物/不爬山"必过集；LLM 关/开双跑口径）；Phase 3 超时注入测试（fake 慢 provider 验证 10s/30s 降级不挂死）；Phase 5/6 复用四套评测 runner（生产门 T1-T12=0 不许回退）。
- **纪律**：只 mock 外部边界（provider/LLM/clock）；新功能带回归测试；强断言；测试 import 与生产 import 同 commit 更新。
- **Phase 7 收口**：全量回归（分块）+ `e2e_demo.py` + 联网金标（阈值不放松）+ 实机 SSE 20 轮走查（含 D1-D3 回归验证）。

## §27 上线方案

1. **全程 flag 灰度**：`TRAVEL_REQUIREMENT_LLM_ENABLED`（默认 off）、`TRAVEL_SESSION_STATE_V2`（Phase 3）、`TRAVEL_UNIFIED_INTENT`（Phase 5）；任一异常按 flag 回退，旧行为全程保留到 Phase 7。
2. **顺序**：规划域（Phase 2/3）→ Provider 归位（Phase 4）→ 交易域（Phase 5/6）→ 全量验收（Phase 7）。每阶段独立 commit（pathspec 双重限定），阶段内测试绿才进下一阶段。
3. **回滚**：每阶段 = 一组可独立 revert 的 commit；shim 层保证旧 import 在 Phase 7 前始终可用；flag 关闭即回旧行为；数据面零迁移（新表 0 张，回滚无数据风险）。
4. **上线检查单**：`.env` 还原 `TRAVEL_USE_LIVE_MAP=true`；`/health` travel_providers 组件 healthy；联网金标硬约束 ≥0.98；D1-D3 实机复测通过；四层规范旅游域章节与 AGENTS.md 同步更新（Phase 7 文档任务）。

---

# 第三部分 迁移计划

## §28 Phase 0-7 执行卡

> Phase 0（审计+设计，本稿）已完成。

| Phase | 改动 | 验收 | 回滚 |
|---|---|---|---|
| **1 建立边界** | 建 `core/ planning/ tools/ memory/ evaluation/ providers/` 骨架 + `core/agent_base.py`（BaseAgent 契约）+ `core/events.py` + supervisor action 枚举与现有入口映射表（零行为变化）+ 域边界守护测试 + 四层规范术语对齐（"域主 Agent 调度子 Agent"口径） | 全量 travel 测试绿；守护测试通过；无行为 diff | revert 单 commit（纯新增文件+规范文档） |
| **2 Requirement Agent** | slot_filler 规则层 git mv → `planning/requirement_agent.py`（旧路径 shim）；destination_agent 拆分；D3/D4/D5 词表补丁；负偏好字段；LLM 第二层框架（flag off）+ 评测集 | slot_filler 49 用例绿；slot 评测集规则层基线落盘；金标不回退 | revert commit；shim 保证旧 import 不断 |
| **3 Planning Agents** | 五子 Agent+quality_gate+repair+reporter 逐个 git mv（**每个独立 commit**）；BaseAgent 契约接入（超时 10s/降级声明/weather 三态）；整图 deadline 30s；TravelSessionState 新字段（flag 控制）；memory/ 迁入+负偏好 | 每步测试绿；金标不回退；超时注入测试过；D6 修复验证 | 逐 commit revert |
| **4 Provider 归位** | `backend/providers/travel/` → `travel/providers/`（git mv + shim；先确认命名空间无其他占用）；TTL 调整；weather 超时 5s；`.env TRAVEL_USE_LIVE_MAP=true` 还原；`travel.get_weather` Tool 补位 | provider_layer 30 用例 + 探针 8/8；真实腾讯探针 phase A 过；联网金标不回退 | revert + `.env` 回退 |
| **5 Commerce 收编** | commerce 子图平移 `commerce/commerce_agent.py`；意图入口收编进域主 Agent（flag）；booking_intent 挂起槽（修 D2 结构部分） | commerce 79 用例绿；H/F 评测不回退；两跳对话实验通过 | flag off + revert |
| **6 Booking 收编** | booking 子图平移（状态机/账本**零改动**）；payment_agent 命名重组；**补 admin API**：webhook 接收 + IN_DOUBT 对账/人工裁决端点（管理端鉴权 + 写操作审批门 `ensure_approved`） | booking 39 用例绿；B1-B20 生产门 T1-T12=0；webhook HMAC 实测；审批门生效验证 | flag off + revert（补的 API 端点独立 commit） |
| **7 E2E 收口** | 删 shim；死代码清理；全量回归+联网金标+实机走查；规范/AGENTS.md/README 口径更新；`.env` 检查单核验 | §27 检查单全绿；全量测试分块跑完 | 不适用（收尾阶段） |

## §29 明确不做（防复杂度失控）

不引入新存储/新消息队列/新框架；不加 Planner LLM 重排（candidate_plans 维持占位）；不给 POI/Route/Budget/Risk 子 Agent 加 LLM（确定性优先）；不动主图 builder；不动 booking 冻结层语义；不因重构假装解决真实供应商 BLOCKED（hotel/flight live 仍是待签约事项，非本次范围）。

## §30 请评审者重点确认的裁量点

1. **Agent 落地形态**：域主 Agent = 现有 supervisor 的意图层扩展 + stage 机保留；子 Agent = 现有节点平移 + BaseAgent 契约，不引入新框架。替代方案（全新 Agent 抽象层/框架）被"稳定性 > Agent 数量"否决。
2. **TravelSessionState 复用 checkpoint，新表 0 张**：trip_id/confirmed_items/history 是图 state 显式 schema，持久化走 PostgresSaver。替代方案（独立 trip 表 + 状态服务）被"不重复实现生产资产"否决；若未来需要"列出我的历史行程"产品能力，trip_id 已是现成主键。
3. **payment_agent 不是新实现**：现有 confirm/reconcile/webhook 的 Agent 化重组；顺带补 webhook/对账的 admin API 生产断头。
4. **理解层只给 requirement 一个 LLM**：其余子 Agent 保持确定性。weather 的坏天气判定维持关键词规则（不升级 LLM 判断），POI 禁幻觉靠"候选池封闭"保证。
5. **迁移顺序**：先规划域后交易域；commerce/booking 的意图收编放 Phase 5/6（有 flag），Phase 1-4 期间现有 prefilter 入口不变。
