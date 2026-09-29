# Phase 4 迁移审计报告 — Provider 归位 + ProviderRouter + Evidence 迁移（只读，未改代码）

> 依施工军规：Phase 4 施工前先审计，**确认决策点前不改任何代码**。基线：Phase 3（`26a7c8c` / `3de085a` / `6882ee7`，报告 `74c5491`）。
> 审计对象：`backend/providers/travel/`（22 文件 3391 行）+ 全仓 46 个 `providers.travel` import 方 + 域层 Evidence 三落点 + validator/TTL/t25。
> 设计依据：v4 §8（ProviderRouter 选择链，冻结契约）+ v4 §4（Evidence 五字段与 confidence 基线，Phase 1 已落 `core/contracts.py`）+ v3 §2.2/§6 Phase 4 卡。
> 结论先行：**Phase 3 已把 Agent/Node 层的 Provider 感知清零（V1/V2/V3 收敛进 service），"归位"的架构收益从结构性退化为目录美学；而迁移成本里藏着冻结交易域（commerce/booking 共 11 个 import 方）的接触风险。推荐"原位不动 + Router 内聚"，ProviderRouter 以链账声明 + 工厂装配的最小形态落地（零行为变更，parity 门=qweather 15 用例），Evidence 在域层组装（provider 层零改动），三项 validator warning 全 warning 级、零 IO。**

---

## 一、Provider 归位裁决

### 1.1 事实底座（本轮全量核实，枚举无截断）

- `backend/providers/` 下**只有 travel 一个域**（1 个 `__init__.py` + `travel/` 22 文件 3391 行：live/ 运行时 18 + poi/transit/facts 业务面 3 + booking/ 契约 3——booking 3 文件计在 live 之外的 booking/ 子目录）。
- 全仓 `providers.travel` 命中 **47 个文件，其中真实 import 方 46 个**（`providers/__init__.py` 仅文档字符串提及）：

| 分层 | 数量 | 文件 |
|---|---|---|
| providers 内部自引用 | 16 | travel/__init__、booking/{__init__,contracts,fake}、live/{__init__,commerce_adapter_base,commerce_contracts,contracts,errors,fake_commerce,health,qweather,result,tencent}、poi、transit |
| travel 域内消费 | 12 | register、booking/{executor,provider_capabilities,reconciliation}、commerce/{models,normalize,reporter,service}、models/{candidate_plan,itinerary}、services/{poi_service,weather_service} |
| tools / app / evaluation | 6 | tools/travel/{live_map,routing}、app/api/routes/health、evaluation/runners/{travel_booking,travel_commerce,travel_provider} |
| 测试 | 12 | tests/travel/ 根 conftest、booking/{conftest,test_booking_lifecycle}、commerce/{conftest,test_commerce_availability,test_commerce_reporter,test_commerce_service}、test_agent_service_boundary、test_booking_candidate、test_provider_layer、test_providers、test_weather_qweather_backup |

- **全部 import 均为绝对路径** `from backend.providers.travel...`（含惰性函数内 import）；域外（scripts/、e2e）零引用。
- `backend/travel/providers/` 命名空间**无占用**（travel/ 下无同名包/模块，无遮蔽风险）。

### 1.2 两个方案的实际成本收益

**方案 2「git mv backend/providers/travel → travel/providers/」（v3 §2.2 原计划）**：

| 成本项 | 实测 |
|---|---|
| import 改写 | 46 文件绝对路径全改（或 providers/ 留 re-export shim 双轨到 Phase 8）——其中 **11 个属于冻结交易域**（commerce 4 代码+3 测试、booking 3 代码+2 测试+conftest，STOP K/L 冻结面），改它们的 import = 直接触碰冻结域文件，需要专项豁免裁决 |
| 审计链漂移 | Phase 0 审计（`docs/travel-domain-current-audit.md`，文件行号事实源）与 STOP J/K/L 三份冻结报告中的 providers 路径全部失效 |
| 行为回归面 | provider_layer 30 + providers 23 + qweather 15 + commerce 79 + booking 39 + 探针 8——为一次零行为变化的目录搬家背全量回归 |
| 收益 | 目录上"域内闭环"；v3 §2.2 映射表字面达成；`backend/providers/` 顶层空壳层消失 |

**方案 1「原位不动 + Router 内聚」**：

| 项 | 实测 |
|---|---|
| 成本 | 零迁移；Router 落 `backend/providers/travel/live/router.py`（与 FallbackWeatherProvider 同层，见 §二）；唯一"代价"是 v4 §8 示例路径 `travel/providers/runtime/router.py` 不按字面落地 |
| 收益保留度 | Phase 3 已达成归位的**实质目标**：services/（域内）是全仓唯二 Provider 触达层（poi_service:58-70 / weather_service:70-73），Agent/Node 零 Provider 感知且有静态扫描锁定（test_agent_service_boundary 18 例）——"域逻辑不感知外部数据源"这一冻结契约**已经成立**，与 providers/ 目录在不在 travel/ 下无关 |

### 1.3 推荐：方案 1（原位不动 + Router 内聚）

理由按权重：

1. **用户二轮拍板的治理先例**（Phase 2，5c15d3f）：「Phase 2 目标=建立职责边界，非代码搬迁」；Phase 3 D2 同款裁决（planning.py 原位，"迁移动机消失"）。施工军规明文「冻结的是 Domain Contract 不是实现结构；内部文件目录不冻结」——v4 §8 的 `travel/providers/runtime/router.py` 是**示例落位不是冻结契约**（v4 冻结清单列的是「ProviderRouter 选择链」语义）。
2. **冻结边界收益 > 目录收益**：46 个 import 方中 11 个在冻结交易域。为目录美学去改冻结域 import，违反「冻结层零接触」的更高优先级约束；用 shim 双轨则制造一层必须活到 Phase 8 的新债。
3. **风险不对称**：搬家是零行为变化 + 全量回归代价 + 审计链漂移；不动是零风险 + 一条 G4 台账登记（归位延后至 Phase 8 shim 删除同批，或域内闭环立项时与 `travel_plans` 表一并重估）。
4. `backend/providers/__init__.py` 的「后续其他域同构扩展」叙事：现状仅 travel 一域、STOP H 架构基线冻结后无第二域计划——该注释与"原位"不冲突（原位不动不需要改它）。

**若拍板方案 2**：必须接受 ①冻结交易域 import 豁免裁决（或 shim 双轨）②Phase 0 审计路径漂移 G4 登记 ③commerce+booking 全量回归纳入验收门；且 live→runtime 更名（v3 卡）随之捆绑，风险再乘一档。**不建议本期做。**

---

## 二、ProviderRouter 设计

### 2.1 现状：链已存在，缺的是"声明式"与"单一事实源"

| v4 §8 要求的链 | 现状实现 | 落点 |
|---|---|---|
| weather: [tencent, qweather] → stale → 七态失败 | **已实现**：`get_weather_provider()`（live/__init__.py:116-127）按 `is_qweather_configured()` 硬编码装配 `FallbackWeatherProvider`（68-104：主败才降级、DISABLED 不降级、双败保留主因）；stale-if-error 与七态失败在**各 adapter 内部**（共享 cache/resilience 基建，STOP J 冻结） | 硬编码在工厂函数里 |
| map: [tencent_live, local_estimate] | **已实现**：`routing.set_route_provider` 注入槽（routing.py:46-63，register.py:27 `install_travel_providers()` 启动接线）；`estimate_leg` 122-165：live 失败/异常→本地估算，带 source/observed_at/is_estimate/fallback_reason 标注 | 注入式，语义即链 |
| poi: [seed] | **已实现**：search_poi 纯函数读种子；点名补全走 `maps.place_resolve`（另一 capability，不算 poi 链头）；城市级 live 源=BLOCKED（capabilities.py:32-35 ticket 同款纪律） | 隐式 |
| price: [fake_commerce] | **已实现**：fake-only（STOP K 冻结域，live 恒 DISABLED） | 冻结域自治 |

### 2.2 Router 形态裁决（决策点 D2）

- **✅ 推荐：组装期链账装配**。新增 `live/router.py`：
  ① `PROVIDER_CHAINS: dict[capability, tuple[str, ...]]`——四条链的**单一声明事实源**（G2），weather 链装配从 `get_weather_provider()` 的硬编码改为查链账驱动（装配产物仍是 `FallbackWeatherProvider` 或裸 TencentWeatherProvider，**逐字节同构**）；
  ② `ttl_for(operation)` / `timeout_for(operation)` 薄读口——Evidence 组装取 expire_at 用（读 `cache.FRESH_TTLS` 与 `resilience.TIMEOUT_BUDGETS`，不复制第二份表）；
  ③ map/poi/price 链**只声明不接管**（v4 §8"先声明后接管"——map 已由 set_route_provider 注入槽承担，重复接线=为统一而统一）。
- **❌ 否决：调用期链执行器**。链的执行语义（fresh→stale-if-error→七态失败）已内嵌在各 adapter 内部的 STOP J 冻结共享基建里；Router 再实现一遍=第二套降级语义，直接违反「不重复实现生产资产」。
- **Agent/Node 零感知已达成**：Phase 3 已把 V1/V2/V3 收敛进 service（test_agent_service_boundary.py:46/84 静态扫描锁定 agents 与 expert 节点零 providers import）。Router 的全部触达面天然只在 service 层——**weather_service.fetch_forecast 一行不改即享 Router**（它经 `get_weather_provider()` 取数，工厂装配变了、消费面不变）。
- **parity 门**：qweather_backup 15 用例（本轮实测 15 passed）+ FallbackWeatherProvider 三条语义断言逐字保留（主败才降级/双败保主因/DISABLED 不降级）+ provider_layer 30 + providers 23 全绿；新增 Router 契约测试（链账完整性：声明中的 provider 名必须可解析、链序非空、capability 命名与 capabilities.py 能力账对齐）。

---

## 三、Evidence 附加落点（候选池 / 天气 / 知识三处）

### 3.1 契约与原料（Phase 1 已冻结，本轮核实齐备）

- 契约：`core/contracts.py::Evidence`（57-106，五字段+构造期不变量：confidence≤source_type 基线、缺 verified_at 上限 0.5）+ `CONFIDENCE_BASELINE`（LIVE .95/CACHE .85/RAG .7/SEED .5/ESTIMATE .5）+ `evidence_level()` 三档。**stale=0.6** 的降档在组装侧实现（0.6≤CACHE 基线 0.85，合法）。
- 原料（provider 层零改动即可组装）：
  - `ProviderResult`（result.py:50-79）：status 七态+DISABLED、freshness 四值、provider、source_id、observed_at——status×Freshness→LIVE/CACHE 的映射原料齐备；
  - `Poi`（models/poi.py）：source（默认 seed:local）、observed_at、verification_status（**默认 verified=种子数据；unverified=外部解析补全**，冻结语义）；
  - TTL 表 `cache.FRESH_TTLS` → expire_at = observed_at + TTL（v4 §4"现有 TTL 即 expire_at"）。

### 3.2 三落点与关键缺口

| # | 落点 | 组装规则 | 现状缺口 |
|---|---|---|---|
| 1 | **候选池**（poi_service.retrieve_candidates，38-71） | 种子 POI→SEED/0.5/expire_at=None（诚实：不冒充时效）；腾讯补全 POI→LIVE/0.95/verified_at=observed_at——坐标权威性归 Evidence；营业时间/票价的占位语义归 `verification_status` 字段（POI_UNVERIFIED warning 消费，见 §四——**字段级真相不并入 Evidence 单值**） | 无：candidates 本就以 dict（Poi.model_dump）进 state（experts/poi.py:77），Evidence 可从 Poi 字段纯函数派生 |
| 2 | **天气**（weather_service.fetch_forecast，62-87） | ProviderResult→(ok ? freshness==live ? LIVE .95 : CACHE .85 : 失败不产 Evidence)；stale→CACHE/0.6；expire_at=observed_at+FRESH_TTLS[weather]；source=provider 名（FallbackWeatherProvider 降级后 result.provider 天然标注实际服务源 qweather/tencent——**降级自动反映进信任口径**，v4 §8 要求） | **★ 关键缺口：预报不落 state**——weather 节点（experts/weather.py:63-96）节点内消费后只落 itinerary/weather_actions（repair_log），Evidence 无处可写。须新增 state 键并打通取数通道（D3/D4） |
| 3 | **知识**（risk_service.retrieve_knowledge，41-63） | RAG/0.7/source=`rag:<tag>`/expire_at=None；value=摘录引用（chunk 索引）而非全文（控 checkpoint 体积） | knowledge_refs 现为 list[str]（graph_state.py:103，reporter 消费）——Evidence 走**并行新增结构**，reporter 零改动（三档渲染属 Phase 6） |

### 3.3 存储形态（决策点 D3）

- **✅ 推荐：统一 state 键 `evidences: dict[str, dict]`**（fact_id→Evidence dict）：
  - validator SOURCE_STALE 单点消费（扫 evidences 判过期）；Phase 6 Assistant fact_id 引用单点；checkpoint 序列化纪律单点测试（纯 dict，符合"dict 进 dict 出"红线）；
  - graph_state.py 需显式声明新键（LangGraph updates 流纪律：节点写的新键必须入 state schema，`langgraph-updates-strips-unknown-keys` 事故教训）；`new_travel_graph_input()` **不预置**（跨轮契约 1）；全部 `.get()` 读取（跨轮契约 2，旧 checkpoint 无键→不误报）；
  - 体积评估：候选 60 POI×~150B + 天气摘要 ~300B + 知识 3 条 ≈ **12KB 级**，远小于 itinerary 本体，checkpoint 体积无虞。
- ❌ 散点字段（weather_evidence/knowledge_evidence 各一）：SOURCE_STALE 与未来 citation 要扫多处，且每加一类事实改一次 schema。
- 天气 value 用**摘要**（city/days 数/日期窗/served_by），不存全量预报（预报本体维持"节点内消费不落 state"的现状；Phase 6 Assistant 需要时再按需扩）。

### 3.4 天气取数通道（决策点 D4，补丁缝纪律）

- `fetch_forecast` 现返回二元组 `(forecast|None, reason)`，是 Phase 3 实战的补丁缝（4 个 weather 用例 monkeypatch 它）。
- **✅ 推荐：新增 `fetch_forecast_evidence(destination) -> (forecast, reason, evidence|None)` 三元组函数**（weather_service + ResearchAgent + experts/weather.py 裸名委托三层同步，补丁缝模式复刻），节点改调新函数；`fetch_forecast` 保留为兼容薄封装。
- 代价如实披露：**4 个存量 weather 补丁用例的 patch 目标需改指新函数**（机械替换，行为断言不变——Phase 3 已有同款先例：search_poi→retrieve_candidates 1 处）。❌ 直接改 `fetch_forecast` 返回元数=破坏缝语义，否决。

---

## 四、validator 3 项 warning（POI_UNVERIFIED / SOURCE_STALE / PREFERENCE_VIOLATION）

### 4.1 三项检查的精确语义与数据来源（全部零 IO）

| code | 判定 | 数据来源（全在 state/行程内） | 触发面实测 |
|---|---|---|---|
| `POI_UNVERIFIED` | 行程含 `verification_status=unverified` 的 POI | Itinerary 内 Poi 对象字段（现成） | **低**：种子 POI 默认 verified（models/poi.py:53-55 冻结语义）；只有腾讯补全点位触发 |
| `SOURCE_STALE` | 行程引用的 Evidence 已过期（expire_at < now）或 freshness=stale | state.evidences（§三新增）；datetime.now() 为纯计算非 IO（`evidence_level()` 同款先例） | **低**：现状唯一会过期的是天气 Evidence；旧 checkpoint 无 evidences 键→不告警（不误报） |
| `PREFERENCE_VIOLATION` | 行程 POI 命中 brief.avoid 负偏好 | Itinerary POIs × brief.avoid（load_brief 现成） | **低**：candidates 已被 search_poi 的 avoid 过滤前置拦截；仅补全/换点旁路可能引入。must_go 与 avoid 冲突时**不阻断**（kept_required 纪律：用户点名永不被静默丢弃），warning 只做冲突披露 |

### 4.2 接入方式与波及面

- **级别：全部 LEVEL_WARNING，不设 error**（用户指令 + v3 §3 口径一致）。结论映射零变化：errors→degraded/修复，warnings 不翻结论（validator.py:515-517 映射不触及）。
- **接入点**：不进既有六轴 `_run_axis`（轴函数签名只吃 itinerary；SOURCE_STALE 需要 state）——在 `travel_validator_node` 内 axes 之后追加一段独立检查（节点本就持有 state：candidates 读取是现成先例，validator.py:489-490），新增 code 常量入 `models/validation.py`（22-46 现有 CODE_* 表追加）。
- **confidence 波及（决策点 D5）**：`compute_confidence`（444-462）对 warnings 计 -0.05/条。三项触发面都低，但一旦触发即拉低分数。**推荐进扣分**（与既有 warning 同权，口径一致）；配套跑 validator 32 + scenarios 23 + user_decision + quality_metrics 分块验证相对断言（`test_confidence_drops_with_errors...` 等为相对比较，预期稳）。若要绝对零波及，可让新 warning 走"不计分披露"档——**不推荐**（制造两等 warning）。
- validator/repair/reporter 三门禁"独立保留、禁止吞并"纪律不变：本次只**加法**改 validator 一个文件 + validation.py 常量表，repair/reporter 零接触。

---

## 五、TTL 调整（v4/v3 拍板值 vs 现状）

### 5.1 缓存 TTL（cache.py FRESH_TTLS，35-52；改动须同步 J0 文档——头部注释自带义务）

| operation | 现状 | 目标（v3 §6 卡 + 用户本指令） | 评估 |
|---|---|---|---|
| place（POI） | 600s | **86400s（24h）** | place=坐标/名称等稳定事实，24h 合理；物理 TTL=fresh+grace 自动变 87000s；`get_cache` 的 max(FRESH_TTLS)+grace 自适应，无遗漏点 |
| route | 120s | **1800s（30min）** | 路况成分陈旧上界放宽到 30min——v4 已拍板；traffic_aware/is_estimate 字段自带语义不变；跨轮重排收益明显（120s 只覆盖单轮） |
| weather | 300s | **600s（10min）** | 与 §四 expire_at 直接挂钩：Evidence.expire_at=observed_at+600s |
| ticket / commerce 七项 / NEGATIVE 60s / STALE_GRACE 600s | 不变 | 不变 | 冻结交易域与负缓存纪律零接触 |

- 键格式不变 → **存量缓存条目自然过期，无迁移**。测试钉住核查：qweather 用例整体 monkeypatch FRESH_TTLS（test_weather_qweather_backup.py:112，不受常量变更影响）；commerce 用例只 patch 自己的条目。

### 5.2 超时预算（resilience.py TIMEOUT_BUDGETS 52-58 + config/travel.py:155）

- weather **6.0s → 5.0s**（v3 卡 + v4 §9.3 口径 place 3/route 4/weather 5）：`TRAVEL_WEATHER_TIMEOUT_S` 默认值 "6"→"5"（resolve_budget 读 config，生效点唯一）；
- place 3.0 / route 4.0 / ticket 3.0 / commerce 8.0 不变；
- ⚠️ 施工时核验本机 `.env` 是否显式设了 `TRAVEL_WEATHER_TIMEOUT_S=6`（env 覆盖 default——.env 不入库，须现场确认，遗漏则"改了没生效"）。

---

## 六、t25 存量失败顺带修复（一行）

- 位置：`tests/travel/test_provider_layer.py:496` `TestHardening::test_t25_required_provider_semantics_frozen`。
- 根因（本轮实跑复现：`1 failed, 15 passed`）：qweather 备用源接入后 `provider_health()`（live/health.py:57）新增 `qweather_backup` 键，测试期望集合未更新——`Extra items in the left set: 'qweather_backup'`。
- 修法（一行）：期望集合 `{"tencent_lbs", "weather", "ticket"}` → `{"tencent_lbs", "weather", "ticket", "qweather_backup"}`。qweather_backup 15 用例本轮实测全绿，非本次引入（Phase 3 Completion Report 如实挂账项）。

---

## 七、实施顺序（Commit A/B/C）+ 风险

### 7.1 三 Commit（各自测试+提交，pathspec 双重限定）

| Commit | 内容 | 验收门 |
|---|---|---|
| **A Provider 层：Router + TTL + t25** | ① `live/router.py`：PROVIDER_CHAINS 四链声明 + `get_weather_provider()` 改链账装配（产物逐字节同构）+ ttl_for/timeout_for 薄读口；② FRESH_TTLS 三值调整 + 头部注释同步 + `TRAVEL_WEATHER_TIMEOUT_S` 默认 6→5（核验 .env）；③ t25 一行修复；④ Router 契约测试（链账完整性/装配同构 parity） | provider_layer 30 + providers 23 + qweather 15 全绿；一致性三门 + node_runtime parity 40 |
| **B 域层：Evidence 三落点 + validator 3 warning** | ① graph_state 声明 `evidences` 键（不预置/.get 纪律）+ 序列化守护；② poi/weather/risk_service 三处组装（纯函数派生）+ `fetch_forecast_evidence` 三层通道 + 4 处补丁目标机械更新；③ validation.py 三 code 常量 + validator_node 追加检查段；④ report 数据流（weather 节点写 evidences） | validator 32 + scenarios 23 + weather_expert + travel_graph + 金标 34 零回退 + user_decision/quality_metrics 分块 |
| **C 评测集 + 契约收口** | ① research freshness/coverage 评测集（v4 §10 Phase 4 注入项：候选池 LIVE/CACHE Evidence 占比、stale 占比；无源能力如实计 0）落 `evaluation/datasets/travel/research/`；② 边界契约测试追加：Router 不进 Agent/Node（静态扫描扩展）、evidences 键序列化断言、三 warning 级别断言；③ Phase 4 Completion Report（八项格式） | 全量 travel 分块 + 一致性四测试 + G4 台账登记（归位延后裁决/城市级 POI 源 BLOCKED/events·notice 契约位处置） |

### 7.2 风险表

| # | 风险 | 级 | 缓解 |
|---|---|---|---|
| R1 | confidence 分数漂移（新 warning 计分）影响 scenarios/user_decision 断言 | 中 | Commit B 单独成轮，先跑 validator+scenarios 分块再扩大；相对断言预期稳，绝对阈值断言逐例甄别 |
| R2 | fetch_forecast 通道改动破坏补丁缝（4 用例） | 中 | 裸名委托三层同步 + patch 目标机械更新（Phase 3 先例模式）；Commit B 内 weather 分块必跑 |
| R3 | route TTL 30min 的路况陈旧被当新数据 | 中 | v4 已拍板目标值；traffic_aware 字段语义自带；stale-if-error 只在失败路径的红线不动 |
| R4 | weather 超时 6→5s 慢网超时面变大 | 低 | fallback 链（qweather）+ stale 兜底已存在；DISABLED 不降级语义不变 |
| R5 | evidences 增大 checkpoint | 低 | ~12KB 级实测估算；摘要不存全文；旧 checkpoint 无键 `.get()` 兼容 |
| R6 | 方案 1 与 v4 §8 示例路径字面不一致 | 低 | 本报告 D1 显式裁决；军规"目录不冻结"背书；G4 登记 |
| R7 | 并行会话冲突（governance 批次仍在推进） | 中 | pathspec 双重限定；三 commit 各自可独立 revert |

---

## 八、待确认决策点（确认后才施工）

| # | 决策点 | 推荐 |
|---|---|---|
| **D1** | **归位裁决**：原位不动+Router 内聚（Router 落 live/router.py） vs git mv travel/providers/（冻结交易域 11 import 方接触+审计链漂移+全量回归代价） | **原位不动**；归位延 Phase 8 与 shim 删除同批重估，G4 登记 |
| **D2** | Router 形态：组装期链账装配（零行为变更） vs 调用期链执行器（重写 adapter 内 STOP J 冻结降级语义） | **组装期链账装配**；map/poi/price 先声明后接管 |
| **D3** | Evidence 存储：统一 state 键 `evidences`（dict fact_id→Evidence） vs 散点字段 | **统一 `evidences`**；天气 value 用摘要；candidates 用 Poi 字段纯函数派生 |
| **D4** | 天气通道：新增 `fetch_forecast_evidence` 三元组（4 处补丁目标机械更新） vs 改 `fetch_forecast` 返回元数（破坏缝） | **新增三元组函数**，缝语义保留 |
| **D5** | 新 warning 是否进 compute_confidence 扣分（-0.05/条） | **进**（与既有 warning 同权） |
| **D6** | v3 Phase 4 卡其余项取舍：① `travel.poi.detail` Tool 化（V1 已收敛 service，仅命名对齐）② live→runtime 更名 ③ events/notice 契约位文件 ④ 城市级 POI 源 BLOCKED 台账 | ① 做（薄封装，随 Commit B/C）；② 不做（随 D1 原位失效）；③ 只登记不建空壳文件；④ 登记（随 Commit C） |
| **D7** | TTL/超时确认：place 86400/route 1800/weather 600 + weather 超时 6→5（v3/v4 已拍板，本次确认无异议即执行）；施工时核验 .env 无覆盖 | 确认执行 |

---

## 附：本轮审计已核实事实清单（供施工对照）

1. import 方 46 个（47 命中−providers/__init__.py 纯文档提及），分布 16/12/6/12，全绝对路径，域外零引用；
2. `backend/providers/` 仅 travel 一域；`travel/providers` 命名空间空闲；
3. FallbackWeatherProvider（live/__init__.py:68-104）三语义：主败才降级/DISABLED 不降级/双败保留主因；qweather 15 用例实测全绿；
4. TTL 现状 place 600/route 120/weather 300/ticket 600 + commerce 七项 + NEGATIVE 60 + GRACE 600（cache.py:35-56）；超时 place 3/route 4/weather 6/ticket 3/commerce 8（resilience.py:52-58），weather 走 config TRAVEL_WEATHER_TIMEOUT_S 默认 6；
5. Evidence 契约与基线已冻结（core/contracts.py）；ProviderResult/Poi 字段可无损组装；**天气预报不落 state** 为 SOURCE_STALE 前置缺口；
6. validator 六轴+compute_confidence 扣分口径（errors -.15/decision -.10/warning -.05/seed -.20/no_date -.05/repair -.05）；结论映射不因 warning 翻转；
7. t25 实跑复现失败（health 键集缺 qweather_backup）；Phase 3 报告挂账一致；
8. Agent/Node 零 Provider 感知静态扫描已在位（test_agent_service_boundary 18 例），Router 触达面天然限于 service 层。
