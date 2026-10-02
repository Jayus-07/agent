# Domain Service Map — 域图 × 专家 × 工具 × 第三方服务 × 凭据

> 回答一个问题：**每个域图里的每个专家，用什么工具、调什么外部服务、凭据怎么管、挂了怎么办。**
> 最后验证：2026-09-29 · 基于三路并行代码 survey（专家文件 import 与延迟 import 逐个核对、config 变量逐个 grep、`.env` 变量名实测）。
> 编排层（图结构 / 进入通路 / 跨轮契约）见 [ai-runtime.md](ai-runtime.md)；本文只写**依赖与治理**。

---

## 0. 口径纪律（先读这个）

- **可派生的事实不在本文手抄**：「专家→工具」映射的事实源是专家文件的 import / 延迟 import；「capability→Skill」的事实源是 `backend/orchestration/router/capabilities.yaml`；「有哪些工具」的事实源是 `backend/tools/`（`@tool` + `tool_registry.register`）。本文引用这些事实时**标注事实源**，代码变更以代码为准，本文过期不承担事实源职责。
- **本文承载代码里读不出来的部分**：降级路径的设计意图、服务的开关/熔断/缓存策略、凭据治理口径、红线约束（如「绝不 fallback 充数」）、演进承诺（P0 种子 → P1 MCP）。
- 全文只出现**凭据变量名**，绝不出现密钥值。

---

## 1. 全局能力地图（事实源索引）

| 层 | 数量 | 唯一事实源 |
|---|---|---|
| capability | 17（14 路由可见 + 3 内部 `routed:false`：`email.watch` / `competitor.watch` / `competitor.history`） | `backend/orchestration/router/capabilities.yaml` |
| Skill | 12 | `backend/skills/registry.py::_instances` |
| Workflow | 4（daily_report / inventory_alert / market_research / selection_decision） | 同上 capabilities.yaml `workflows:` 段 |
| Tool | 34 | `backend/tools/`（map/ 7 件、travel/ 6 件 + 各域散件） |
| 域图 | 5（本文 §2~§6） | `backend/domains/__init__.py` 自动发现 |

对账入口：管理端 `/api/agents`、`/api/capabilities`、`/skills` 页。其中 `/api/agents` 的域图节点带
**域归属三字段** `domain` / `domain_label` / `subflow`（由域图注册表派生，顶级域图三者皆 null）——
管理端据此展示「谁是谁的子流」，**不在前端另存一份归属映射**（STOP E §9）。**五个域图共用「注册恒在、prefilter/开关把门」模式：域图代码不感知开关**，`CS_ENABLED` / `TRAVEL_ENABLED` / `SELECTION_FUNNEL_ENABLED` / `TRAVEL_COMMERCE_ENABLED` / `TRAVEL_BOOKING_ENABLED` 代码默认全 false，由根 `.env` 决定放量。

---

## 2. 客服域图（CS_ENABLED）

图：`state_loader → pending_handler → cs_supervisor ⇄ 5 专家 → cs_reporter`（recursion 20，专家上限 5 轮）。配置中心 `backend/config/customer_service.py`。

| 专家 | 职责 | 依赖（事实源=文件 import） | 外部服务/凭据 | 降级行为 |
|---|---|---|---|---|
| knowledge | 知识问答 RAG | `customer_service/knowledge/service.py` → `rag/pipeline.ask_result`（默认 kb=`cs_faq`，audience=customer 的 cs_* 库）；置信度门控 `knowledge/answer_decision.py` | LLM 主栈（§7） | RAG 异常→固定话术引导转人工；无答案→拒答后缀 |
| query | 只读业务查询 | `service/order_service.py`（订单）/ `logistics_service.py`（物流，演示口径=orders 表推算 ETA，**未接外部物流 API**）/ `ticket_store` | `CS_BUSINESS_GATEWAY_MODE`：`http`→business-service（`X-Internal-Token`=`INTERNAL_API_TOKEN`）；`sandbox`→直查演示库 | **网关失败不降级回 sandbox**（红线）；复合问题 LLM 分解（`CS_QUERY_LLM_DECOMPOSE_ENABLED`）失败回退单意图 |
| action | 写操作 + 确认状态机 | `confirmation_flow.process_confirmation`（唯一执行流）+ `service/refund|after_sales|account_action`（当前 `simulate_execute` 模拟执行）+ `business_guard` 唯一性守卫 + `risk.py` 分级 | PG：`cs_pending_actions` / `ai.idempotency_records` / `agent_actions` | DB 不可用→「转人工」，**绝不 fallback 演示库**；缺订单槽位→结构化追问（禁猜 latest）；执行包裹幂等账本（`client_key=cs_action:{confirmation_id}`），IN_DOUBT 阻断 |
| complaint | 投诉→工单→安抚→转人工 | `complaint_service.detect_with_llm_fallback`（规则优先，LLM 只评 severity）+ `ticket_store` + `realtime.get_agent_hub().publish("conversation.waiting")` | LLM（`CS_COMPLAINT_LLM_ENABLED`，3s 超时） | LLM 异常→确定性回退规则判定；工单落库失败不阻断安抚 |
| handoff | 显式/自动转人工 | `handoff.detect_handoff_trigger` + 状态机单次落盘 WAITING_HUMAN + 工单 `HANDOFF-{uuid8}` | Redis `cs:events` 广播 | 已处人工态→复用工单返回 duplicate（防 supervisor 同轮重复调度） |

**敏感操作链**（用户确认后执行）：前端确认卡 `CSConfirmCard.tsx` → `POST /api/cs/confirm` → `confirmation_flow`：原子认领（DB 条件 UPDATE pending→confirmed）→ 状态机 `PENDING→CONFIRMED→EXECUTING→…` → 幂等账本包裹执行 → 审计落库。TTL 900s / 重试上限 3。注意：**CS 这套 confirmation 体系与主图工具审批门（§8 tool_approval）是两条独立人机确认链**，前者管对话内动作确认，后者管编排层工具副作用。

**supervisor 三层**：L1 handoff 拦截 + 循环上限（5）+ 低置信 finish；L2 pending 等待 / 同专家连跑 2 次截停；L3 LLM 兜底（`CS_SUPERVISOR_LLM_ENABLED`，默认 OFF）失败回退规则路由。人工接入超时（`CS_HANDOFF_TIMEOUT_SECONDS`=120）自动回退 AI。

**转人工之后的派单与坐席**：`customer_service/dispatch/service.py`（P4 落池 / P6 单 PG 事务派单：优先级→在线容量过滤→最少负载→`agent_offered`）；offer 30s 超时回收，超 5 次关单恢复 AI；坐席 WS `ws/cs/agent?ticket=`（一次性 ticket，跨进程经 Redis `cs:events`），断线降级 2s 轮询。

## 3. 旅游规划域图（TRAVEL_ENABLED）

图 10 节点：`slot_filler → supervisor ⇄ {poi/transit/weather/budget/risk} + validator → repair`。配置中心 `backend/config/travel.py`。**契约 `TravelBrief → Poi → Itinerary`（`backend/travel/models/`）不变承诺：换数据源不改契约**。

| 专家 | 依赖（事实源=import） | 外部服务/凭据 | 降级行为 |
|---|---|---|---|
| poi | `tools/travel/poi.py::search_poi`（本地种子 36 条：福州/厦门/杭州）+ `routing.day_radius_km`；动态 import `providers/travel/live/tencent.py::resolve_missing_places`（必去项补全） | 腾讯 LBS（`TENCENT_LBS_KEY/SK`）；`TRAVEL_USE_LIVE_MAP` 默认 false | 种子池优先；腾讯补全失败→留「未匹配」notes；**无候选如实说明不造数据** |
| transit | `routing.estimate_leg/route_km` + `cost.day_cost`；动态 import `live_map.prefetch_legs`（路段预热） | 腾讯 LBS 路线 | provider 返回 None→**本地直线估算回落（source=`estimate:local`）**，契约不变；远期行程（>14 天）强制本地估算 |
| weather | `providers/travel/live/__init__.get_weather_provider()`：**主源腾讯 LBS 天气 + 备源和风天气**（`qweather.py`，配 Key 才参与） | `TENCENT_LBS_KEY` / `QWEATHER_API_KEY`；`TRAVEL_WEATHER_ENABLED` 默认 true、超时 6s | 七态软降级：disabled 静默跳过 / 超时·限流写明原因 / 预报窗无交集如实披露；**绝不阻塞排程主链**。⚠️ 仓库无 `WEATHER_API_KEY` 变量（常见误传） |
| budget | `cost.estimate_cost`（纯函数：门票×人数+城市档位餐交） | 无外部调用 | 无预算→写「本次未做预算校验」，金额不进 notes 防陈旧 |
| risk | 动态 import `tools/travel/knowledge.py`（RAG 检索，kb=`travel`，只取原文不生成） | pgvector（`TRAVEL_RAG_ENABLED`） | 检索空/失败→退回纯免责声明；source=seed→追加「本地示例数据」warning |

**validator**：纯规则零 LLM 零 IO（`backend/travel/validator.py` 头三条纪律），四轴（time/geo/pace/budget）+ coverage，数据=行程字段 + config 阈值；**repair** 只拿不添、必去项永不静默丢弃（`kept_required`）。

**演进承诺（代码锚点）**：`tools/travel/poi_seed.py` 头部声明——接入地图/票务 MCP 后替换数据供给、source 改 provider 标识、**Poi 契约不变**；`experts/risk.py` docstring——P1 接 MCP 后本节点扩为真实外部风险核查。

## 4. 旅游商务域图（TRAVEL_COMMERCE_ENABLED，STOP K）

2 节点线性零 LLM：`commerce_slot_filler → commerce_executor`。**与规划域完全独立**（不读不写 Itinerary/Poi/TravelBrief）。

- Provider 三态 `off|fake|live`（`config/travel_commerce.py::TRAVEL_COMMERCE_PROVIDER_MODE`）：live 当前无凭据无适配器→恒 DISABLED 且**绝不回退 fake 充数**；fake 强制 source=`fake:commerce` 并向前端披露「测试数据」。
- 执行流统一走 `commerce_adapter_base.run_commerce_search`（cache/quota/single-flight/timeout/遥测）。
- Deeplink 域名白名单 `TRAVEL_COMMERCE_DEEPLINK_ALLOWED_HOSTS` 空=fail-closed。
- 冻结约束：不改 `live/contracts.py`（STOP K0 §4），新增契约走 `commerce_contracts.py`。

## 5. 旅游预订域图（TRAVEL_BOOKING_ENABLED，STOP L）

2 节点线性零 LLM：`booking_resolver（纯正则子意图 confirm|status|new）→ booking_executor（唯一 IO 节点）`。订单/报价事实全在 PG `travel` schema，图状态不承载业务事实。

**执行前门（顺序固定）**：鉴权 → 报价有效 → 确认有效 → 价格库存复核 → **幂等 claim**（`shared/idempotency.py`，表 `ai.idempotency_records`；幂等键=sha256(`pv1|tenant|actor|travel.booking.create|merchant_order_id|provider|op`)）→ 订单 CAS（SUBMITTING）→ 才调 `provider.create_booking()`。

**结局三模型**（`shared/provider_idempotency.py::PROVIDER_CONTRACTS`）：Model A 原生幂等=SAFE_RETRY / Model B 可查询=RECONCILE_FIRST / Model C 裸奔=IN_DOUBT；**UNKNOWN 一律置订单 IN_DOUBT 绝不猜 SUCCESS**；对账=`booking/reconciliation.py`（查询事实≠重试），恢复扫描 `recovery.py`（stale 120s 批 20）。**真实供应商未登记能力契约→fail-closed 拒绝执行**。Mock 三 profile（`booking/fake.py`）分别模拟 A/B/C 三模型。注意区分两个 fake：`live/fake_commerce.py`（搜索，STOP K）vs `booking/fake.py`（预订副作用，STOP L）。

## 6. 选品漏斗域图（SELECTION_FUNNEL_ENABLED）

7 节点线性漏斗 + 条件短路（无 supervisor）：`brief → pool → screen → verify → econ → rank → report`；任一层淘空直接跳 report 如实收尾。`stages/` 包零 LLM（verifier 复用 `selection/scoring.py`），**漏斗可复现**。

- **数据源全部在内部 PG**（`{prefix}import_candidates` 导入池 + `keyword_stats`/`product_reviews` 赛道数据 + 竞品 watchlist 兜底，反爬预算 40 次/天），**不调外部第三方服务**。
- 管理端链路：`/selection-funnel` API（`resolve_operator_role` 门禁）→ 管理端选品漏斗页。
- 预过滤已接线（与旅游同层，强信号词命中进域图；「决策/值不值得」类让给 selection_decision workflow）。

---

## 7. 模型类服务矩阵（凭据 DB 唯一来源）

**治理口径（2026-09 起）**：模型配置与凭据**只存 PG**（6 表：`llm_providers` / `llm_models` / `llm_provider_credentials`(Fernet 密文+指纹+尾4位) / `llm_model_role_bindings` / `llm_specialized_model_bindings` / `llm_model_role_policy`），后台 15s 刷新进进程内快照、热路径零 IO。**`.env` 无回退通道**：DB 未配置=明确失败，绝不回落环境变量（守护测试 `tests/infra/test_llm_credentials.py`）。主密钥 `SECRETS_ENCRYPTION_KEY` 必须留在 `.env`。管理端「模型与供应商」页维护。

| Provider | 服务 | driver / billing | 凭据（DB 未配置时报错提示名） |
|---|---|---|---|
| minimax | MiniMax（**Anthropic Messages API**，端点为代码内集中管理字面量） | anthropic / metered | `MINIMAX_API_KEY` |
| deepseek | DeepSeek 云 | openai / metered | `DEEPSEEK_API_KEY` |
| qwen | 阿里云百炼 DashScope | openai / metered | `QWEN_API_KEY` |
| qwen_tp | 百炼模型包 Token Plan 专属端点 | openai / subscription | `QWEN_TP_API_KEY` |
| siliconflow | 硅基流动 | openai / metered | `SILICONFLOW_API_KEY` |
| vllm | 自托管 vLLM（`workspace/vllm-server-deploy/`） | openai / local | `VLLM_API_KEY`（loopback 地址需勾「内网服务」过 url_guard） |
| ollama | 本地 Ollama（cloud 模式禁用） | ollama / local | 无 Key（`OLLAMA_BASE_URL`，唯一保留的基础设施默认值） |
| driver_compat | DB 自建供应商（custom-*）通用出口 | 按 llm_providers.driver 分发 | 随供应商登记 |

**现行默认角色绑定**（代码默认，实际以 DB 绑定为准）：main=MiniMax-M3；embedding=text-embedding-v3（**换模型必须重建向量索引**，禁 fallback）；rerank=qwen3-rerank（失败跳过重排）；ocr=qwen-vl-max。LLM 韧性链统一：重试+熔断 → fallback 模型 → 固定拒答话术（`infra/llm/proxy.py`）。

## 8. 非模型类服务清单

| 服务 | 用途 | 消费方 | 凭据/开关 | 降级与防护 |
|---|---|---|---|---|
| **腾讯位置服务 LBS** | 地理编码/POI/路线/天气/静态图/街景 | `tools/map/` 14 工具（高德商家检索除外，见下行）、`/api/map/*` 后端代理（前端永不接触 Key）、旅游 poi/transit/weather | `TENCENT_LBS_KEY`（服务端）+ `TENCENT_LBS_SK`（SN 签名，可选）+ `TENCENT_LBS_FRONTEND_KEY`（仅浏览器 URL 场景，配 Referer 白名单）；`TENCENT_LBS_ENABLED` 且 Key 非空才生效 | 定位「增强非硬依赖」：缺失/熔断整体降级（空结果/直线估算/`is_estimate=true`）；节流 0.2s（≈5 QPS 对齐个人 Key）、熔断 3 次/60s、缓存 600/120/300/1800s、stale-if-error |
| **高德开放平台（Web 服务 API v5）** | 商家维度详情检索：评分 / 人均消费 / 营业状态（`show_fields=business`）——「推荐哪家店、现在去行不行」的依据，腾讯源只有名称/类别/地址 | `tools/map/merchant.py::map_merchant_search_tool` 及 `map_lookup_tool` 的 `merchant_search` action、旅游域 Research Agent（美食/住宿实时增强检索） | `AMAP_KEY` + `AMAP_SECRET`（仅控制台开启数字签名时）+ `AMAP_ENABLED`（代码默认 true，Key 非空才生效）；类目码 `AMAP_FOOD_TYPES`/`AMAP_HOTEL_TYPES`、区域 `AMAP_DEFAULT_REGION` | 未配置时 merchant_search 降级返回「未配置」提示，不影响腾讯系地图能力；字段缺数据显式 null 不编造 |
| **mcp-12306（外部 MCP server）** | 12306 火车票余票/时刻查询数据源——「外部 MCP server 作为 Tool 数据源」首例（2026-10-02）；上游 drfccv/mcp-server-12306，非官方聚合、无 SLA、仅供学习研究（不商用） | `tools/travel/train.py::travel_train_search_tool`（经 `infra/mcp_client.py` 同步薄客户端）、旅游 Planning Agent 交通耗时/车次 | 无凭据；`TRAIN_MCP_ENABLED`（默认关）+ `TRAIN_MCP_BASE_URL`；compose 服务 `mcp-12306`（宿主 `127.0.0.1:18000` → 容器 8000） | 关闭时明确报「未启用」；协议层失败归「查不了」，与「查不到」按键名结构区分不猜文本；只读查询无副作用、不进审批门；失败不阻塞行程主链（耗时估算回退本地直线） |
| **和风天气 v7** | 天气备用源（2026-09-28 接入） | `get_weather_provider()` 主备组合器备位 | `QWEATHER_API_KEY` + `QWEATHER_API_HOST`；不配 Key 零参与 | 仅主源超时/限流/鉴权/城市未收录才降级；DISABLED 不触发降级；双源皆败保留主源结局 |
| **SMTP 邮件** | 报告推送 / email.send | `tools/email.py`（agently 引擎时另有 search/read/watch） | `SMTP_HOST/PORT/USER/PASSWORD/FROM`；`EMAIL_ENGINE`=smtp\|agently；当前 .env 未配置 | 发送前双保险：审批门（§8 下方）+ 进程内指纹去重 600s + **PG 幂等账本**（crash-window 防重发） |
| **DuckDuckGo / Bing** | web.search（**爬 HTML 非官方 API**，无凭据） | `tools/web.py` | 无 | DDG 失败兜底 Bing 结果页；两路皆败上抛重试 |
| **crawl4ai（Playwright）** | web.crawl | `tools/crawler_runtime.py` 常驻单例 | 可选 `CRAWLER_PROXY` | SSRF 防护 `tools/url_guard.py`（FakeIP 感知）；正文截断 5 万字；专用后台线程规避 Windows 崩溃 |
| **business-service**（内部 Java/Java-mock 网关） | 客服订单/物流查询 | `infra/http/business_client.py` | `BUSINESS_SERVICE_URL` + `INTERNAL_API_TOKEN` + `X-Trace-Id` | 失败**不降级回 sandbox**，映射可读错误引导转人工 |
| **告警 webhook** | observability warn/error 推送 | `observability/alerts.py` | `ALERT_WEBHOOK_URL/TYPE`，未配置跳过 | 5s 超时即弃 |

## 9. 凭据与审批治理总口径

**三层凭据存放**（2026-09-29 实测 `.env` 变量名）：

1. **DB 治理（模型类唯一通道）**：§7 的 6 表；密钥 Fernet 加密落库、指纹+尾 4 位脱敏展示、15s 热刷新、无 env 回退
2. **`.env`（基础设施 + 非模型 SaaS）**：`PGPASSWORD` / `PG_READONLY_PASSWORD` / `REDIS_PASSWORD` / `JWT_SECRET` / `SECRETS_ENCRYPTION_KEY`（密文库主密钥）/ `TENCENT_LBS_KEY` / `TENCENT_LBS_SK` / `TENCENT_LBS_FRONTEND_KEY` / `AMAP_KEY` / `AMAP_SECRET` / `QWEATHER_API_KEY` / `ALERT_WEBHOOK_URL` / `API_KEY`（服务自身鉴权）/ `HF_TOKEN`（backend 内未发现消费点，待清理确认）。模型云 Key 与 SMTP 凭据**已不在 .env**
3. **审批门（副作用的人机确认，区别于 CS confirmation 链）**：`TOOL_APPROVAL_MODE`=required（默认，非法值也回落 required）| auto（仅本地调试）；指纹=sha256(tool|action|detail|user_id|tenant_id)（身份参与指纹防跨用户消耗）；单 TTL 600s；DB 不可用降级进程内存。**必须过门的写操作**：`email.send`、采集 `write_db`、竞品监控列表写、`export_csv`、管理端竞品写接口

**预算/配额**：租户级闭环（PG `budget_*` 6 表 + `model_price`，hard/soft/audit 三档，金额策略只存 PG）+ 请求级（`LLM_BUDGET_MODE` off/observe/enforce，默认 8 次调用/32k tokens/$0.50）+ 旅游 provider 日预算软停（`TRAVEL_PROVIDER_*_DAILY_BUDGET`，达限抛 `BudgetExhausted` 走缓存/降级不硬撞上游）。

**工具身份**：`get_tool_user_id()` 请求级 ContextVar（`tools/session.py`）；无上下文返回空串→审批门/预算门拒绝——MCP 直调与脚本必须先绑定身份。

## 10. 待补项（如实台账）

- **密钥泄漏扫描**：未发现 gitleaks/detect-secrets 类仓库级扫描器或 CI 配置（未确认是否有仓库外扫描）——建议列为发布门禁候选
- `HF_TOKEN` 在 `.env` 有变量名但 backend 无消费点——确认用途或清理
- 旅游 `ticket` provider：契约冻结、无真实适配器（J0-6 决策），health 恒 disabled
- 现行 embedding/rerank 实际生效源（代码默认 vs DB 专项绑定）未做 DB 实查——以管理端模型页实况为准

---

## 维护约定

- 域图增删专家 / 服务增删凭据变量 → 本文档对应表格同步更新（与 `docs/README.md` 维护约定同规则：被更新才盖章，验证日期不得落后于结构性变更）
- 「专家→工具」「capability→Skill」明细不在这里维护——以代码 import 与 `capabilities.yaml` 为准；本文只管**依赖的设计意图、治理与降级**
- 新增第三方服务必须补进 §8 表：用途 / 消费方 / 凭据变量 / 开关 / 降级五行缺一不可
