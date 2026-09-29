# 旅游域现状审计（Travel Domain 重构 · Phase 0）

> 基线：commit `03a8eb3`（2026-09-29）。本审计为重构设计的前置只读盘点，不含任何代码修改。
> 方法：五路并行深读（规划主链 / 五专家 / providers / commerce+booking / 路由+前端+缺陷档案）+ 补充核查。
> 关联：目标设计见 `docs/travel-domain-production-design.md`；域×工具×服务×凭据映射见 `docs/architecture/domain-service-map.md`。
> 结论先行：**工程质量本身是高的（分层干净、fail-fast、诚实披露、544 用例），问题集中在数据面（3 城 31 条种子 POI）与交易面（fake-only 供应商）。重构的关键动作是"重排边界 + 换理解层 + 接数据源"，而不是重写可靠性骨架。**

---

## 1. 当前目录结构

```
backend/travel/                      # 域根（全部能力堆在一层，仅 commerce/booking 有子目录）
├── register.py                      # 域图自注册 + install_travel_providers() 接线
├── graph_builder.py                 # 规划主图拓扑（10 节点）+ checkpointer 三级降级
├── graph_state.py                   # TravelGraphState + new_travel_graph_input + 指纹/planning_reset
├── travel_graph_node.py             # 主图适配器（thread 命名/resume 三态/run 同步）
├── slot_filler.py                   # 槽位抽取（844 行，纯规则零 LLM）
├── supervisor.py                    # 调度状态机（decide() 15 条规则）
├── planning.py                      # must_go 解析契约（names_match 唯一事实源）
├── repair.py                        # 局部修复器（kept_required/防震荡）
├── validator.py                     # 四轴校验 + coverage + pool（655 行）
├── reporter.py                      # Markdown 渲染（纯模板零 LLM）
├── recommend.py                     # 目的地推荐（已接线：追问 + GET /travel/recommend）
├── quality_metrics.py               # Prometheus + 结构化质量事件
├── timeutil.py                      # "HH:MM" 分钟算术
├── models/                          # 数据契约（brief/poi/itinerary/validation/candidate_plan/graph_result）
├── experts/                         # 五专家（base/poi/transit/weather/budget/risk）
├── commerce/                        # 比价子图（18 文件，STOP K）
└── booking/                         # 预订子图（18 文件，STOP L）

backend/providers/travel/            # Provider 层（在 travel/ 之外）
├── __init__.py / poi.py / transit.py / facts.py        # 业务消费面
└── live/                            # Provider 运行时（约 2900 行）
    ├── result.py                    # 七态 ProviderStatus + Freshness + ProviderResult
    ├── contracts.py / capabilities.py / errors.py      # 契约/能力账/错误映射
    ├── cache.py / quota.py / resilience.py / telemetry.py / health.py   # 共享基建
    ├── tencent.py                   # 腾讯 LBS 适配器（529 行：Place/Route/Weather 主源）
    ├── qweather.py                  # 和风备用源（f4f9628，240 行）
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

## 2. 当前 LangGraph 节点

**规划主图（10 节点，唯一有 checkpointer 的子图）**：

```
START → travel_slot_filler → travel_supervisor
supervisor ─Command(goto)─→ poi / transit / weather / budget / risk / validator / repair（各自静态边回 supervisor）
           ─→ travel_reporter → END
```

- 调度顺序固定：poi → transit → weather → budget → risk → validate →（error 则 repair，上限 2 轮）→ report。
- 五专家**串行**非并行（`graph_builder.py:14-18`：数据依赖 poi→transit→weather→budget/risk，可并行收益有限）。
- 四条终止护栏：`TRAVEL_MAX_STEPS=14` / 修复上限 2 轮 / `repair_stalled`（error 签名 2 轮不变）/ 专家失败无产物。
- checkpointer 三级降级（`graph_builder.py:135-202`）：postgres（默认，PostgresSaver）→ MemorySaver（degraded）→ 无；`TRAVEL_CHECKPOINTER_ENABLED` .env 已开 true；thread = `travel:{tenant}:{user}:{conv}`，与 CS/主图共表但前缀隔离。

**commerce 子图（2 节点，无 checkpointer）**：`travel_commerce_slot_filler → travel_commerce_executor`（.env `TRAVEL_COMMERCE_ENABLED=true`，mode=fake）。

**booking 子图（2 节点，无 checkpointer，订单事实全在 PG）**：`travel_booking_resolver → travel_booking_executor`（.env 已开，provider=fake_booking_native）。celery beat 注册恢复扫描 `travel_booking_recovery_scan`（maintenance 队列）。

**入口路由**（`orchestration/graph/router_node.py:341-540`，全零 LLM）：优先级 客服 > 旅游 > 选品 > 预订 > 商务 > 主 Router。旅游命中靠 `travel_prefilter.py`（22 个强信号词 ≥2，或种子城市+信号词/N天）；多轮续跑靠三层：`TravelPendingResolver`（cancel > avoid_patch > 值型补槽 > new_run）→ `ContinuationResolver` → 域内 checkpoint。

## 3. 当前 Agent

**没有独立的"Agent 层"——术语现状：域图节点即专家（expert）**。`experts/base.py` 定义统一执行框架 `TravelExpertResult` + `run_expert_safely`（异常不穿透转 failed），但**无超时控制、无统一输入输出契约声明、无降级策略字段**——这是与目标架构差距最大的一点。
> 术语决议（2026-09-29 用户拍板）：目标态统一称 Agent——域内 Supervisor=主 Agent（调度者）、职能节点=子 Agent，共同被主 Agent 调用；现状 "expert/专家" 为历史称谓，Phase 3 随目录迁移改为 `*_agent.py`。本节表格保留历史称谓以对应真实代码路径。

| 现状 | 位置 | LLM | 职责 |
|---|---|---|---|
| slot_filler | `travel/slot_filler.py` | 无（纯规则，`config/travel.py:62-66` 明确 P1 才引 LLM 兜底，未实现） | 13 槽抽取 + 指纹检测 + planning_reset + 追问 |
| supervisor | `travel/supervisor.py` | 无（确定性状态机） | stage 推进 + 护栏 |
| poi 专家 | `travel/experts/poi.py` | 无 | 候选池打分（上限 60）+ 骨架分配 + must_go 三态解析 |
| transit 专家 | `travel/experts/transit.py` | 无 | 最近邻排程 + 午餐占位 + 版本盖章 |
| weather 专家 | `travel/experts/weather.py` | 无 | 坏天气换点（必去永不换）+ 预报窗口披露 |
| budget 专家 | `travel/experts/budget.py` | 无 | 四分项估算（只算不判） |
| risk 专家 | `travel/experts/risk.py` | 无（RAG 检索原文摘录，刻意不用 ask()） | 来源警告 + 免责 + knowledge_refs |
| validator | `travel/validator.py` | 无（纯规则零 IO） | 四轴 + coverage + pool + 三级分治 |
| repair | `travel/repair.py` | 无 | 违反码确定性动作 + kept_required + 防震荡 |
| reporter | `travel/reporter.py` | 无（模板渲染） | Markdown + 诚实披露 |

**主图能力面**：`travel.plan` 刻意不注册 Skill（有状态多步流程归域图，`skills/travel_poi/skill.py` docstring）；仅 `travel.poi_search` 注册（12 Skill 之一）。

## 4. 当前 Tool（`backend/tools/travel/`，7 文件 1203 行）

| Tool | 职责 | 外部依赖 |
|---|---|---|
| `poi.py`（173 行） | `search_poi` 纯函数检索（打分：必去×1000+标签×10+rating）+ `travel_poi_search_tool`（Skill 底座） | 无（读种子） |
| `poi_seed.py`（123 行） | 3 城 31 条种子（福州 11/厦门 10/杭州 10），**坐标/营业时间/票价自声明为示例值** | 无 |
| `routing.py`（185 行） | `estimate_leg`：live provider 优先→本地估算回落（haversine×1.35÷速度模型）；`route_km` 永远纯函数 | 腾讯（注入式） |
| `live_map.py`（366 行） | 腾讯 LBS 传输封装：place/route/weather + 5QPS 节流 + 3 次失败熔断 60s + 6 线程预热（4s 预算） | 腾讯 HTTP |
| `cost.py`（76 行） | 四分项预算（门票/餐饮/住宿/通勤），CNY，城市档位表 | 无 |
| `knowledge.py`（54 行） | `retrieve_travel_knowledge`（kb_id=travel，top_k=3），**无显式超时** | RAG pipeline |
| `preferences.py`（162 行） | 偏好 upsert/读取（软失败，绝不挡规划） | PG |

命名与目标规范 `travel.search_poi` / `travel.calculate_route` / `travel.get_weather` / `travel.calculate_budget` 的映射：search_poi、calculate_route、calculate_budget 已有对应实现（`poi.py`/`routing.py`/`cost.py`）；**`travel.get_weather` 缺位**——天气目前是 expert 直接调 Provider，无独立 Tool 封装。

## 5. 当前 Provider（`backend/providers/travel/`，约 3391 行）

- **七态契约**（`live/result.py:28-38`）：SUCCESS / NOT_FOUND / UNAVAILABLE / RATE_LIMITED / INVALID_RESPONSE / TIMEOUT / UNAUTHORIZED + 第八态 DISABLED（配置关闭≠故障，不触发降级）。底层异常→状态单一映射入口 `live/errors.py:34-55`。
- **Freshness 四值**：live / cached / stale / unknown（`result.py:41-47`）。
- **共享基建**：分数据 TTL 缓存（place 600s / route 120s / weather 300s / price 120-180s，Redis 两级 + stale-if-error 600s 宽限，仅 NOT_FOUND 负缓存 60s）；Redis INCR 日预算软停（键含日期跨午夜自恢复，三个预算 env 默认 0=不限）；single-flight；专用线程池；超时预算 place 3s / route 4s / weather 6s。
- **适配器**：腾讯 LBS（place/route/weather 主源）、和风 QWeather（备用源，`FallbackWeatherProvider` 主败才降级、双败保留主因，f4f9628）、FakeHotel/Flight（显式测试数据源，走与真实同一套执行流）。
- **关键缺口**：① 城市级 POI 候选检索**无 live 源**（`providers/travel/poi.py:103-114` TencentPOIProvider.search 显式返回空——腾讯不支持逐城全量+营业时间），`SeedPOIProvider` 是唯一来源；② `ticket.facts`（票价/营业时间核实）implemented=False，腾讯 WebService 无此字段，契约冻结 unknown；③ `TRAVEL_USE_LIVE_MAP=false`（.env:235，09-28 测评后未还原）——**当前实际跑纯本地估算通勤**。

## 6. 当前数据库表

| 表 | migration | 用途 |
|---|---|---|
| `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` | PostgresSaver 自建 | 主图/CS/旅游三域共用的图执行态（TTL 7 天清理，`checkpointer_cleanup.py`） |
| `travel.booking_quotes` | 052 | Quote 不可变事实快照（DB 级 immutable trigger） |
| `travel.booking_orders` | 052 | 八态订单（CAS + status_version 单调） |
| `travel.booking_webhook_inbox` | 052 | webhook 去重收件箱 |
| `travel.booking_events` | 052 | append-only 审计账 |
| `ai.idempotency_records` | 031 建 / 047 补列 | 全局副作用幂等账本（booking 复用） |
| `travel_preferences` | **运行时自动建表**（`preferences.py:54-64`，与 feedback 同款先例，不走 alembic） | 用户偏好（user_id 主键 + origin/preferences JSONB/pace/diet/lodging/transport） |
| `travel_feedback`（同类） | 运行时自动建表 | 行程单反馈 |

## 7. 当前 Memory 使用方式

- **旅游长期偏好**（已有，P1-1）：`slot_filler` 每轮抽取后 upsert 用户显式表达的偏好（只写表达过的字段）；跨轮首轮预填（本轮显式表达永远优先）；**预填在指纹计算之前**（不造成每轮重排抖动）；读写失败全部软降级。
- **图执行态记忆**：PostgresSaver checkpoint + `brief_fingerprint`（9 槽 sha1）+ `planning_reset()`（指纹变化/NEW_RUN/降级时清产物保 brief）+ 版本链（`plan_version/parent_plan_version/brief_version/change_reason`）。
- **会话摘要记忆**：`_sync_travel_run` 把 run 身份/阶段/pending 写进 ConversationContext（服务端 seq CAS 事件账），作为无 checkpoint 时的 graceful reconstruction 基底。
- **缺口**：无 trip_id 实体（一个会话多个 trip 无法区分历史）；confirmed_items（用户裁决记录）只存在于 interrupt payload 与 `kept_required` 文案，未结构化沉淀；偏好不含 avoid 类负偏好（"不购物/不爬山"只能当轮生效，不持久化）。

## 8. 当前测试覆盖

- `backend/tests/travel/`：35 文件 **约 544 个测试函数**。分布：根目录 22 文件约 426 函数（slot_filler 49 / travel_graph 38 / validator 32 / provider_layer 30 / scenarios 23 / providers 23 / slot_patch_boundary 20 / persistence 20 / dataset 19 / repair 19 / versioning 18 / checkpointer 18 / planning_contract 16 / qweather_backup 15 …）；commerce/ 79 函数；booking/ 39 函数（真 PG 5433，PG 不可达 skip 不假绿）。
- **金标门禁**（STOP I5）：`tests/travel/test_quality_golden.py:38-49` 阈值冻结——valid_poi_rate=1.0、must_go_coverage=1.0、avoid_violation=0、duplicate=0、day_count_accuracy=1.0、hard_constraint≥0.98、unsupported_fact=0、budget_silent_over=0、单日在途≤150min、单日负载≤780min；数据集 `evaluation/datasets/travel/cases.jsonl` 34 条。
- **评测 runner 四套**（非 pytest）：travel（联网金标）/ travel-commerce（H1-H12、F1-F12）/ travel-booking（B1-B20 探针，生产门 T1-T12=0）/ travel-provider（8 探针离线）。
- 域外强相关：`test_router_prefilter_order.py`（prefilter 顺序守护）、`test_travel_pending_resolver.py`、`test_domain_checkpoint_namespace.py`（thread 前缀两两隔离）。
- skip 全部是"PG 不可达不假绿"守卫，**无 xfail**。
- 局部跑纪律：必须 `--no-cov`（pytest.ini 挂死 `--cov-fail-under=55`）。

## 9. 当前可保留资产（生产资产，重构禁止重复实现）

| 资产 | 位置 | 为什么是生产资产 |
|---|---|---|
| **validator 四轴 + pool + coverage** | `travel/validator.py` | 六轴规则全部有实测事故背书；34 金标门禁与之咬合 |
| **repair 局部修复 + kept_required + 防震荡** | `travel/repair.py` | `repair_stalled` 分支是死循环事故（撞 recursion_limit、用户侧行程丢失）换来的 |
| **provider 七态契约 + 共享基建** | `providers/travel/live/` | 缓存/配额/熔断/single-flight/stale-if-error 全部实机验证过（STOP J 联网金标 33/34） |
| **checkpoint 跨轮契约** | `graph_state.py`（fingerprint/planning_reset）+ thread 命名 | 三次实测事故修复（预置默认值清空成果/旧行程当新需求/跨租户串行程） |
| **booking 状态机 + 幂等账本 + 三模型契约** | `travel/booking/` + `shared/provider_idempotency.py` | STOP L 核心冻结（`TRAVEL_BOOKING_TRANSACTION_CORE_PASS=true`），052 四表 + immutable trigger |
| **commerce fail-closed 纪律** | `travel/commerce/normalize.py` + `identity.py` + `ranking.py` | Decimal+ISO4217、sha256 指纹、确定性排序、deeplink 白名单，与评测生产门咬合 |
| **版本链** | `models/itinerary.py` stamp_version | 每次修改生成新版本不覆盖旧版（已符合目标要求） |
| **测试基线** | 544 用例 + 34 金标 + 4 评测 runner | 重构的安全网本体 |
| **和风备用源** | `live/qweather.py` + FallbackWeatherProvider | 刚落地（f4f9628），冻结契约同构 |

## 10. 当前需要迁移部分（现状 → 目标映射）

| 现状 | 问题 | 目标位置 |
|---|---|---|
| `travel/slot_filler.py` | 纯词表上限锁死（D3/D4/D5 口语漏抓）；单文件 844 行混抽取+指纹+追问+推荐接线 | `travel/planning/requirement_agent.py`（规则层）+ LLM Structured Output 第二层；推荐拆 `destination_agent.py` |
| `travel/experts/*.py` | 无统一 Agent 契约（无超时/无降级字段声明） | `travel/planning/{poi,route,weather,budget,risk}_agent.py`（transit 改名 route）+ `core/` 统一 BaseAgent |
| `travel/validator.py` | 命名不含"Gate"语义，unverified POI 未单独成检查项 | `travel/planning/quality_gate.py`（逻辑平移 + WARNING 分级扩展） |
| `travel/supervisor.py` + `travel_graph_node.py` | 只管规划 stage，无意图层（PLAN/MODIFY/QUERY/BOOK/CANCEL）；booking/commerce 入口游离在外（D2 两跳断根因） | `travel/core/supervisor.py`（意图分发 + stage 调度两层） |
| `travel/graph_state.py` + `models/` | 无 trip_id/confirmed_items/history 结构化沉淀 | `travel/core/state.py` + `core/contracts.py` |
| `backend/providers/travel/` | 位置在 travel/ 域外（目标架构要求域内闭环） | `travel/providers/{poi,map,weather,hotel,flight}/`（Phase 4） |
| `tools/travel/` | 缺 `travel.get_weather` 独立 Tool；knowledge 无超时 | `travel/tools/`（命名对齐）+ weather tool 补位 |
| `travel/preferences.py`（实为 tools/ 下） | 不支持负偏好持久化 | `travel/memory/`（扩展 avoid 类偏好） |
| `commerce/`、`booking/` | 与规划域并列但入口不统一、无挂起恢复 | `travel/commerce/commerce_agent.py`、`travel/booking/booking_agent.py` + payment/confirmation 收口（Phase 5/6） |
| 散落遥测（quality_metrics / 各专家 qm.record_*） | 出口分散 | `travel/core/events.py` 统一事件出口 |

## 11. 已知缺陷与债（迁移顺带修复项）

**实测 7 缺陷**（2026-09-28 实机测评，报告 `docs/archive/2026-09/2026-09-28-旅游Agent实机测评与目标差距报告.md`，总判定 45~50%）：

| # | 级 | 缺陷 | 精确位置 |
|---|---|---|---|
| D1 | P1 | ICS 导出 500（中文目的地必现） | `app/api/routes/travel.py:201-206` Content-Disposition 直接内嵌目的地，缺 RFC 6266 `filename*=UTF-8''` |
| D2 | P1 | 预订对话两跳必断（反问日期后掉域） | booking 无域内挂起恢复；`booking_prefilter.py` 单消息正则 |
| D3 | P1 | "再去福州玩两天""N天游"漏路由；完成后"取消吧"接不住 | `travel_prefilter.py:27-35` 词表缺口 + 域完成态无常驻语义 |
| D4 | P2 | "2个大人"排成 1 人 | `slot_filler.py:113-117` 词表 |
| D5 | P2 | "节奏慢一点"不生效 | `models/brief.py:46-47` 词表 |
| D6 | P2 | 关天气后仍声称"使用了天气预报" | `experts/risk.py` 静态文案未按 provider 状态裁剪 |
| D7 | P2 | /travel 页无鉴墙（未登录可 plan/导出/反馈） | `resolve_identity` 允许匿名 |

**结构性缺口**（目标设计逐项覆盖）：
- 专家无单节点超时（`run_expert_safely` 不限时）、整图无 deadline（只靠 recursion_limit=25+）、RAG 检索无显式预算；
- booking 生产接线断头：`webhook.ingest_webhook` / `reconciliation.reconcile_order` / `manual_resolve` **无任何 HTTP/admin API 入口**（只有测试与评测 runner 调用）——IN_DOUBT 解除闭环在生产依赖不存在的入口；
- `candidate_plans` 纯占位无实现；PRICE_CHANGED 替代 Quote 钩子无消费方（`revalidate.py:33-36`）；
- 死代码：`reschedule_after_repair`（transit.py:308-323 无调用方）、`booking/reporter.py:44` 引用不存在的 `quote['nights']`、`providers/travel/booking/contracts.py` Phase7 预留；
- 重复：`_quote_expired` 两份、stale 容忍转换两份、槽位组装三份、预订正则两份（commerce 有单一事实源纪律而 booking 未贯彻）；
- `.env` 未还原：`TRAVEL_USE_LIVE_MAP=false`（备份 `d:/tmp/env.backup-20260928-travel-eval`）。

## 12. 风险与约束（重构必须遵守的仓库铁律）

1. **不动主图**：builder.py 9 核心节点固定；域图通过 `domains/__init__.py` 自注册自动发现，不得手写进 builder。
2. **G1-G4**：声明式注册、单一事实源（capabilities.yaml 禁手抄派生量）、谁定义谁注册、例外必须登记规范 §4 台账——目录迁移后注册链不能断。
3. **局部测试跑必须 `--no-cov`**；改 params_schema/prompt 后跑 planner 评估。
4. **多会话并行纪律**：pathspec 双重限定提交；每阶段独立 commit 可回滚。
5. **术语对齐**：`docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md` 口径"勿把所有节点统称 Agent"——本次引入的 "Agent" 命名需在设计文档中显式定义并登记台账（G4），避免文档体系自相矛盾。
6. **checkpointer 共表**：旅游 checkpoint 与主图/CS 共用三表，state 键变更必须保持 checkpoint 可序列化纪律（dict 进、dict 出，运行时对象只在节点边界经 load_*/save_*）。
