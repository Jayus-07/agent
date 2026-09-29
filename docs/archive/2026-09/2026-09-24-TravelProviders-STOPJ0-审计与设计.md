# Travel Providers — STOP J0 审计与设计

> 日期：2026-09-24 ｜ 基线 HEAD：`912df8e`（STOP I 冻结 abed4ee 之后）
> 性质：**只读审计**（J0 零业务代码修改）+ STOP J1~J11 设计冻结
> 前置：STOP_H/I 全部 PASS；STOP I 回归基线 1087 passed / Q1-Q10 全达标

---

## 0. 总判断（STOP_J0_PASS=true）

Travel 域的 Provider 基础设施**比任务书预期的成熟**：外部调用全部收敛在
`infra/http/tencent_lbs.py` 单点（connect 3s/read 8s 超时、1 次重试、固定
backoff、三态熔断、5QPS 节流、SN 签名、状态码语义表、进程内 TTL+LRU 缓存），
`infra/lbs/api.py` 已做语义归一，`providers/travel/` 已有 POI/Transit Protocol
层。travel 域**零 HTTP 散点**。真正的缺口是六个「生产化」维度：

| # | 缺口 | 现状证据 | 归属 |
|---|------|---------|------|
| D1 | 无统一 ProviderResult：调用方拿原始 dict/None，「真没有」与「挂了」靠约定区分 | `safe_call→None`；lbs/api 用 `None vs []` 约定 | J1 |
| D2 | 缓存仅进程内：多 worker/多副本不共享（T22），无 negative cache（303/347 无结果反复打 Provider），无 stale-if-error | `tencent_lbs._cache` OrderedDict；`_LEG_CACHE` | J3 |
| D3 | `TRAVEL_WEATHER_TIMEOUT_S=6` **声明未接线**：实际预算=底层 (3+8s)×2 次+0.4s≈22.4s | grep 只有 config 与 docstring 引用 | J5 |
| D4 | JSON 解析失败会被重试一次（schema 无效属「再试也没用」类）；无坐标范围校验（lat=999 可穿透到 Poi float） | `call_sync` except TencentLbsError 统一重试；`resolve_place` 只查 `if not lat` | J2/J4 |
| D5 | 无 quota 日预算治理：`is_quota` 能检测 120-123 但没有预算计数与软停 | 无 `TRAVEL_PROVIDER_*_BUDGET` 配置 | J7 |
| D6 | 无 provider 级指标/health 组件：travel_provider_* 零存在 | metrics.py 无；/health 无 travel_providers 段 | J9/§94/§98 |

**Ticket/Opening（J0-6）**：腾讯 WebService place search 归一产物字段为
`id/name/address/category/tel/lat/lng/distance_m/province/city/district/adcode`
——**无营业时间、无票价字段**（实测归一层 + 官方端点字段）。决策：**B（继续
unknown/unverified），不硬凑**。J1 冻结 TicketProvider 契约 + 能力注册表，
真实适配器不接（无可靠来源）；T11/T12 用 fake provider 验证
「ticket=0+verified ≠ None+unknown」语义。

**Hotel/Flight（J0-7）**：全项目无 hotel/flight planning、schema、validator 消费链
（grep 零命中）→ 冻结：

```text
HOTEL_PROVIDER_SCOPE=OUT
FLIGHT_PROVIDER_SCOPE=OUT
```

（与 BookingProvider 纯预留同口径——契约形状已在 `providers/travel/booking.py`，
STOP K Commerce & Inventory 再评估。）

---

## 1. J0-1 Provider Inventory（真实 HEAD）

| # | Provider | 位置 | 能力 | 状态 | required |
|---|----------|------|------|------|----------|
| 1 | Tencent LBS place | `infra/lbs/api.py::place_search/suggestion` → `infra/http/tencent_lbs.py` | maps.place_search（must_go 缺失补全） | live（`TRAVEL_USE_LIVE_MAP`+Key 双闸） | false |
| 2 | Tencent route | `api.direction/distance_matrix` | maps.route（driving/walking+路况+taxi_fare） | live（同上） | false |
| 3 | Tencent weather | `api.weather/weather_for_city` | weather.forecast（now/future/hours） | live（`TRAVEL_WEATHER_ENABLED`） | false |
| 4 | Tencent district | `api.district_search/resolve_district` | 行政区 adcode/中心点（weather 前置） | live | false |
| 5 | RAG travel KB | `tools/travel/knowledge.py`（pgvector） | risk 知识摘录 | live（`TRAVEL_RAG_ENABLED`） | false |
| 6 | Seed POI | `tools/travel/poi_seed.py`（31 条） | canonical 候选基座 | 静态（verified） | true（缺它无候选） |
| 7 | Local estimate | `tools/travel/routing.py` + `LocalEstimateProvider` | Haversine 估算兜底 | 纯函数（恒可用） | true（兜底） |
| 8 | Booking | `providers/travel/booking.py` | 预订状态机/幂等键契约 | **纯预留（不接线）** | — |
| 9 | Hotel/Flight | — | 无消费链 | **OUT（STOP K）** | — |

## 2. J0-2 真实调用链（逐 provider）

```
transit expert → routing.estimate_leg（插槽）→ TencentTransitProvider.estimate
    → live_map.live_leg → lbs.api.direction → tencent_lbs.call_sync → httpx.Client(单例)
poi expert → live_map.resolve_missing_places → resolve_place
    → lbs.api.place_search → tencent_lbs.call_sync → 同一 httpx 单例
weather expert → lbs.api.weather_for_city → resolve_district + weather → 同上
risk expert  → tools.travel.knowledge → pgvector 检索（非第三方 HTTP）
```

九问九答（只认代码）：
- **谁创建 client**：`tencent_lbs._get_sync_client()` 进程级单例（max_conn=10/keepalive=5）
- **谁管 timeout**：`httpx.Timeout(connect=MAP.TENCENT_LBS_CONNECT_TIMEOUT=3, read=8)`
- **谁 retry**：`call_sync` 重试 1 次（`TENCENT_LBS_RETRIES=1`），fatal 码（110/111/112/113/190/199/301/311）豁免
- **谁 cache**：`tencent_lbs._cache`（进程内 TTL+LRU，maxsize 1024；默认 TTL 600s/route 120s/weather 300s/forecast 1800s/district 86400s）+ `live_map._LEG_CACHE`（900s，坐标 4 位小数）
- **谁计 quota**：无人——`is_quota`（120/121/122/123）仅用于异常消息
- **谁处理 429**（腾讯语义=121/122）：归入可重试 → 重试 1 次 → raise → `safe_call` 吞掉返 None
- **谁处理 5xx**：腾讯 HTTP 恒 200；非 200/非 JSON → `TencentLbsError(status=0)` → 重试 1 次
- **谁做 schema normalization**：`lbs/api.py`（`_normalize_poi`/`direction`/`weather` 归一层）
- **谁做 fallback**：两层——`safe_call`（吞异常返 None）+ 调用方本地估算（`TencentTransitProvider` 远期强制本地、`estimate_leg` 网络失败回落；`resolve_missing_places` 失败即 unresolved）

## 3. J0-3 HTTP 散点审计

`grep httpx|requests.get|AsyncClient` 于 `backend/travel/ backend/tools/travel/
backend/providers/travel backend/infra/lbs` → **零散点**。全部外部 HTTP 经
`infra/http/tencent_lbs.py`。任务书 G2 已满足，本轮维持并加契约测试锁死。

## 4. J0-4 腾讯 LBS 字段实测

place search 归一产物（`_normalize_poi`，实测字段）：

```text
id(腾讯 POI id) / name(title) / address / category(形如"旅游景点:国家级景点")
/ tel / lat / lng / distance_m(仅周边检索) / province / city / district / adcode
```

**没有** opening_hours、ticket、price、rating、popularity。→ 票价/营业时间的
「占位 + unverified」不是实现偷懒，是数据源能力边界（J6 决策依据）。

## 5. J0-5 Weather 审计

- provider/endpoint：腾讯 `/ws/weather/v1/`（type=now/future/hours）
- 归一：`api.weather` → `{kind, province, city, district, adcode, update_time, days[{date, day{weather,temperature,...}, night{...}}]}`
- cache：now=300s / future=1800s（`TENCENT_LBS_WEATHER_TTL/FORECAST_TTL`）
- failure：`safe_call` → None → expert 跳过 + 披露「天气数据暂不可用」
- 模式：**live + cached + 软降级**（无 mock/estimate 分支——不做假天气）
- horizon：expert 用「预报日期 ∩ 行程日期」求交，超出预报窗口的日期自然无匹配
  （**当前无 OUT_OF_HORIZON 显式状态**，行为正确但不可观测 → J5 补）
- **缺口 D3**：`TRAVEL_WEATHER_TIMEOUT_S=6` 未接线（实际最坏 ≈22.4s）

## 6. J0-8 缓存现状明细

| 缓存 | 位置 | key | TTL | negative | stale | 共享 |
|------|------|-----|-----|----------|-------|------|
| LBS 通用 | `tencent_lbs._cache` | path+全部参数（含 key/sig 剔除） | 按端点 600/120/300/1800/86400 | ❌（303/347 不缓存） | ❌ | ❌ 进程内 |
| 路段 | `live_map._LEG_CACHE` | 4 位小数坐标四元组 | 900s | ❌ | ❌ | ❌ 进程内 |
| 共享缓存基建 | `infra/cache/backend.py::get_cache` | 命名空间 | 自定义 | — | — | **TwoTierCache（L1+Redis）✅ 已有** |

结论：**共享缓存不新建**——复用 `get_cache("travel_provider", ttl)`。

## 7. J0-9 timeout/retry 实测配置

```text
connect=3s  read=8s（write/pool=connect）  retry=1  backoff=0.4s（固定）
throttle=0.2s（≈5QPS，与个人 Key 对齐）
breaker: 3 次连续失败开路 / 60s cooldown / HALF_OPEN 试探（CIRCUIT_BREAKER_SHARED_ENABLED 可选 Redis 共享）
weather 预算：6s（声明）→ 实际未强制
单次排程 legs：并发预热硬预算 4s（_PREFETCH_BUDGET_S）+ 串行兜底逐段
```

延迟灾难防护已被 STOP H 验证（熔断把 6 分钟实测事故压回秒级）；本轮不加重试次数、
不放大超时，只做**分类修正**（JSON 失效不重试）与 **weather 预算接线**。

## 8. J0-10 Provider Failure Matrix（现状）

| Failure | Current Behavior |
|---------|------------------|
| timeout | httpx 超时 → 重试 1 次 → `TencentLbsError(status=0)` → safe_call None → 本地估算/unresolved |
| DNS/连接拒绝 | 同上（网络错误 status=0） |
| 429（121/122/123/120） | 重试 1 次（无差别）→ raise → None 降级；**无日预算软停** |
| 401/403（110/111/112/113/190/199） | fatal 不重试 → 直接降级 |
| 5xx（非 200/非 JSON） | `TencentLbsError(status=0)` → **重试 1 次（分类瑕疵）** → 降级 |
| malformed JSON | 同 5xx 行为 |
| empty result（303/347 无结果） | raise（**不缓存 → 反复打 Provider**）→ 调用方视作未解析 |
| invalid coordinates（lat=999） | **无范围校验**，穿透为 float 入 Poi |
| quota exceeded（120） | 仅异常消息；无预算计数/软停/观测 |
| partial response | 字段级 `_to_float/_to_int` 宽松默认；路线缺 distance → live_leg 返 None 降级 |

---

## 9. 冻结：目标架构（STOP J1~J11）

```
Travel experts（冻结面不动：poi/transit/weather/risk/validator/repair/reporter）
   ↓ 仅消费 Contract
providers/travel/live/                      ← 新增 Provider Layer（本轮主体）
   ├─ result.py        ProviderResult[T] + ProviderStatus + Freshness
   ├─ contracts.py     PlaceProvider / RoutingProvider / WeatherProvider /
   │                   TicketProvider（Protocol）+ ProviderCapabilities 注册表
   ├─ errors.py        ProviderTimeout/Unavailable/RateLimited/Unauthorized/
   │                   InvalidResponse/NotFound（对齐 TencentLbsError 映射）
   ├─ cache.py         共享缓存（get_cache 复用）+ negative cache + stale-if-error
   │                   + 坐标/参数归一化 + freshness 语义
   ├─ resilience.py    timeout budget（per-operation）+ retry 分类 + single-flight
   ├─ quota.py         日预算（Redis INCR 跨进程）+ 软停 + 观测
   ├─ tencent.py       Tencent 适配器（Place/Routing/Weather/Ticket×4 契约实现，
   │                   内部走既有 lbs 门面 —— 不动 infra 层其他消费方）
   ├─ health.py        travel_providers health 组件
   └─ telemetry.py     travel_provider_* 指标 + trace + 结构化事件
   ↓
External APIs（腾讯 LBS —— 现有 httpx 客户端原样复用）
```

设计红线：
- **业务层签名零变更**：`POIProvider`/`TransitProvider` 既有 Protocol（STOP I 冻结）
  保持；新 Result 容器与共享缓存在 `providers/travel/live/` 内部包裹，experts
  继续拿 `Poi/dict`。
- **不为换而换**：lbs/api.py 归一层已是「SDK 隔离层」，Tencent 适配器复用它；
  tencent_lbs.py 客户端（超时/熔断/节流）原样复用——J2 只在 Provider 层补分类
  与预算，不改共享基础设施的行为。
- **Ticket Provider**：契约 + fake adapter 测试；无真实适配器（J0-6 决策 B）。
- Hotel/Flight OUT：能力注册表登记 `hotel.search/flight.search` 为 not_implemented。

## 10. 阶段计划

| 阶段 | 交付 |
|------|------|
| J1 | result.py（ProviderResult/Status/Freshness）+ contracts.py（4 Protocol + capabilities）+ errors.py |
| J2 | resilience.py：per-operation timeout budget（实测冻结：place 3s/route 4s/weather 6s（接线既有配置）/ticket 3s）+ 重试分类修正（INVALID_RESPONSE 不重试）+ single-flight |
| J3 | cache.py：Redis 共享缓存（TTL 分数据：place 600s/route 120s/weather 300s）+ NOT_FOUND 短 negative cache（60s）+ stale-if-error + key 归一化（坐标 4 位小数） |
| J4 | tencent.py Place/Routing 适配器 + POI 校验（city 一致/坐标 GCJ-02 范围 LAT 3.5-53/LNG 73.5-135/空名/无 id）+ canonical merge 优先级（verified>fresher>placeholder，字段级） |
| J5 | Weather 适配器 + 6s 预算接线 + OUT_OF_HORIZON 显式状态 + 降级披露保持 |
| J6 | Ticket 契约 + fake adapter（unknown ≠ free 语义测试） |
| J7 | quota.py：TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET（默认 0=不限）+ 软停→cache/estimate + 指标 |
| J8 | fallback matrix 冻结（§11）+ 错误分类学对齐 |
| J9 | telemetry.py（7 指标+trace+事件）+ health 组件 + Q1-Q10 回归 + PQ1-PQ8 |
| J10 | tests T1-T25 |
| J11 | 真实网关 E2E J-E2E-1~6 + 冻结报告 |

## 11. Fallback Matrix（预冻结，J8 按实现复核）

| Capability | Primary | Fallback 1 | Fallback 2 | 终态 |
|-----------|---------|-----------|-----------|------|
| place resolve | Tencent place_search（共享缓存优先） | 同进程 LBS 缓存 | unresolved（如实披露） | **禁止伪造 POI** |
| route | Tencent direction（缓存优先） | stale-if-error 缓存 | Haversine 估算（is_estimate=true） | **禁止伪装实时** |
| weather | Tencent weather（缓存优先） | stale 缓存 | unavailable 披露（行程照常） | **禁止伪装晴天** |
| ticket/opening | TicketProvider（本轮无真实适配器） | — | unknown/unverified（seed 语义） | **禁止 ticket=0 充当 unknown** |
| quota 软停后 | — | 全部走 cache/estimate/seed | — | 可观测（quota_exhausted 事件） |

## 12. 安全红线（§99-§102 现状确认 + 维持）

- Key 只在 env（`TENCENT_LBS_KEY/SK`），代码零硬编码；URI API 前端 Key 分离已实现
- URL 白名单：host 固定 `TENCENT_LBS_HOST`，业务层不传任意 URL
- 用户 POI 名进 query param 均经 httpx params encode + 长度上限（page_size/keyword
  上游自然受限，J4 补显式 keyword 长度钳制 ≤50 字符）
- 原始响应不进普通日志（现状即如此；debug 级仅键名）
