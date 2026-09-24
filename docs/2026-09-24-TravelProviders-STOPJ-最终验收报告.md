# Travel Providers — STOP J 最终验收报告

> 日期：2026-09-24 ｜ 基线：STOP I 冻结（abed4ee）→ 收口 HEAD：`6a2b392`
> 审计与设计：`docs/2026-09-24-TravelProviders-STOPJ0-审计与设计.md`

---

## 1. Verdict

```text
STOP_J_PASS=true
TRAVEL_LIVE_PROVIDER_PRODUCTION_CLOSURE_PASS=true
TRAVEL_PROVIDER_LAYER_FROZEN=true
```

---

## 2. Before Architecture

STOP I 后：外部调用已收敛单点（`infra/http/tencent_lbs.py`：超时/熔断/节流/
SN 签名/进程内缓存），`infra/lbs/api.py` 已归一，但缺六个生产维度——统一
ProviderResult（"真没有" vs "挂了"）、跨 worker 共享缓存、weather 6s 预算
接线（声明未实现）、schema 校验（lat=999 可穿透）、quota 日预算软停、
provider 指标与健康组件。

## 3. Provider Architecture

```
Travel experts（冻结面零改动：签名与产物不变）
   ↓
providers/travel/live/          ← 本轮新增 Provider Layer
   result.py      ProviderResult[T] + ProviderStatus(七态) + Freshness(live/cached/stale)
   contracts.py   PlaceProvider / RoutingProvider / WeatherProvider / TicketProvider(Protocol)
   capabilities.py 能力账：place_resolve/route/weather=已实现(tencent:lbs)；
                  ticket.facts/hotel.search/flight.search=not_implemented
   errors.py      错误分类学（TencentLbsError/CircuitBreaker → 七态，单一映射点）
   resilience.py  timeout budget(place 3s/route 4s/weather=TRAVEL_WEATHER_TIMEOUT_S 6s/ticket 3s)
                  + single-flight + 3.10 FutureTimeout 归一
   cache.py       共享缓存（复用 infra/cache get_cache→TwoTierCache/Redis）：
                  分数据 TTL(place600/route120/weather300) + NOT_FOUND 短负缓存(60s)
                  + stale-if-error（仅失败路径，fresh TTL+600s grace）
   quota.py       日软预算 TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET（0=不限），
                  Redis 跨进程计数、先查后增（被拒调用不耗计数）
   tencent.py     腾讯适配器（复用 lbs 门面；POI 校验/坐标 GCJ-02 范围/keyword≤50）
   health.py      travel_providers 健康组件
   telemetry.py   7 个低基数指标 + trace 字段 + 结构化事件
   ↓
infra/http/tencent_lbs.py（既有：J2 仅补「JSON 失效不重试」status=-1 分类）
   ↓
腾讯位置服务（真实外部 API）
```

业务层依赖 Contract，Provider 层依赖 SDK/API；腾讯原始 JSON 不越过适配器。

## 4. Provider Inventory

| provider | capability | status | required |
|----------|-----------|--------|----------|
| tencent:lbs | maps.place_resolve | live（TRAVEL_USE_LIVE_MAP+Key 双闸） | false |
| tencent:lbs | maps.route | live | false |
| tencent:lbs | weather.forecast | live（TRAVEL_WEATHER_ENABLED） | false |
| — | ticket.facts | 契约冻结，无真实适配器（J0-6 决策 B：腾讯无该字段，不硬凑） | false |
| — | hotel.search / flight.search | **OUT**（HOTEL/FLIGHT_PROVIDER_SCOPE=OUT，无消费链 → STOP K） | false |
| seed:local | canonical 候选 | 静态 verified 基座 | true（缺它无候选） |
| estimate:local | Haversine 兜底 | 纯函数恒可用 | true（兜底） |

## 5. Contract（归一化 schema）

`PlaceRecord`(provider_id/name/category/address/city/district/adcode/lat/lng/tel)、
`RouteRecord`(distance_m/duration_min/mode/taxi_fare_cny/traffic_aware)、
`WeatherForecast`(city/days[date/condition/min/max]/horizon_days)、
`TicketFacts`(ticket_price_cny=None=unknown｜0+verified=明确免费, opening_time,
closed_weekdays, reservation_required, verified)。
ProviderResult 统一携带 provider/operation/status/source_id/observed_at/
freshness/latency_ms/error。

## 6. Failure Taxonomy

`ProviderTimeout / ProviderUnavailable / ProviderRateLimited /
ProviderUnauthorized / ProviderInvalidResponse / ProviderNotFound` + DISABLED。
映射：LBS fatal 码→UNAUTHORIZED；quota 码→RATE_LIMITED；303/347→NOT_FOUND；
JSON 失效（status=-1，本轮新增哨兵且**不重试**）→INVALID_RESPONSE；
熔断开路→UNAVAILABLE。

## 7. Timeout / Retry（真实配置）

- budget：place 3s / route 4s / weather 6s（=TRAVEL_WEATHER_TIMEOUT_S，本轮接线生效）/ ticket 3s
- 底层 httpx：connect 3s / read 8s；网络重试 1 次 + 0.4s backoff；fatal 与
  schema 失效不重试；5QPS 节流；熔断 3 次/60s/HALF_OPEN
- Provider timeout < Travel 整体预算；单次排程 legs 预热仍有 4s 硬预算

## 8. Cache

| 项 | 实现 |
|----|------|
| 后端 | `get_cache("travel_provider")` → Redis(TwoTierCache)/进程内降级 |
| key | operation + 参数（坐标 4 位小数归一；place 含 name+city；route 含起终点+mode） |
| TTL | place 600 / route 120 / weather 300（fresh）+ 600s stale grace |
| negative | 仅 NOT_FOUND，60s，物理=语义；TIMEOUT/429/5xx 绝不入缓存 |
| stale-if-error | 仅 Provider 失败路径读取过期条目，observed_at 如实保留 |

## 9. Provider Integration

- **Place**：poi 专家 must_go 缺失解析切至 Provider 通道（`resolve_missing_places`
  同签名同语义；Poi 构造与 unverified 标注仍由 live_map 唯一出口）；输出校验
  （空名/无 id/坐标越界→INVALID_RESPONSE 拒绝）；canonical merge：Tencent 只
  补种子缺失（名称模糊命中已有池则跳过），字段级 verified>fresher>placeholder
  以 TicketProvider 契约预留（无真实适配器，本轮无冲突场景）
- **Route**：TencentTransitProvider 接共享路由缓存（跨 worker）；live 失败→
  stale→Haversine（is_estimate=true 语义不变）
- **Weather**：经 Provider（6s 预算/缓存/分类降级）；**OUT_OF_HORIZON 显式
  披露**（预报窗口与行程日期零交集→「超出天气预报的可信范围…临近出发可重新
  评估」），绝不拿今天天气伪装远期；不可用时行程照常出单+如实披露
- **Ticket**：契约+fake adapter 测试；unknown=None ≠ 免费=0+verified（§47/§48）

## 10. Fallback Matrix（冻结）

| Capability | Primary | Fallback 1 | Fallback 2 | 终态 |
|-----------|---------|-----------|-----------|------|
| place | 腾讯 place_search（缓存优先） | stale 缓存 | unresolved 披露 | 禁止伪造 POI |
| route | 腾讯 direction（缓存优先） | stale 缓存 | Haversine（is_estimate=true） | 禁止伪装实时 |
| weather | 腾讯 weather（缓存优先） | stale 缓存 | unavailable 披露（行程照常） | 禁止伪装晴天 |
| ticket | TicketProvider（无真实适配器） | — | unknown/unverified | 禁止 0 充当 unknown |
| quota 软停 | — | 全部走缓存/估算/种子 | — | quota_exhausted 事件 |

## 11. Freshness

live（本次调用）/ cached（共享缓存命中，observed_at=写入时点）/ stale（仅
降级路径）/ estimated（TransitLeg.is_estimate）/ unverified（Poi.verification_status）
——五态可区分并沿既有链路到达行程单。

## 12. Quota / Cost

`TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET`（默认 0=不限）；Redis 跨进程计数
（键带日期，25h 过期）；**先查后增**——被拒调用不消耗计数；超预算→立即
转缓存/估算/种子 + `travel_provider_quota_total` 指标 + `quota_exhausted`
事件。免费额度不伪造成本（仅记 request_count）。

## 13. Quality Regression（G17）

| 指标 | STOP I 基线 | STOP J 实测（质量门禁，离线纪律） | 判定 |
|------|------------|-----------------------------------|------|
| Q1 valid_poi_rate | 1.0 | **1.0000** | ✅ |
| Q2 must_go_coverage | 1.0 | **1.0000** | ✅ |
| Q3 avoid_violation_rate | 0 | **0.0000** | ✅ |
| Q4 duplicate_rate | 0 | **0.0000** | ✅ |
| Q5 day_count_accuracy | 1.0 | **1.0000** | ✅ |
| Q6 单日在途峰值 | ≤150 | **70** | ✅ |
| Q7 单日负载峰值 | ≤780 | **537** | ✅ |
| Q8 budget_silent_over | 0 | **0** | ✅ |
| Q9 hard_constraint_pass | ≥0.98 | **1.0000** | ✅ |
| Q10 unsupported_fact_rate | 0 | **0.0000** | ✅ |

联网模式全量金标（`python -m backend.evaluation travel`）：**33/34 通过 +
1 skip（T-G10 声明 offline-only）+ 0 失败**。T-G10 语义说明：离线确定性环境
「未知必去→unresolved」成立；联网模式腾讯会模糊命中**真实**地点（非伪造），
Live≠Better——该用例如实标注 `requires_offline`，不计失败。
本轮另修：runner 层日期锚动态归一（E/G 组「9月21/28日」→动态周一，修复
日期漂移导致的闭馆场景失效——该缺陷在 STOP I 的 CLI 全量跑中同样存在，
本轮一并关闭）；确定性测试剥离时钟字段。

## 14. Provider Metrics（PQ1-PQ8）

`python -m backend.evaluation travel-provider`：**8/8 通过（100%）**——探针
覆盖 place success/not_found/timeout/invalid、route success/timeout、quota
soft-stop、cache hit(freshness=cached)。PQ 口径由探针+指标共同承载：
success_rate（PQ1）/cache_hit（PQ2）/fallback（PQ3）/timeout（PQ4）/
invalid_response（PQ6）/not_found（PQ7）/route estimate fallback（PQ8）均为
`travel_provider_*` 指标或探针断言；PQ5 延迟由
`travel_provider_latency_seconds` 直方图承载（buckets 至 10s，可算 P50/P95）。

实机 /metrics（真实链路后）：`travel_candidate_total=91`、
`travel_itinerary_validation_total{status=pass}=9`；`/health` 新组件
`travel_providers: {tencent_lbs: healthy, weather: healthy, ticket: disabled}`。

## 15. Runtime E2E（真实 APISIX+JWT+SSE+真实腾讯 API）

`backend/scripts/e2e_travel_providers.py`（app 镜像按本轮代码重建）：

```text
Phase A（.env 正常）：
  J-E2E-1 live place resolution  PASS  必去「狐尾山公园」（种子外）真实解析并排入
  J-E2E-2 live route             PASS  Redis 共享路由缓存 3 条（真实路线证据）
  J-E2E-3 weather                PASS  预报进共享缓存（10月3日在视野内）
  J-E2E-5 cached response        PASS  同 brief 重放：配额计数 6→6 零增量（全被缓存吸收）
Phase B（TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET=1 临时注入）：
  J-E2E-4/6 provider unavailable PASS  行程照常出单 +「地点数据服务暂时不可用」
                                       如实披露；配额 18→18 零消耗（先查后增）
STOP_J_PROVIDER_E2E_PASS=true
```

共享栈纪律：只重建 app；postgres/redis/APISIX 未动；未 down -v；.env 临时
改动已还原并重建恢复；测试写入共享 Redis 的产物已清理（12 键）。

## 16. Health / Observability

- health：`/health.travel_providers`（healthy/degraded/disabled；required 全
  false，非关键降级不拖垮整体）
- metrics：`travel_provider_requests_total{provider,operation,status}` /
  `travel_provider_latency_seconds` / `travel_provider_errors_total` /
  `travel_provider_cache_total{cache_status}` / `travel_provider_fallback_total`
  / `travel_provider_stale_total` / `travel_provider_quota_total`——标签仅
  provider/operation/status/cache_status/fallback（§95 低基数）
- trace：span 内 provider.name/operation/status/cache_status/fallback/
  duration_ms（无 secret/完整 query/完整原始响应）
- 事件：`travel.provider.request/success/timeout/rate_limited/invalid_response/
  fallback/circuit_open(熔断语义复用既有观测)/quota_exhausted`

## 17. Files Changed

| 文件 | 变更 |
|------|------|
| docs/2026-09-24-TravelProviders-STOPJ0-审计与设计.md | 新增（J0 审计） |
| backend/providers/travel/live/{__init__,result,errors,contracts,capabilities,resilience,cache,quota,telemetry,tencent,health}.py | 新增 Provider Layer（11 文件） |
| backend/providers/travel/transit.py | 共享路由缓存 + stale-if-error |
| backend/travel/experts/poi.py | must_go 解析切 Provider 通道 |
| backend/travel/experts/weather.py | Provider 化 + 6s 预算 + OUT_OF_HORIZON 披露 |
| backend/infra/http/tencent_lbs.py | JSON 失效 status=-1 哨兵（不重试） |
| backend/app/api/routes/health.py | travel_providers 组件 |
| backend/evaluation/{cli.py,models.py,runners/builtin.py} | travel-provider 模块接入 |
| backend/evaluation/runners/travel_provider.py | 新增探针 runner（缓存/quota 双隔离） |
| backend/evaluation/datasets/travel-provider/ | 探针数据集（8 条） |
| backend/evaluation/runners/travel.py | 日期锚动态归一 + offline-only skip |
| backend/evaluation/datasets/travel/cases.jsonl | G10 声明 requires_offline |
| backend/tests/travel/{conftest,test_provider_layer,test_provider*}.py | 缓存隔离 fixture + T1-T25 矩阵（30 测） |
| backend/tests/travel/{test_weather_expert,test_scenarios,test_travel_graph}.py | 新契约增量修订 |
| backend/scripts/e2e_travel_providers.py | 新增 J-E2E 驱动 |

## 18. Tests

| command | passed | failed | exit |
|---------|-------:|-------:|-----:|
| `pytest tests/travel/ tests/orchestration/ tests/test_stop_g4_matrix.py -q --no-cov` | **1117** | **0** | **0** |
| `pytest tests/travel/test_provider_layer.py -q --no-cov` | 30 | 0 | 0 |
| `pytest tests/travel/test_quality_golden.py tests/travel/test_travel_dataset.py -q --no-cov` | 21 | 0 | 0 |
| `python -m backend.evaluation travel`（联网全量） | 33 过+1 skip | 0 | 0 |
| `python -m backend.evaluation travel-provider` | 8/8 | 0 | 0 |

（首轮回归 1 例失败 = `test_plan_is_deterministic` 整字典比较含 created_at
时钟字段，接真实路线后跨秒必假失败——按测试意图修订为剥离时钟字段后全绿。）

## 19. Commits

| hash | subject | scope |
|------|---------|-------|
| 4fa1af0 | docs(travel): STOP J0 审计与设计 | J0 |
| 2c04d3d | feat(travel): STOP J1-J8 provider layer | J1-J8 |
| c252d59 | test(travel): STOP J9-J11 provider evaluation+E2E | J9-J11 |
| 6a2b392 | fix(travel): STOP J9 质量回归口径 | J9 |

## 20. Parallel Workspace Protection

- 开工记录他人 WIP（tasks/context_budget/customer_service 等，过程中新增
  memory/domain-runtime 提交穿插主干）；未触碰、未收编；全程双重 pathspec。
- `scripts/init_db.py` 的他人改动未纳入任何本轮提交。
- 共享栈：app 重建前预检（工作区全量 py_compile+import）；仅重建 app；
  .env 临时注入（软预算）已还原；测试产物（探针缓存 12 键、配额计数 2 键）
  已清理；无 accidental staging。

## 21. Deferred（全部非阻塞）

1. **Ticket/Opening 真实 Provider**（J0-6 决策 B）：腾讯 WebService 无该字段；
   契约已冻结，待 STOP K 评估票务类供给。
2. **Hotel/Flight**：HOTEL/FLIGHT_PROVIDER_SCOPE=OUT → STOP K Commerce &
   Inventory。
3. **第二 Place Provider**（高德/Google）：仅可作新 Adapter 接入（冻结边界），
   无现网诉求前不接。
4. **跨副本 single-flight**（Redis 锁级去重）：当前进程内 single-flight +
   Redis 共享缓存已吸收主要重复；分布式显式互斥留待有真实多副本压力时评估。
5. **quota 计数的 GET→INCR 微竞态**：软预算语义下可接受（最坏多放行个位数，
   有熔断与第三方硬限兜底），已在代码注释登记。

---

## 22. 最终 Gate（§110 逐条）

| Gate | 判定 | 证据 |
|------|------|------|
| G1 Provider Contract 统一 | ✅ | §3 四 Protocol + ProviderResult |
| G2 业务层不依赖外部原始 schema | ✅ | experts 只消费归一产物；散点审计零命中 |
| G3 timeout 全部有界 | ✅ | §7 budget 表 + call_with_budget |
| G4 retry 分类正确且有界 | ✅ | 网络重试 1 次；fatal/schema 失效不重试 |
| G5 Provider failure 不导致 Travel 主链 500 | ✅ | J-E2E-4/6 软停照常出单；T3/T6/T10 |
| G6 Redis/shared cache 生效 | ✅ | J-E2E-2/3 缓存键证据；J-E2E-5 零增量 |
| G7 cache key 正确 | ✅ | operation+归一化参数（坐标 4 位小数/mode/城市） |
| G8 negative cache 不混淆 timeout 与 not-found | ✅ | 仅 NOT_FOUND 入缓存；T15 |
| G9 stale-if-error 语义明确 | ✅ | 仅失败路径；observed_at 如实；T16 |
| G10 live/cached/stale/estimate/unverified 可区分 | ✅ | Freshness + 既有事实字段 |
| G11 POI 输入通过 schema/city/coordinate 校验 | ✅ | T4/坐标范围/keyword 钳制 |
| G12 route live→estimate fallback 可用 | ✅ | T6 + estimate_leg 语义不变 |
| G13 weather horizon 正确 | ✅ | OUT_OF_HORIZON 显式披露；T9 |
| G14 ticket unknown 不伪造免费 | ✅ | T11/T12 |
| G15 Provider quota 可观测 | ✅ | 软停指标+事件；J-E2E-4；T19 |
| G16 circuit/failure fallback 生效 | ✅ | 既有三态熔断接入分类学；T17/T18 |
| G17 Q1-Q10 无回归 | ✅ | §13 全表 |
| G18 provider metrics 低基数 | ✅ | §16 标签白名单 + 守护测试 |
| G19 secrets 零泄漏 | ✅ | Key 仅 env；URL 白名单；原始响应不入日志 |
| G20 真实 external-provider E2E 通过 | ✅ | §15 真实腾讯链路 Phase A/B |
| G21 full regression=0 failed | ✅ | 1117/0/exit=0 |
| G22 parallel workspace clean | ✅ | §20 |

---

## 23. 冻结边界

```text
TRAVEL_PROVIDER_LAYER_FROZEN=true
```

冻结：Provider Contracts（四 Protocol + 归一化模型）、ProviderResult 七态与
Freshness、错误分类学映射、Timeout/Retry 政策、Cache/Freshness 契约
（TTL/negative/stale 语义）、Fallback Matrix、Quota/Cost 指标、Provider
Health 模型。未来新增 Google Maps/高德/Amadeus/票务等，只能作为新
Provider Adapter 接入，不得再侵入 Planner/Validator/Reporter/Travel Graph。

下一阶段：STOP K — Travel Commerce & Inventory（Hotel/Flight Inventory、
Availability、Price Snapshot、Booking Deep Link 等）。
