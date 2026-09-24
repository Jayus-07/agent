# Travel Commerce & Inventory — STOP K0 审计与设计

> 日期：2026-09-24 ｜ 基线：STOP J 冻结（`09b7c65`，TRAVEL_PROVIDER_LAYER_FROZEN=true）
> 前序冻结：STOP I（TRAVEL_ITINERARY_QUALITY_FROZEN=true）／STOP H（运行时）／STOP G（分布式上下文）
> 本文是 STOP K 的唯一设计事实源；实现（K1-K10）不得偏离本文，偏离必须先改本文。

---

## 1. Existing Architecture（现状调用链）

```text
HTTP /chat/stream（APISIX :9080 唯一入口，JWT → X-User-Id）
    ↓
GraphRunner（主图 8 核心节点，builder.py）
    ↓ router_node
    ├─ domain_hint=cs 锁域 / cs_prefilter ──────────→ cs_graph_node → END
    ├─ travel_prefilter（正则，TRAVEL_ENABLED）──────→ travel_graph_node → END
    │     travel_graph_node（orchestration 适配器：state 转换 + invoke）
    │        ↓
    │     travel 域图（graph_builder.py，10 节点固定拓扑）
    │        slot_filler → supervisor → poi/transit/weather/budget/risk
    │        → validator →(repair)→ reporter（final_answer）
    │        ↓
    │     providers/travel/（STOP I 业务消费面：POIProvider/TransitProvider）
    │        ↓
    │     providers/travel/live/（STOP J Provider Layer，冻结）
    │        result 七态 / contracts / capabilities 能力账 / errors 分类学
    │        resilience(timeout budget + single-flight) / cache(Redis 共享)
    │        quota(日软预算) / telemetry(低基数指标) / health / tencent 适配器
    │        ↓
    │     infra/http/tencent_lbs.py（超时/熔断/节流/签名）→ 腾讯位置服务
    ├─ selection_funnel_prefilter ──────────────────→ 选品漏斗域图 → END
    └─ 三层 Router → direct / workflow / plan
```

**Commerce 应插在哪一层——三选一判定（任务书 K0-A）：**

| 方案 | 判定 | 理由 |
|------|------|------|
| Travel 主图内 extension（加节点/改 reporter） | ❌ 排除 | STOP J 冻结边界明文：「新增 Provider 只能作为新 Provider Adapter 接入，不得再侵入 Planner/Validator/Reporter/Travel Graph」。且 `travel_graph_node.py` 现混有并行会话未提交改动（checkpoint 租户 namespace），本轮不可提交 |
| post-plan enrichment（行程+商务建议） | ⏸ Deferred | 场景成立（§18）但依赖行程图状态出口，同样触碰冻结面；且本轮无真实库存数据源，enrichment 无生产价值。留待真实 Provider 落地后专项 |
| **独立 Commerce SubGraph（域图自注册）** | ✅ **采用** | 仓库正式扩展点：`backend/domains/__init__.py` 加一行 import → `domain_graph_registry.register` → builder 自动布线（router 条件边 + 直连 END），**零冻结文件触碰**。选品漏斗域（2026-09-17）已验证该路径。天然满足 §18「纯商务 query 独立返回」；Commerce failure 不经过行程图 → 行程主体永不被破坏（G1/G19） |

优先级口径：**客服 > 旅游 > 选品 > 商务 > 主 Router**。商务 prefilter 插在旅游之后——「酒店推荐」「住宿推荐」已是旅游强信号（行程语境的住宿偏好，slot_filler 的 lodging 槽承接），继续进旅游域；只有**纯**商务意图（找/查/订酒店机票，无行程信号词）才进 Commerce。

## 2. Inventory（现存能力盘点）

全仓关键词扫描（hotel/flight/inventory/availability/offer/price/booking/deep_link/
reservation/fare/room/occupancy/cabin/IATA）结论：

| 类别 | 命中 | 说明 |
|------|------|------|
| Provider 契约 | `providers/travel/live/contracts.py` | `CAP_HOTEL_SEARCH`/`CAP_FLIGHT_SEARCH` 常量已预留，能力账登记 **not_implemented（OUT → STOP K）**；无 Hotel/Flight Protocol |
| 能力账 | `providers/travel/live/capabilities.py` | hotel.search/flight.search：`implemented=False, provider=None` |
| 预留语义 | `providers/travel/booking.py` | **纯预留** BookingProvider（预订状态机+幂等键），任务书 Phase 7 资产；STOP K Scope OUT（Booking Create）**不触碰**，属未来 Transaction STOP |
| 域内槽位 | `travel/slot_filler.py` | `lodging` 槽（住宿区域，记录性，不参与排程与指纹——「P0 无酒店供给数据」）；日期解析工具可复用 |
| 预算估算 | `travel/experts/budget.py` + `tools/travel/cost.py` | 静态成本模型（`rooms_needed` 2人1间、城市档位 `TRAVEL_CITY_COST_TIERS` 住宿定额）——**纯档位估算，连 mock 报价都没有**；行程预算口径（STOP I 冻结语义）与 Commerce 实时报价互不干涉，禁止互相渗透 |
| 前端消费 | `frontend/src/app/travel/page.tsx` | 仅展示 /travel/plan 的 lodging **估算**数字；无任何 hotel/flight/deep_link 消费（frontend-admin/frontend-cs 同） |
| API route | `app/api/routes/travel.py` | /travel/plan、/travel/export/ics、/travel/feedback、/travel/preferences、/travel/recommend——**无 hotel/flight/booking/offer 端点** |
| DB | `sql/migrations/`（049 个） | 零 travel/hotel/flight/booking 表（`016_inventory_alerts_pg` 是电商库存告警域，非 travel） |
| 路由信号 | `orchestration/graph/travel_prefilter.py` | 「酒店推荐/住宿推荐/住哪」= 旅游信号；无「找酒店/查机票」类纯商务信号 |
| Golden cases | `evaluation/datasets/` | travel-provider 8 探针全为 place/route/quota/cache；travel 数据集仅 T-G08 lodging 槽位 case——**无任何 commerce 金标** |
| deep_link/airline/cabin/IATA/occupancy | 全仓 grep 零命中 | 无任何残留半成品 |
| 测试 | `tests/travel/test_booking_candidate.py` | 钉死「域图出单后 `candidate_plans` 恒为空」——本轮不得违反（Commerce 不写行程状态，天然满足） |
| 真实凭据 | **不存在** | `.env`/`.env.example` 无任何 `HOTEL_*/FLIGHT_*/AMADEUS_*` 变量名（`HOTEL_PROVIDER_SCOPE` 仅为冻结决议记号，非 env）；唯一外部凭据 = 腾讯位置服务（LBS，无酒店/机票字段） |

**结论：无存量可复用的 hotel/flight 业务能力，不存在重复造轮子风险；也不存在任何可冒充 live 的假阳性来源。**

## 3. Data Source Matrix（数据源现状与判定）

| 数据源 | 类型 | 库存能力 | STOP K 判定 |
|--------|------|---------|------------|
| 腾讯位置服务 | LIVE（真实 API） | place/route/weather（LBS）；**无酒店/机票字段** | 不可复用为 Inventory Source |
| seed:local | 静态种子 | POI 候选（示例坐标） | 非库存数据 |
| estimate:local | 纯函数 | Haversine 路线估算 | 非库存数据 |
| Hotel Provider | — | — | **NOT_IMPLEMENTED**：无凭据、无 SDK、无账号 |
| Flight Provider | — | — | **NOT_IMPLEMENTED**：同上 |
| Fake Adapter（本轮新增） | FAKE（显式标注） | 模拟响应，仅测试/评测/链路验收 | **严禁作为生产验收**（任务书 §28）；`mode=fake` 时输出必须可辨识 |

## 4. Frozen Boundaries（冻结边界与触碰清单）

STOP H/I/J 冻结面（不得重新设计）：ProviderResult 七态、Freshness、Provider
failure taxonomy、cache 语义、quota 语义、travel itinerary quality metrics、
POI validator、Planner、itinerary repair、Travel Graph routing contract。

**本轮对冻结文件的全部触碰（均为加法式、backward compatible，逐条理由）：**

| 冻结文件 | 触碰 | 理由（为什么不存在更好的 extension point） |
|----------|------|------------------------------------------|
| `live/cache.py` | `FRESH_TTLS` 增加 hotel_meta/hotel_avail/hotel_price/flight_offer/flight_price 条目 | §14 要求分数据 TTL 且禁止第二套缓存；`cache_put_success` 只查本字典，commerce 自建 TTL 表 = 语义分叉 |
| `live/resilience.py` | `TIMEOUT_BUDGETS` 增加 hotel/flight 条目 | 同上：`resolve_budget` 只认本表；commerce 绕行 = 第二套超时体系 |
| `live/quota.py` | `daily_budget()` 增加 provider env 约定分支 | §21 要求复用 Provider quota 设施；`check_and_consume` 内部只查本函数 |
| `live/capabilities.py` | hotel.search/flight.search 行更新（implemented=True + fake-only 注记） | 能力账定义即「单一事实源」；STOP J 报告 Deferred §2 明文「STOP K 再评估」——本轮即该评估的落地 |
| `contracts.py` | 不触碰 | Hotel/Flight Protocol 与归一化 Record 放新文件 `live/commerce_contracts.py`（CAP_* 常量从原文件 import） |

以上触碰全部加法式（新增 dict 条目 / 新增函数分支），存量取值零变化；K8 阶段
重跑 STOP I Q1-Q10 + STOP J provider 全量回归证明无影响。最终报告单列
「Frozen Boundary Touches」章节。

## 5. Existing Reusable Components（直接复用清单）

- **ProviderResult / Freshness / ProviderStatus**（result.py，冻结）——commerce 调用结局唯一容器
- **共享缓存** cache.py：`build_key`（归一化）/`cache_get`（hit/negative/stale_ready）/`cache_get_stale`（仅失败路径）/`cache_put_success`/`cache_put_not_found`（仅 NOT_FOUND，60s）
- **resilience**：`call_with_budget`（timeout budget）+ `single_flight`（进程内去重）
- **quota**：`check_and_consume`（先查后增）/`BudgetExhausted`/`current_usage`
- **telemetry** 风格：低基数标签白名单、软失败、结构化事件 `[travel.commerce]`
- **错误分类学**：HTTP 客户端异常 → ProviderStatus 单一映射点（`status_from_lbs_error` 模式，commerce 适配器各自实现同构映射）
- **域图自注册**：DomainGraph 契约 + builder 自动发现 + route_mode 对应
- **评测框架**：`evaluation.runners.registry.register_runner` + datasets jsonl + cli 模块（travel-provider 探针模式）
- **E2E 驱动模式**：`scripts/e2e_travel_providers.py`（APISIX+JWT+SSE+真实 app/Redis/PG）

## 6. Gaps（缺口 = 本轮交付物）

1. Commerce 契约（Money/PriceSnapshot/Availability/HotelOffer/FlightOffer/指纹）——无
2. Hotel/Flight Provider Protocol 与适配器——无
3. Availability 语义矩阵（失败 ≠ 售罄）——无
4. Booking Deep Link 安全校验——无
5. 商务意图路由（prefilter + 域图）——无
6. 可观测（travel_commerce_* 指标/health 组件）——无
7. 评测（travel-commerce Golden + C1-C12）——无
8. 真实 Provider——**缺凭据（外部阻塞，见 §13）**

## 7. Proposed Architecture（定稿）

```text
用户（帮我找大阪10月3日到5日的酒店 / 帮我查10月3日东京到大阪的机票）
    ↓ APISIX :9080（JWT）
router_node
    ├─ 客服锁域/cs_prefilter（不变）
    ├─ travel_prefilter（不变：「酒店推荐」等行程语境仍进旅游域）
    ├─ selection_funnel_prefilter（不变）
    ├─ commerce_prefilter（新增：TRAVEL_COMMERCE_ENABLED + 纯商务意图正则）
    │     ↓ route_mode="travel_commerce"
    │  travel_commerce_graph_node（orchestration 适配器，新文件，包内实现）
    │     ↓
    │  travel-commerce 域图（2 节点线性，无 checkpointer）
    │     commerce_slot_filler →（槽位缺失→澄清收尾）/ commerce_executor
    │     ↓
    │  travel/commerce/（服务核心，全部新文件）
    │     request.py      参数解析+确定性校验（nights=check_out-check_in 等）
    │     models.py       Money(Decimal)/PriceSnapshot/Availability/HotelOffer/FlightOffer
    │     identity.py     offer fingerprint（sha256，跨进程稳定）
    │     ranking.py      确定性排序（price/duration/stops，禁「最推荐」话术）
    │     deeplink.py     https-only + host 白名单 + SSRF/scheme 防护
    │     service.py      编排：adapter 调用 → 归一化 → 校验 → 快照 → 排序 → 结果
    │     reporter.py     结果渲染（缺口说出来；禁预订话术；freshness 如实）
    │     telemetry.py    travel_commerce_* 低基数指标
    │     health.py       commerce_health()（hotel/flight 三态）
    │     register.py     域图自注册（domains/__init__.py 加一行 import）
    │     ↓
    │  providers/travel/live/commerce_contracts.py（Hotel/Flight Protocol + 归一化 Record）
    │  providers/travel/live/fake_commerce.py（FAKE 适配器，显式 mode 标注）
    │     复用：cache.py / resilience.py / quota.py / result.py（冻结设施）
    │     ↓
    │  （live 适配器：未实现——BLOCKED_BY_EXTERNAL_PROVIDER，仅留 Protocol 与
    │    能力账注记；真实供应商签约后按 tencent.py 模式接入，契约不变）
    ↓
Commerce Result（独立返回，不经行程图；行程质量天然零影响）
```

**关键设计决策：**

1. **无 checkpointer**：Commerce 是无状态单发查询（无跨轮改单语义），不接
   checkpointer → 无 checkpoint 租户碰撞面（G26 天然满足：无持久化状态可越权；
   缓存键只含查询参数不含 tenant/user——offer 是公共 Provider 事实）。
2. **不碰行程图**：Commerce result 独立成段返回；不写 Poi/TransitLeg/WeatherForecast/
   Itinerary 任何字段；budget 专家的住宿估算口径不受影响（G1/§18）。
3. **Fake 不冒充 live**：`TRAVEL_COMMERCE_PROVIDER_MODE` ∈ `off`(默认)/`fake`/`live`。
   `live` 在无适配器实现时 = DISABLED（如实 reason「真实供应商未接入」），绝不回退
   fake 充数。fake 适配器输出带 `source=fake:commerce`，渲染层披露「测试数据」。
4. **未知语义**（§2 红线）：`taxes=None`=unknown ≠ 0；provider 未给 cancellation →
   None ≠「不可退款」；未明确库存 → UNKNOWN ≠「有房」；价格过期 → stale ≠ 实时价。
   结构性保证：非 SUCCESS 结局根本不产生 offer 对象 → 失败不可能映射为 SOLD_OUT。
5. **LLM 零参与事实链**（§二.1）：意图识别/槽位抽取 = 正则 + 词典；金额/库存/航段/
   链接全部来自 adapter 返回；渲染 = 模板拼接。域图 2 节点 0 LLM。

## 8. DB / Cache Design

- **Redis（运行时加速）**：完全复用 `travel_provider` 共享缓存（同 back-end、同
  envelope、同 negative/stale 语义），仅新增分数据 TTL：
  | operation | fresh TTL | 依据 |
  |---|---|---|
  | hotel_meta | 3600s | 酒店名称/坐标/星级等基本事实慢变（类 place 600s，但酒店静态属性比地点更稳，取 1h） |
  | hotel_avail | 120s | 可订状态快变，短于 place、对齐 route（含路况的实时性等级） |
  | hotel_price | 180s | 价格快变且商业敏感，介于 avail 与 offer 之间 |
  | flight_offer | 120s | 航班时刻+舱位当日有效，票量变化快 |
  | flight_price | 180s | 票价随时段波动 |
  negative cache 60s / stale grace 600s 沿用冻结值不改。
- **PostgreSQL（可追踪历史事实）**：**本轮不做 Price Snapshot 持久化**。理由：
  snapshot 表的价值是真实成交前的价格审计与争议追溯；当前零真实 Provider，
  持久化的只能是 fake 价格——往审计链路写假事实比不写更危险（违反本仓
  「数据错误=P0」优先级）。Redis 缓存 envelope 已携带 observed_at/freshness
  满足运行时可追踪（G6/G21）。首个真实 Provider 落地时再以 append-only
  migration 落地（snapshot_id/tenant_id/provider/offer_fingerprint/amount/
  currency/observed_at/expires_at/created_at，登记 MIGRATION_TARGETS）。

## 9. Failure Matrix（语义矩阵，K4 实现与测试的唯一口径）

| Provider 结局（ProviderStatus） | Commerce 含义 | 缓存动作 | 用户可见 |
|---|---|---|---|
| SUCCESS + offers | 正常 | 写 fresh（分 TTL） | 完整 offer + 快照 + 链接 |
| SUCCESS + [] | 无搜索结果 | 写 fresh（空结果短 TTL=120s） | 「没有找到符合条件的X」 |
| NOT_FOUND | 无匹配 | negative 60s | 「没有找到符合条件的X」 |
| TIMEOUT | 服务超时 | **不入缓存**；可 stale-if-error | stale→明示「非实时」；否则「暂时无法获得实时库存」 |
| UNAVAILABLE / DISABLED | 服务不可用/未启用 | 同上 | 同上（DISABLED 另披露未接入） |
| RATE_LIMITED | 配额/限流 | **不入缓存** | 「查询过于频繁/服务繁忙」（绝不 = 售罄） |
| INVALID_RESPONSE | Provider 返回非法 | **不入缓存** | 同 UNAVAILABLE（脏数据拒收不穿透） |
| UNAUTHORIZED | 鉴权失败 | 不入缓存 | 同 UNAVAILABLE |

绝对禁止（结构性保证 + 测试钉死）：TIMEOUT/ERROR → 「没有酒店/售罄」；
stale → 「实时价格」；taxes unknown → 「含税 ¥0」；无库存信息 → 「有房」。

## 10. Security Risks 与对策

| 风险 | 对策 |
|------|------|
| Deep Link 注入（javascript:/data:/内网/credential@host） | deeplink.py 白名单校验（§12 全项），拒绝即 deep_link=None；校验率入指标 |
| Secret 泄漏 | Provider key 仅 env；trace/log/事件不含 key/token/完整 URL query/完整原始响应 |
| SSRF | https-only + host 白名单 + localhost/127./10./172.16-31./192.168./169.254 拒绝 + 长度 ≤2048 |
| 租户越权 | 无持久化状态；缓存键无身份字段；结果不落库；域图无 checkpointer |
| fake 冒充 live | mode 三态显式；fake 输出强制 `source=fake:commerce` + 渲染披露；live 未实现即 DISABLED |
| 预订话术误导 | 渲染禁词清单（已预订/已锁定/保证有房/保证有票/最终成交价/立即预订成功）+ 守卫测试 |

## 11. Test Matrix（pytest）

- `tests/travel/test_commerce_contracts.py`：Money Decimal（NaN/Infinity/负数/精度/币种）、
  taxes=None 语义、Availability 枚举、fingerprint 跨进程稳定（禁止 Python hash）、
  offer 模型 forbid 补齐（字段不存在必须 None）
- `tests/travel/test_commerce_deeplink.py`：§12 全矩阵（合法 https/allowlist 命中、
  javascript:/data:/file:/http、127.0.0.1/10.x/172.16.x/192.168.x/localhost、
  credential@host、超长、空 host 白名单全拒）
- `tests/travel/test_commerce_availability.py`：§9 语义矩阵逐行（八种结局 ×
  用户可见语义 + 缓存动作断言；TIMEOUT≠SOLD_OUT 结构性断言）
- `tests/travel/test_commerce_service.py`：hotel/flight 参数校验（check_out>check_in、
  rooms≥1、origin≠destination、return_date>departure_date、nights 确定性计算）、
  排序确定性、指纹去重不并 rate plan、缓存 replay 零外部调用（fake 计数器）
- `tests/travel/test_commerce_prefilter.py`（或扩展 tests/orchestration 既有顺序守卫）：
  商务命中/行程信号让路/CS 优先/开关关闭全放行
- `tests/travel/test_commerce_reporter.py`：渲染字段完整性（observed_at/freshness/
  来源/税费未知明示）+ 禁词清单 + 失败话术
- STOP J 冻结面回归：tests/travel/ tests/orchestration/ 全量 + 上面触碰文件相关
  既有测试零修改全绿

## 12. E2E Plan（真实网关）

`backend/scripts/e2e_travel_commerce.py`（镜像 STOP J 的 e2e_travel_providers.py
模式，app 镜像重建后执行）：

| 场景 | 条件 | 期望 |
|------|------|------|
| K-E2E-C1 | mode=off（默认） | 商务 query → 如实「未接入实时库存」；无编造 offer；主链非 500 |
| K-E2E-C2 | mode=fake（临时注入） | 完整链路：offer 渲染 + 快照字段 + freshness + 链接校验通过 + **replay 零增量**（G17） |
| K-E2E-C3 | mode=fake + 故障注入（预算软停/超时桩） | 不 500/不伪造 offer/不伪造 available/不伪造价格；quota 先查后增 |
| K-E2E-H1/F1 | **真实 Provider** | **BLOCKED**（无凭据）——如实记 BLOCKED_BY_EXTERNAL_PROVIDER |

全程 APISIX+JWT+SSE，不绕网关；.env 临时改动用后还原；共享栈只重建 app。

## 13. Scope In / Scope Out 与外部阻塞判定

**Scope In**：Hotel/Flight 契约与 Fake Adapter、Availability/PriceSnapshot 语义、
参数校验、确定性排序、指纹去重、缓存/新鲜度、Deep Link 安全、可观测/Health/Quota
接线、评测（C1-C12）、网关 E2E（链路级）、回归与冻结。

**Scope Out**（任务书原文）：Booking Create/Payment/Ticket Issue/Hotel Reservation
Write/Cancel/Refund/Flight Change/Hotel Modification/Payment Webhook/Provider
Order Sync——全部属未来 Transaction STOP（`providers/travel/booking.py` 预留
资产本轮零触碰）。另：post-plan enrichment（行程+商务建议）Deferred（§1）；
PG 快照持久化 Deferred（§8）。

**外部阻塞预判定（待 K7 后最终确认）**：仓库无任何 Hotel/Flight Provider 凭据/
SDK/账号（§2/§3）。按任务书 §28/§29：可交付 Contract+Fake+Tests+Architecture+
Evaluation，**不得写 HOTEL_LIVE_PASS=true / FLIGHT_LIVE_PASS=true**；最终
`STOP_K_PASS=false` + `BLOCKED_BY_EXTERNAL_PROVIDER=true` +
`TRAVEL_COMMERCE_CONTRACT_FROZEN=true`（契约冻结，生产闭环待真实供应商）。
G30/G31 记 BLOCKED，其余 Gate 以实际证据判定。
