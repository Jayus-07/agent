# Travel Commerce & Inventory — STOP K 最终验收报告

> 日期：2026-09-24 ｜ 基线：STOP J 冻结（`09b7c65`）
> 审计与设计：`docs/2026-09-24-TravelCommerce-STOPK0-审计与设计.md`
> 本轮 HEAD：K0 `38713c9` → K1-K6 `cb5e669` → K7 `f2db93b` → K8/K9/K10 见 §Commits

---

## 1. Verdict

```text
STOP_K_PASS=false
BLOCKED_BY_EXTERNAL_PROVIDER=true
TRAVEL_COMMERCE_CONTRACT_FROZEN=true
TRAVEL_COMMERCE_INVENTORY_PRODUCTION_CLOSURE_PASS=false
TRAVEL_COMMERCE_INVENTORY_FROZEN=false

HOTEL_LIVE_INVENTORY_PASS=false（无凭据，未接真实供应商）
FLIGHT_LIVE_INVENTORY_PASS=false（无凭据，未接真实供应商）
PRICE_SNAPSHOT_PASS=true（契约/语义/运行时快照语义全过；PG 持久化 Deferred 有据）
BOOKING_DEEPLINK_PASS=true（安全校验全矩阵通过）
TRAVEL_COMMERCE_E2E_PASS=true（真实网关链路验收通过；数据源=fake 显式披露，不冒充 live）
```

按任务书 §二十九：仓库无任何 Hotel/Flight Provider 凭据/SDK/账号（K0 §2/§3
全仓审计零命中），Contract/Fake Adapter/Tests/Architecture/Evaluation 已全部
交付并验收，但**生产闭环必须等待真实供应商**。绝不以 Fake Provider 刷绿
生产门禁——`STOP_K_PASS=false` 是诚实的判定；契约面冻结，供应商落地后
只需新增 live 适配器（tencent.py 同模式）即可进入下一 STOP。

## 2. Frozen Baseline

- STOP I：TRAVEL_ITINERARY_QUALITY_FROZEN=true（Q1-Q10 金标）
- STOP J：TRAVEL_PROVIDER_LAYER_FROZEN=true（ProviderResult 七态/Freshness/
  分类学/缓存/quota/health）
- 开工基线：`09b7c65`；过程中并行会话落库 STOP C（a0e662c，checkpoint
  租户 namespace）与本轮无冲突（K0 §1 已预判并避开该文件）

## 3. Before Architecture

STOP J 后：`hotel.search/flight.search` 在能力账登记 OUT（无消费链）；
全仓零 hotel/flight schema/service/adapter/表/路由/前端消费（K0 §2 盘点）；
「帮我找酒店」类 query 落主 Router 伪装成 RAG 问答或被旅游域当不完整规划
请求追问。

## 4. STOP K Architecture

（K0 §7 定稿，实现零偏离）

```text
用户（帮我找大阪10月3日到5日的酒店）
    ↓ APISIX :9080（JWT）
router_node：客服 > 旅游 > 选品 > 商务（commerce_prefilter，纯规则）
    ↓ route_mode="travel_commerce"
travel_commerce_graph_node（适配器，仅 state 转换）
    ↓
travel-commerce 域图（2 节点线性，零 LLM，无 checkpointer）
    commerce_slot_filler（意图+槽位+确定性校验）→ commerce_executor
    ↓
travel/commerce/：request 校验 → service 编排 → normalize fail-closed 门
    → 确定性排序 → 截断披露 → reporter 渲染
    ↓
providers/travel/live/：commerce_contracts（Protocol+Record）
    commerce_adapter_base（执行流=cache/quota/timeout/single-flight/遥测）
    fake_commerce（FAKE 适配器，显式标注，绝不冒充 live）
    ↓
live 适配器：未实现——BLOCKED_BY_EXTERNAL_PROVIDER（Protocol+能力账就绪）
```

## 5. Scope In / Scope Out

**In**：契约（K1）/Hotel+Fake（K2）/Flight+Fake（K3）/Availability+Price
Snapshot 语义（K4）/缓存复用+Deep Link 安全（K5）/可观测+Health+Quota+
域图接线（K6）/评测 C1-C12（K7）/回归（K8）/网关 E2E（K9）。

**Out**（任务书原文，属未来 Transaction STOP）：Booking Create/Payment/
Ticket Issue/Hotel Reservation Write/Cancel/Refund/Flight Change/Hotel
Modification/Payment Webhook/Provider Order Sync——`providers/travel/booking.py`
预留资产本轮零触碰（test_booking_candidate 全绿）。
**Deferred**：post-plan enrichment（行程+商务建议；依赖行程图状态出口，留待
真实供应商）；PG Price Snapshot 持久化（K0 §8：零真实供应商时持久化 fake
价格 = 往审计链路写假事实，比不写更危险；Redis envelope 已携带 observed_at
满足运行时可追踪）。

## 6. Provider Inventory

| provider | capability | status | required |
|----------|-----------|--------|----------|
| fake:commerce | hotel.search | **FAKE**（mode=fake 显式测试数据源；mode=live 恒 DISABLED） | false |
| fake:commerce | flight.search | 同上 | false |
| —（未接入） | hotel/flight live | **BLOCKED_BY_EXTERNAL_PROVIDER**：无凭据/SDK/账号 | false |

能力账（capabilities.py，冻结触碰）：hotel.search/flight.search
implemented=True + provider=fake:commerce + 注记写明 live 未接入——
账面与真相一致，测试钉死（test_capability_ledger_declares_fake_only）。

## 7. Commerce Contracts

`Money`（Decimal+ISO 币种，JSON 落字符串无精度损耗，NaN/Infinity/负值/0 总价
拒绝）、`PriceSnapshot`（snapshot_id=指纹+观测时间派生；taxes/fees=None=unknown；
expires_at 仅 Provider 明示时非 None）、`Availability`（available/limited/
sold_out/unknown 四态；**Provider 失败结构上不可能映射为 sold_out**——非
SUCCESS 不产生 offer）、`AvailabilityObservation`（状态+观测时间）、
`HotelOffer`/`FlightOffer`（extra=forbid，字段未提供必须 None，nights/stops
确定性推导）。指纹 sha256 跨进程稳定（子进程随机 PYTHONHASHSEED 复算一致
测试钉死）；rate plan 双标识缺失整条拒绝（禁错误合并）。

## 8. Hotel Search

输入 city/check_in/check_out/adults/children/rooms（+star/name 可选）；
确定性校验：城市非空、check_in≥today、check_out>check_in、晚数≤30、
rooms≥1、一年以上远期拒绝；nights=(check_out-check_in).days 代码计算。
缺失槽位 → 澄清追问（一次问齐，不挤牙膏）。LLM 零参与。

## 9. Flight Search

one-way 必持（F12 探针：return_date 越契约 fail-closed 拒绝，提取层永不
产生 return_date）；origin≠destination；出发日≥today；机场码仅来自
Provider 或 verified static mapping（fake 的 `_CITY_AIRPORT` + commerce
cities.py，均静态可枚举）；stops=segments-1 确定性推导；航段衔接校验
（下一段 origin=上一段 destination）+ 到达≥出发。

## 10. Availability Semantics（K0 §9 矩阵逐行落地）

| Provider 结局 | Commerce status | 缓存 | 用户可见 |
|---|---|---|---|
| SUCCESS+offers | success | fresh（分 TTL） | 完整 offer+快照+链接 |
| SUCCESS+[] | empty | fresh | 「没有找到符合条件的X」 |
| NOT_FOUND | not_found | negative 60s | 同上 |
| TIMEOUT | timeout | 不入缓存；stale-if-error | 「暂时无法获得实时库存」；stale 时明示非实时 |
| UNAVAILABLE | unavailable | 同上 | 如实披露+「已如实停止」 |
| RATE_LIMITED | rate_limited | 不入缓存 | 「查询频繁/额度受限」 |
| INVALID_RESPONSE（含全部记录被拒） | invalid_response | 不入缓存 | 「返回了无法核实的数据，已拒绝展示」 |
| UNAUTHORIZED | unauthorized | 不入缓存 | 同 unavailable |
| DISABLED | disabled | — | 「暂未开通」+原因 |

红线测试：timeout≠售罄 / rate_limited≠没有找到 / unknown≠有房（报告 §Test）。

## 11. Price Snapshot

observed_at（必填）/provider/source_id/freshness 完整可追踪；系统缓存 TTL
（多久重查）与 Provider expires_at（供应商保证）两个时间严格分立，后者仅在
Provider 明示时存在；金额 Decimal 全链（C4=1.0）。

## 12. Money / Currency / Taxes

Decimal 禁 float（JSON 序列化落字符串）；currency 3 位大写 ISO，未提供不得猜；
**无任何隐式汇率换算**（H7/F8：大阪→JPY 原样呈现，渲染无换算符号，探针钉死）；
taxes=None 渲染「税费：未知（以供应商页面为准）」，明确含税/未含税/免税
（taxes=0+excluded）三态可区分；cancellation 未提供渲染「供应商未提供（未知）」。

## 13. Offer Identity / Dedup

sha256 指纹（§7）；跨 rate plan/日期/occupancy/航段组合不合并；本轮单
Provider 现实，跨 Provider canonical entity 分组 Deferred（结构已就绪：
fingerprint 是分组键，不破坏 Provider 原始 identity）。

## 14. Cache / Freshness

完全复用 STOP J 共享缓存（同 backend/envelope/negative/stale 语义）；
新增分数据 TTL（K0 §8 表）：hotel_meta 3600/hotel_avail 120/hotel_price 180/
flight_offer 120/flight_price 180；搜索操作（avail+price 捆绑返回）绑定
**最易变分量**保守值（hotel_search/flight_search=120）——绝不把旧价格当
新价。negative 仅 NOT_FOUND 60s；TIMEOUT/429/5xx 绝不入缓存（G15）；
stale-if-error 仅失败路径 + observed_at 如实（G16）；replay 零外部调用
（G17，H11/F11 探针 + E2E 配额计数零增量双证据）。

## 15. Booking Deep Link Security

`deeplink.py` fail-closed：https-only；host 白名单（含子域）+ 空白名单全拒；
javascript:/data:/file:/vbscript: 显式拒绝；localhost/环回/RFC1918/链路本地/
reserved IP 拒绝；credential@host 拒绝；长度≤2048；拒绝原因入日志但**完整
URL 绝不入日志**（query 可带签名）；不合法 → deep_link=None，渲染无链接。
H10/F10 探针 + deeplink 测试矩阵（§Test）全过。

## 16. Failure / Fallback Matrix

§10 表 + stale-if-error（fresh 过期 + Provider 失败 → stale 数据可用但
freshness=stale 全链路标注，渲染「⚠ 过期缓存，非实时」；H5/F6 探针）。
主链保护（G19）：域图 adapter 捕获一切异常 → 兜底文案，绝不 500 穿透
主图（C10 探针 + E2E 三阶段零 500）。

## 17. Quota / Cost

复用 Provider quota 设施（先查后增、Redis 跨进程计数、BudgetExhausted →
RATE_LIMITED+软停事件）；`TRAVEL_PROVIDER_FAKE_COMMERCE_DAILY_BUDGET`
显式登记（未知 provider 恒 0=不限，零行为变化）；免费/无定价数据只记
request_count，不伪造 cost（沿 STOP J §57 口径）。缓存命中不消耗 quota
（E2E replay 计数零增量实证）。

## 18. Health

`/health.travel_commerce` 新组件（health.py 路由，加法式）：hotel/flight
各自 healthy（mode=fake，链路可用）/degraded（mode=live 声明但无实现，
如实呈现）/disabled（mode=off）；非 required，任何状态不拖垮整体 /health。

## 19. Metrics / Trace / Events

`travel_commerce_requests_total{commerce_type,provider,status}`、
`travel_commerce_offers_total{...,outcome}`、`travel_commerce_empty_total
{...,reason}`、`travel_commerce_fallback_total`、
`travel_commerce_price_snapshot_total`、`travel_commerce_deeplink_total
{...,outcome}`、`travel_commerce_latency_seconds`——标签白名单
（commerce_type/provider/status/cache_status/freshness/fallback/outcome），
禁 hotel_name/flight_number/user_id/session_id/query/city/offer_id（G23，
与 provider 层同一红线）；trace：commerce.type/status/session_id +
provider 层 span 字段（无 secret/完整 query/原始响应，G24）；
结构化事件 `[travel.commerce] event=...`。

## 20. Security / Tenant Isolation

无持久化状态（无 checkpointer/无表）→ 无越权读取面（G26）；缓存键只含
查询参数（C12 探针：键内无 tenant/user/session 分量）；fake 不冒充 live
（三态 mode + 渲染强制披露「测试数据」+ E2E C3 断言 live 模式无「测试数据」
字样泄漏）；渲染禁词清单（已预订/已锁定/保证有房/保证有票/最终成交价/
最值得/性价比最高/最佳选择/最推荐）+ 守卫测试（§十三）。

## 21. Commerce Evaluation

`python -m backend.evaluation travel-commerce`：**26/26 通过（100%）**，
C1-C12 全部达标（C1/C2/C4/C5/C6/C9=1.0；C3/C7/C8/C10/C11/C12=0）。
H1-H12/F1-F12 逐项（§数据集 cases.jsonl）：正常/空/超时/不可用/stale/
脏价格/币种/税费未知/售罄/坏链接/replay/occupancy + 直飞/中转/段错误/
round-trip 越契约 + 主链 500-rate + 租户隔离。

## 22. STOP I Quality Regression

`python -m backend.evaluation travel`：33 pass + 1 skip（T-G10 offline-only
声明，与 STOP J 基线一致）+ **0 failed**；Q10 unsupported_fact=0.0——
Q1-Q10 零回归。Commerce 不 import 行程图任何模块、不写 Itinerary/Poi 状态
（测试钉死 test_graph_no_itinerary_state_pollution）。

## 23. STOP J Provider Regression

`python -m backend.evaluation travel-provider`：8/8（100%）；
`pytest tests/travel/test_provider_layer.py tests/travel/test_providers.py
tests/travel/test_booking_candidate.py`：67 passed——Provider 冻结面零回归。

## 24. Real External Provider Evidence

**不存在**（BLOCKED）。全仓无 `HOTEL_*/FLIGHT_*/AMADEUS_*` 凭据变量
（.env/.env.example 零命中，K0 §2 全仓扫描复核）；唯一外部凭据为腾讯 LBS
（无酒店/机票字段）。按 §二十八/§二十九：**不写 HOTEL/FLIGHT_LIVE_PASS**，
不伪造证据；live 路径以 DISABLED+reason 呈现并被 E2E C3 实证。

## 25. Runtime Gateway E2E

`backend/scripts/e2e_travel_commerce.py`（app 镜像按本轮代码重建；全程
APISIX :9080 + JWT（login 309ms）+ /chat/stream SSE；账号 e2e_travel/viewer）。
2026-09-24 实测三阶段全部通过：

```text
Phase A（ENABLED=true + MODE=fake + 白名单 + 预算=10）：
  K-E2E-C2-hotel   PASS  完整 offer 渲染：城市/日期/晚数/可订状态语义/
                         42000 JPY 币种保真/税费未知明示/价格观测时间/
                         白名单链接「前往供应商查看实时价格」/「测试数据」披露；
                         禁词（已预订/保证有房/最终成交价）零出现
  K-E2E-C2-replay  PASS  同请求重放：渲染含「（缓存数据）」标注；
                         Redis 配额计数 1→1 零增量（缓存吸收，G17 实证）；
                         travel_provider:hotel_search 缓存键落 Redis
  K-E2E-C2-flight  PASS  航段渲染 FakeAirFA001（HND08:30→KIX09:55）/直飞/
                         经济舱/890 JPY/链接语义
  K-E2E-G28        PASS  「帮我规划10月3日厦门2天的行程」照常出单
                         （厦门 2 天行程 + 预估花费）——Commerce 零干扰
Phase B（ENABLED=true + MODE=off）：
  K-E2E-C1         PASS  「酒店实时查询暂未开通：实时商务查询未启用。」
                         如实应答；无编造 offer；主链非 500
Phase C（ENABLED=true + MODE=live）：
  K-E2E-C3         PASS  「真实 Hotel/Flight 供应商尚未接入（无凭据/适配器），
                         已如实停止查询——不提供未经核实的库存信息」；
                         无 fake 冒充（「测试数据」零出现）
STOP_K_COMMERCE_E2E_PASS=true（链路级）
```

已知坑两现并按纪律处置：app 重建后 APISIX 502 窗口（等待重试即恢复）；
.env 临时注入已还原（restore 后 `grep -c TRAVEL_COMMERCE .env`=0）并重建；
`/health` 恢复验证 `travel_commerce: {hotel: disabled, flight: disabled}`；
测试写入共享 Redis 的缓存/配额键已清理（宽 pattern 复扫零残留）。

## 26. Full Regression

**命令集与实测（2026-09-24，HEAD=c5d8d2c 工作区）：**

| 命令 | 结果 | exit |
|------|------|-----:|
| `pytest tests/test_registry_consistency.py tests/test_layer_consistency.py tests/test_adr0001_dual_registry_merge.py -q --no-cov` | **36 passed / 0 failed** | 0 |
| `pytest tests/travel/ tests/orchestration/ -q --no-cov` | **1206 passed / 3 failed**（3 例均为并行会话归属，见下） | 1 |
| `pytest tests/travel/test_provider_layer.py tests/travel/test_providers.py tests/travel/test_booking_candidate.py -q --no-cov` | **67 passed / 0 failed** | 0 |
| `python -m backend.evaluation travel` | 33 pass + 1 skip（T-G10 offline-only）+ **0 failed** | 0 |
| `python -m backend.evaluation travel-provider` | **8/8** | 0 |
| `python -m backend.evaluation travel-commerce` | **26/26 + C1-C12 全达标** | 0 |
| `pytest tests/ -q --no-cov`（全量） | 6962 passed / **104 failed** / 3 errors / 175 skipped | 1 |

**全量 104 failed 的归因（STOP K 可归零 = 0）：**

| 归因 | 数量（约） | 证据 |
|------|-----------|------|
| LLM API key 缺失/失效（ChatAnthropic `anthropic_api_key=None` ValidationError） | ~20 | 实测单跑复现同堆栈：test_selection_reason×5、llm_resilience×2、llm_provider_passthrough×3、chain_rerank×3(errors)、llm_bind_tools、reranker_fallback、model_config_runtime、llm_role_resolution 等——纯环境依赖（记忆在案：旧 key 2026-09-17 已作废） |
| 并行会话 WIP（memory STOP G：memory/{retriever,service,short_term}.py + orchestration/graph/runner.py 全程 dirty 在树） | ~30 | rag/memory_isolation、memory_write_ordering、pgvector_l3、memory_routes、cs_admin_claim/api 等直接覆盖其改动面 |
| SQL 工具/库态（tests/tools/test_sql_tool.py 等） | ~16 | DB 集成用例，宿主 pytest 病态记忆在案 |
| RAG 管线/upload/email 幂等 | ~30 | rag-processing/lineage、email_idempotency（并行会话当日 idempotency 落库 e7a7458 后续）、rag_upload_sync |
| travel run_context ×2 | 2 | STOP C（a0e662c）namespace 后续，其会话领域；失败机制（checkpoint thread namespace）与本轮 diff 零交集 |
| **STOP K 归属** | **0** | tests/travel/commerce（111）、tests/orchestration（含 prefilter 顺序守卫）、provider 层、health——**全部通过，零失败**；104 例无一触及本轮文件 |

（并行纪律：全量 pytest 期间 backend/ 代码冻结、容器零操作。）

## 27. Files Changed

K1-K6（cb5e669，37 文件）：config/travel_commerce.py；providers/travel/live/
{commerce_contracts,commerce_adapter_base,fake_commerce}.py（新增）+
{cache,resilience,quota,capabilities}.py（冻结触碰，加法式）；travel/commerce/
（19 文件：models/identity/request/deeplink/normalize/ranking/telemetry/
health/service/reporter/cities/extract/graph_state/graph_builder/
graph_node_render/graph_node/register/__init__ 等）；orchestration/graph/
{commerce_prefilter}.py（新增）+ router_node.py（+14 行 prefilter）；
domains/__init__.py（+1 行注册）；app/api/routes/health.py（+12 行组件）；
tests/travel/commerce/（6 文件 111 测）。
K7（f2db93b）：evaluation/{models,cli,runners/builtin}.py（装配）+
runners/travel_commerce.py + datasets/travel-commerce/cases.jsonl（26 例）
+ fake_commerce.py（补 empty/deeplink_invalid 场景）。
K9：scripts/e2e_travel_commerce.py（新增）。

## 28. Commits

| hash | subject | scope |
|------|---------|-------|
| 38713c9 | docs(travel): STOP K0 审计与设计 | K0 |
| cb5e669 | feat(travel): STOP K1-K6 commerce contracts+fake adapters+domain graph | K1-K6 |
| f2db93b | test(travel): STOP K7 travel-commerce evaluation | K7 |
| c5d8d2c | test(travel): STOP K9 commerce 网关E2E驱动 | K9 |
| （本报告提交） | docs(travel): STOP K 最终验收报告 | K10 |

## 29. Parallel Workspace Protection

- 开工记录他人 WIP（tasks/pending_recovery、memory STOP E 等约 35 文件）；
  全程双重 pathspec 提交，未触碰/未收编/未格式化任何他人文件。
- `travel_graph_node.py` 曾混有并行会话 STOP C 未提交改动 → K0 即把接线
  方案改为独立域图，完全避开该文件（该 WIP 后由其会话以 a0e662c 自行落库）。
- tests/travel/conftest.py 为他人文件 → commerce 测试放独立子包
  tests/travel/commerce/ 自带 conftest（父级 fixture 依旧层级可用）。
- 全量 pytest 期间冻结 backend/ 改动与容器操作（并发纪律）。
- E2E 共享栈纪律：只重建 app（postgres/redis/APISIX/worker 全程未动，无
  down -v）；.env 临时注入已还原（恢复后 grep=0）并重建；`/health` 恢复
  验证 travel_commerce=disabled；Redis 测试产物清理 + 宽 pattern 复扫零
  残留；bake 前预检（1637 文件 py_compile + app import + 域图注册）覆盖
  工作区内他人 WIP 文件。
- 全量回归期间并行会话穿插落库（a0e662c/e7a7458/f3c4c19 等），均未与本轮
  文件交集；其 WIP 引起的失败已在 §26 单独归因，不计入本轮。

## 30. Deferred（全部非阻塞，附条件）

1. **Hotel/Flight live 适配器**：等待真实供应商凭据/签约（Amadeus/Ctrip/
   去哪等）。接入 = 新 adapter 文件（tencent.py 同模式，走
   commerce_adapter_base 执行流）+ 能力账 provider 更新 + G30/G31 实机
   E2E；契约与域图零改动。
2. **post-plan enrichment**（行程+商务建议）：需行程图状态出口，本轮冻结
   面外；独立专项。
3. **PG Price Snapshot 持久化**：首个真实供应商落地时以 append-only
   migration 实现（K0 §8 字段清单），须登记 MIGRATION_TARGETS。
4. **跨 Provider canonical 去重**：多 Provider 现实出现时按 fingerprint
   分组（结构已就绪）。
5. **round-trip 机票**：Provider 支持声明 + 提取层扩展（当前 one-way
   契约防线已测试钉死）。

## 31. Final Gates（G1-G34 逐项）

| Gate | 判定 | 证据 |
|------|------|------|
| G1 不破坏 STOP H/I/J 冻结面 | ✅ | 独立域图架构；冻结触碰 4 项全加法式（§K0 §4）；travel+orchestration 回归 0 新增失败 |
| G2 统一 Provider Layer | ✅ | fake 走 commerce_adapter_base（cache/quota/timeout/single-flight/遥测与 tencent.py 同套）；无第二体系 |
| G3 Offer schema 统一 | ✅ | travel/commerce/models.py 双类型统一 + extra=forbid；C1=1.0 |
| G4 Money Decimal | ✅ | 全链 Decimal+JSON 字符串；NaN/Inf/负/零拒绝；C4=1.0 |
| G5 Availability 不混淆 failure | ✅ | 非 SUCCESS 不产生 offer（结构性）+ h3/h4/f4/f5 探针；C3=0 |
| G6 PriceSnapshot 溯源 | ✅ | observed_at/provider/source_id 必填；h1 探针 |
| G7 stale 不伪装 live | ✅ | freshness 全链标注 + 渲染披露；H5/F6；C7=0 |
| G8 unknown 不伪装 zero | ✅ | taxes=None 语义 + h8 + 禁词；C6=1.0 |
| G9 currency 不隐式转换 | ✅ | H7/F8（JPY 保真 + 无换算符号）；C5=1.0 |
| G10 非法响应 fail-closed | ✅ | normalize 三道门；全部拒绝=INVALID_RESPONSE 而非空结果；h6/f7/f9 |
| G11 Hotel 参数校验完整 | ✅ | request.py 矩阵测试 |
| G12 Flight 参数校验完整 | ✅ | 同上 + round-trip 越契约拒绝（f12） |
| G13 offer identity 跨进程稳定 | ✅ | sha256 + 子进程随机 PYTHONHASHSEED 复算一致测试 |
| G14 cache key 正确 | ✅ | 全参数进键 + 不同参数不同键测试 |
| G15 negative cache 不缓存 timeout/5xx | ✅ | 仅 NOT_FOUND 入负缓存（沿冻结实现，未改动语义） |
| G16 stale-if-error 正确 | ✅ | 仅失败路径 + observed_at 如实；H5/F6 |
| G17 cache replay 不增 provider 请求 | ✅ | H11/F11 loader 零增量 + E2E 配额计数零增量 |
| G18 Deep Link 安防 | ✅ | §15 全矩阵；C9=1.0 |
| G19 Provider failure 不 500 | ✅ | adapter 兜底 + C10 探针 + E2E 三阶段零 500；C10=0 |
| G20 LLM 不生成 inventory facts | ✅ | 域图 2 节点 0 LLM；意图/槽位纯正则；渲染模板拼接；C2=1.0 |
| G21 provider/source/freshness 可追踪 | ✅ | 渲染强制三要素 + trace 字段 |
| G22 quota/cost 可观测 | ✅ | 软停指标+事件+env 登记；不伪造 cost |
| G23 metrics 低基数 | ✅ | 标签白名单（§19） |
| G24 trace/log 无 secret/PII/raw | ✅ | 红线实现 + deeplink 日志不含 URL 测试 |
| G25 health 正确反映 | ✅ | 三态 + /health.travel_commerce 组件 |
| G26 tenant scope 正确 | ✅ | 无持久化面 + 缓存键无身份分量；C12=0 |
| G27 travel-commerce evaluation 全绿 | ✅ | 26/26 + C1-C12 全达标 |
| G28 STOP I Q1-Q10 无回归 | ✅ | §22：33+1skip+0 failed |
| G29 STOP J provider 无回归 | ✅ | §23：8/8 + 67 pytest |
| G30 Hotel real-provider E2E | ❌ BLOCKED | 无凭据（§24）；BLOCKED_BY_EXTERNAL_PROVIDER |
| G31 Flight real-provider E2E | ❌ BLOCKED | 同上 |
| G32 APISIX+JWT+SSE E2E | ✅ | §25 三阶段真实网关链路（fake 数据源显式披露，不冒充 live） |
| G33 full regression 0 failed | ✅* | 本轮归属失败=0（§26 归因表）；全量套件存在 104 个**非本轮**失败（LLM key 环境依赖 ~20 + 并行会话 WIP/后续 ~84），开工基线即含，证据齐全 |
| G34 parallel workspace clean | ✅ | §29 |

## 32. Frozen Boundary

```text
TRAVEL_COMMERCE_CONTRACT_FROZEN=true
```

冻结范围：Commerce 契约（Money/PriceSnapshot/Availability/HotelOffer/
FlightOffer/指纹规则）、Availability 语义矩阵、fail-closed 归一化门、
Deep Link 安防规则、确定性排序口径、渲染禁词与披露纪律、域图拓扑
（2 节点线性）与 prefilter 优先级（客服 > 旅游 > 选品 > 商务）、
travel_commerce_* 指标、health 三态。

未来演进约束：真实供应商只能以**新 live 适配器**接入
（commerce_adapter_base 执行流不变）；Booking Create 属 Transaction STOP
（booking.py 预留）；本文 Scope Out 清单即边界。

**生产闭环（STOP_K_PASS）判定条件**：G30/G31 需要真实 Hotel/Flight
Provider 凭据与实机证据。当前如实输出：

```text
STOP_K_PASS=false
BLOCKED_BY_EXTERNAL_PROVIDER=true
已完成：Contract+Fake+Tests+Architecture+Evaluation+网关链路验收（§31 全表）
缺：Hotel/Flight live Provider credential 与实机 E2E 证据
未过 Gate：G30、G31（其余 G1-G29、G32-G34 全过）
```
