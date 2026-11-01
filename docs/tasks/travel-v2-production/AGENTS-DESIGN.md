# 旅游域智能体重构设计（满足原型的后端子智能体规划）

> 2026-11-01 · 依据：原型功能全清单（travel-platform-proto.html + 审查报告 + 对账矩阵 F01-F19）× 真实仓库现状（main@0d848a6 侦察）· 归属任务：docs/tasks/travel-v2-production/
> 关联：travel_commerce / travel_booking 两个子域图的收编重构与真实数据契约测试，见同目录 **DOMAIN-CONSOLIDATION-PLAN.md**（2026-11-01 用户确认：两域收编为旅游域子智能体）。
> 原则：**原型 = 功能验收基准**；沿用仓库既有 Router / Runtime / Domain Agent / Skill / Tool 边界，专家保持"业务编排、零 LLM 决策"（experts/base.py:20-22），不建平行实现。

---

## 一、现状盘点（重构的起点）

**域图**（backend/travel/graph_builder.py，12 节点，supervisor 动态路由）：
`slot_filler`（槽位+意图，纯规则 8 类，LLM 兜底默认关）→ `supervisor` → `poi / transit / weather / budget / risk` 五专家（零 LLM）→ `validator ⇄ repair` 循环 → `partial_replan` / `auxiliary_tasks`（每轮 ≤4 个附加实时查询）→ `reporter`。域注册于 domain_graph_registry；`/agent` 主助手不执行旅游域（domain_router.py:137-162）。

**工具**（双层治理）：`travel/tools/spec.py` 21 个 ToolSpec（副作用四级/确认门/Agent 白名单）+ `tools/travel/*.py` 本地实现；providers/travel/live 链账：weather.forecast=(tencent→qweather)、maps.route=(tencent_live→local_estimate)、poi.search=(seed 单源)。

**Skill**（无状态 capability，registry）：旅游域仅 `travel.poi_search`、`map.lookup`；`travel.plan` 刻意不注册（有状态多步流程归域图，防双实现）。

**MCP**：12306 MCP（默认关，唯一走 MCP 的 travel tool）、知乎 MCP（攻略检索）；平台 MCP 能力中心（mcp.* 动态 skill，DB 治理）；高德/腾讯为直连 REST 适配器（非 MCP）；**rollinggo 未接入**。

**Prompt 中心**：travel 类 7 键（llm_intent / turn_decision / planning_agent / v2.trip_ai_edit / slot_enrichment / clarification_renderer / reporter），DB 可热更。

**V2 层**（travel_v2/）：17 REST 端点 + ai-edits 状态机（clarify / modify / pending_resolution 裁决 / UNKNOWN）+ revisions/shares/exports + booking 确认门域（TTL/指纹/金额互检/CAS/对账，仅 fake provider）。

## 二、原型 → 智能体映射总表

原型 8 阶段流水线与 12 类对话场景对现有图的覆盖：

| 原型功能/阶段 | 承载智能体（现状） | 覆盖判定 |
|---|---|---|
| 槽位补全 + 三轮澄清选项 | Slot & Clarify（slot_filler + clarification_service） | ✅ 结构已有；clarification_options 前端消费已补（C2） |
| POI 候选检索（评分/门票/时长标注） | POI Curator（poi_expert → poi_service） | ⚠️ 门票价/游玩时长补全 = HANDOFF 实施层 P0 |
| 路线估算（地理聚类动线+市内交通耗时） | Route & Schedule（transit_expert → transit_service） | ✅ |
| 天气（出行日预报/降水分级/室内替换预案） | Weather Expert（weather_expert → weather_service） | ✅ 已对齐原型语义 |
| 预算求解（档位匹配→估算总额） | Budget Expert（budget_expert → budget_service） | ⚠️ 档位匹配缺住宿数据源支撑（依赖 F12 新智能体） |
| 风险（户外暴露评估→替换预案/闭园公告） | Weather（预案）+ Risk Expert（免责/溯源）+ 契约位 notice | ⚠️ notice.search 无数据源（契约位保留） |
| 排程校验（日负载/在途峰值拦截） | Validator & Repair（validate⇄repair 循环） | ✅；写侧对应 trip_write_validator |
| 行程单渲染（逐日卡/来源/unverified 披露） | Reporter（reporter_renderer） | ✅ |
| **住宿建议（档位 low/mid/high + 实时库存）** | ❌ 无 | **新增 Lodging Expert（本轮重构核心）** |
| **餐饮推荐（晚餐位/携老人/不辣偏好过滤）** | ⚠️ 工具在（map.merchant.search 高德）、编排缺 | **新增 Dining Scout 编排（扩展 auxiliary）** |
| **城际交通（返程窗口衔接/车次/机票）** | ⚠️ auxiliary.query_train（12306 MCP）+ flight 契约位 | **扩展 Intercity Scout 衔接校验** |
| **自动优选（餐饮/住宿/交通/天气择优→确认→批量选入）** | ❌ 无 | **新增 Auto Optimizer（travel_v2 服务，不进图）** |
| AI 对话编辑（改卡/替换/增删天/裁决） | Trip Editor（travel_v2 ai-edits 全状态机） | ✅ 已对齐（前端已接） |
| 预订确认门（TTL/金额互检/幂等） | Booking 域（机制完整，fake provider） | ⚠️ V2 工作台接线 + rollinggo BLOCKED |
| 域边界拦截（OUT_OF_SCOPE → 指引 /agent） | slot_filler 意图 + supervisor REPORT 出口 | ✅ |
| 会话关联/版本/恢复/ICS/分享 | travel_v2 服务层 | ✅ |

## 三、目标智能体清单（职责 / Skill / Tool / MCP）

> 命名沿用仓库习惯：域图节点=Expert（零 LLM 业务编排），LLM 理解/表达走 prompt 中心受控键，无状态能力才注册 Skill。

### A. 生成主线（现有 6 专家 + 1 新增，对应原型 8 阶段）

**A1. Slot & Clarify（槽位与澄清编排）** — `travel_slot_filler` 节点
- 职责：需求解析→槽位（目的地/天数/人数/预算/节奏/必去/回避/饮食）合并；槽位缺失时产出 `clarification_options`（结构化追问选项）；意图 8 类分类（OUT_OF_SCOPE 域边界拦截在此发生）；改单信号识别（is_new_run_query）。
- Skill：无（入口编排归域图）。Tool：无直接外部工具。LLM：`travel.slot_enrichment`（语义富化，STOP 1）+ `travel.llm_intent`（词表盲区，默认关）。
- MCP/Provider：无。输出：槽位状态 + clarification_options（→ 前端 C2 已消费）。

**A2. POI Curator（候选检索与筛选）** — `travel_poi_expert` 节点
- 职责：按"必去 × 节奏 × 预算"从候选池筛景点与餐饮位，产出行程骨架（地理聚类初排）；门票价/游玩时长补全（HANDOFF P0）；unverified 标注。
- Tool：`travel.search_poi`（现有，seed+腾讯补全）、`travel.poi.detail`（现有）。**新增数据源**：`AmapPOIProvider` 实现后链账改 `poi.search=("amap_live","seed")`（providers/travel/live/router.py:26-31）。
- Skill：`travel.poi_search`（已有）。MCP：无（腾讯/高德直连 REST）。
- 增强点：时长/票价字段落 PlaceFacts（opening_hours/ticket_price_cny/duration），支撑原型"停留 N 分钟 + unverified"标注。

**A3. Route & Schedule（路线估算与排程）** — `travel_transit_expert` 节点
- 职责：骨架→带时刻行程：order_pois 地理排序、schedule_day 日负载排布、estimate_leg 市内交通耗时（步行/公交/驾车/出租系数）。
- Tool：`travel.calculate_route`、`route.optimizer`（现最近邻；2-opt 后续位）。Provider：maps.route=(tencent_live→local_estimate)。
- MCP：无。输出：legs（selected_mode/duration_min/reliability）→ 支撑原型交通行与行内切换（set_leg_mode 写侧已有）。

**A4. Weather & Risk（天气与风险预案）** — `travel_weather_expert` + `travel_risk_expert` 两节点
- 职责：weather——出行日期与预报求交（区县锚点，OUT_OF_HORIZON 如实披露）、降水分级（mild 提示/severe 替换）、户外→室内替换（必去不换，repair_log 可解释）；risk——数据可信度、免责与溯源、**闭园/管控公告核对（依赖 notice 数据源）**。
- Tool：`travel.weather.query`、`travel.notice.search`（契约位）、`travel.event.search`（契约位）。
- Provider：weather.forecast=(tencent→qweather)。
- 增强点：notice/event 接真实数据源后，原型"闭园公告（契约位）"场景转真实。

**A5. Lodging Expert（住宿档位与库存）** — **新增 `travel_lodging_expert` 节点**（weather 后、budget 前：预算依赖住宿档位）
- 职责：按预算档位（low/mid/high）与行程位置匹配住宿建议（原型"住宿建议：东百坊巷 ¥388/晚"）；产出 nightly_price/check_in/out 候选（booking_status=not_booked）；喂给 Budget Expert 的住宿成本项；对话中响应"帮我找三坊七巷附近 500 以内的酒店"类 QUERY_DYNAMIC。
- Tool：`travel.hotel.search`（**新注册 ToolSpec**：implementation 先接高德商户检索兜底 map.merchant.search(hotel)，rollinggo MCP 就绪后切直连）；复用 `map.merchant.search`。
- MCP：**rollinggo MCP**（端点 https://mcp.rollinggo.cn/mcp，Bearer；经平台 MCP 能力中心注册为 `mcp.rollinggo__*` capability，GrantRequest.product_key 加 travel）——**BLOCKED：库存权限待上游开通**。
- 治理：查询类 READ 不需确认；"预订"动作不在此专家（转 Booking 域确认门）。

**A6. Budget Expert（预算求解）** — `travel_budget_expert` 节点
- 职责：按档位核算 tickets/meals/lodging/transit 汇总预估（"只算不判"）；响应"预算砍到 1200 行不行"（calculate_budget→降档改写建议）。
- Tool：`travel.calculate_budget`。输入新增：Lodging Expert 的住宿档位成本。
- 输出：totals.estimated_cost + health.issues 的 BUDGET_* 警示（前端已消费）。

**A7. Validator & Repair（排程校验与修复）** — `travel_validator` ⇄ `travel_repair` 节点
- 职责：日负载/营业时间/在途峰值校验；失败触发 repair 循环（原型"在途峰值校验拦截→裁决"的生成侧）；写侧同语义由 trip_write_validator 承担（remove_day/move_item 校验）。

**A8. Reporter（行程单渲染）** — `travel_reporter` 节点
- 职责：逐日行程卡渲染 + 来源/置信标注（amap:live/unverified）+ change_summary + 披露（llm 受控改写，失败回确定性模板）。
- LLM：`travel.reporter`。

### B. 查询子智能体（auxiliary 扩展 + 新编排，对应原型对话场景）

**B1. Dining Scout（餐饮上下文推荐）** — 扩展 `travel_auxiliary_tasks`（不新增节点）
- 职责：按行程上下文（时段空档/当日位置/用户偏好"不辣、携老人、清淡"）推荐餐饮并给选入建议（原型 food 场景："今晚带爸妈吃点什么？不要辣的"→ 从闽菜挑两家加晚餐位）。
- Tool：`map.merchant.search`（高德，现有，food capability）；偏好来自 `travel.memory.search`（现有）。
- 新增编排：候选→偏好过滤（avoid 辣/清淡）→"加入第 N 天晚餐位"建议卡（选入走 selections/meals 既有端点）。
- Skill：新增 `travel.food_search`（无状态包装，供主图/其他域复用）。

**B2. Intercity Scout（城际交通衔接）** — 扩展 `travel_auxiliary_tasks`
- 职责：返程窗口匹配（原型"周日下午怎么回厦门最方便"→ Day2 返程窗 14:00-18:00 拉车次）、班次与行程衔接校验（末站离开时间 vs 返程发车）；机票对比（原型 flight 场景）。
- Tool：`travel.train.search`（现有，**12306 MCP**，默认关）、`travel.flight.search`（契约位：rollinggo /mcp/flight 目标态）。
- MCP：12306 MCP（已接）；rollinggo flight MCP（规划项）。
- 披露：12306 非官方聚合无 SLA 的既有注记保留。

**B3. Local Context Scout（节事与公告）** — `travel.event.search` / `travel.notice.search`（契约位保留）
- 职责：行程日期 overlapping 的展览/活动；闭园/管控/防汛公告（原型 event/notice 场景）。数据源接入后排为真实（现阶段契约位+如实披露"暂无数据源"）。

### C. 修改与优化（V2 层，已对齐 + 一项新增）

**C1. Trip Editor（对话编辑专家）** — travel_v2 `trip_ai_edit_service`（已实现）
- 职责：自然语言编辑→意图授权（clarify/modify/unsupported）→ TripEditOperationV2（add/remove/update/move/reorder/replace/set_leg_mode/add_day/remove_day）→ trip_write_validator 校验→落 Revision；冲突时 pending_resolution 裁决（allowed_resolutions）；幂等键+UNKNOWN。
- LLM：`travel.v2.trip_ai_edit`。Tool：不需要外部工具（操作在 TripDocument 上）。✅ 原型 A4/A4b/编辑卡全链路已对齐。

**C2. Auto Optimizer（自动优选）** — **新增 travel_v2 服务端点（不进域图）**
- 职责：对正式行程按上下文聚合查询（午餐位/晚餐位/住宿/交通/天气）→ 按"评分×距离×预算×天气"择优 → 产出建议清单（每类含备选）→ 用户确认后批量选入（复用 selections/meals|lodgings|intercity_trains 端点，逐条幂等）。
- 形态：`POST /travel/v2/trips/{id}/auto-optimize`（READ 编排+建议；"选入"仍走既有单条 selection 写端点，保持写路径唯一）。前端 A6 入口 + 确认卡。
- Tool：复用 B1/B2/A5 查询能力（map.merchant.search / hotel.search / travel.train.search / travel.weather.query）。
- 治理：编排本身 READ；批量选入继承每条的幂等与写状态机。

**C3. Booking 确认门域**（收编为旅游域预订子智能体，事务机制原样保留）
- 职责：酒店预订 Quote→确认（TTL/指纹重验/金额互检/P0 复核门/CAS/幂等账本/对账）。原型 B7 全语义已具备。
- 形态修订（2026-11-01 用户决策）：原 `travel_booking` 独立域图注册与聊天子图壳删除，事务机制（executor/store/state/identity/revalidate/webhook/reconciliation/recovery）原样保留在 `backend/travel/booking/`，对外改为 **travel_v2 REST 三段端点**（quote/confirm/status）——见 DOMAIN-CONSOLIDATION-PLAN.md §4。
- 待接线：V2 工作台住宿卡→确认门 UI；真实供应商 rollinggo（BLOCKED，当前 fake + fail-closed）。

### D. 支撑能力（非智能体，共享组件）
- **Knowledge & Memory**：`travel.retrieve_knowledge`（RAG：pgvector+bm25）、`travel.memory.search/save`（L1-L3 偏好，"不辣/携老人"过滤的来源）、知乎 MCP 攻略（city_guide/inspiration）。
- **Export/Share**：calendar.export（ICS 服务端导出）、share（令牌匿名只读）——travel_v2 服务层已实现。
- **治理组件**：写状态机（travelV2WriteState 对应后端事务回执）、幂等账本、trip_write_validator、tool_failure_policy（12306/AMAP/QWEATHER 失败语义与 realtime_disclosure）。

## 四、Skill 层规划（无状态 capability 才注册）

| Skill | capability | 现状 | 动作 |
|---|---|---|---|
| TravelPoi | travel.poi_search | 已有 | 保留（AmapPOIProvider 就绪后内部换源） |
| MapLookup | map.lookup | 已有 | 保留 |
| **FoodSearch** | travel.food_search | 无 | 新增：包装 map.merchant.search(food)，主图/其他域可查餐饮 |
| **HotelSearch** | travel.hotel_search | 无 | 新增：包装 hotel 数据源（高德兜底→rollinggo 切换），V2 工作台与主图共用 |
| travel.plan | - | 刻意不注册 | 维持不注册（有状态多步归域图，防双实现——既有决策） |
| mcp.*（rollinggo/12306） | mcp.<conn>__<tool> | 动态注册机制已有 | rollinggo 权限开通后经能力中心登记 |

## 五、MCP 与数据源规划

| 能力 | 通道 | 现状 | 动作 |
|---|---|---|---|
| 火车票 | 12306 MCP（drfccv/mcp-server-12306） | 已接，默认关 | 保持"非官方聚合无 SLA"披露；灰度开启 |
| 酒店库存/报价 | rollinggo MCP | **未接入** | 能力中心注册 + GrantRequest.product_key=travel；**BLOCKED 权限** |
| 机票 | rollinggo /mcp/flight | 契约位 | 随 rollinggo 接入排期 |
| 景点 POI | 腾讯 LBS + **AmapPOIProvider（新增）** | seed 单源 | 实现 provider + 链账 ("amap_live","seed")；key 走 env |
| 美食/商户 | 高德 REST 直连 | 已有 | 保持直连（低延迟；非 MCP 治理面） |
| 天气 | 腾讯→和风链 | 已有 | 保持 |
| 攻略 | 知乎 MCP + RAG | 已有 | 保持 |
| 节事/公告 | 契约位 | 无源 | 数据源评估后接入 |

凭据纪律：rollinggo/和风 key 一律 env/管理端，**不入库不入代码**（用户桌面 tool mcp.txt 用后删除）。

## 六、重构路径（分阶段，最小改动）

| 阶段 | 内容 | 涉及 |
|---|---|---|
| R1（工具/数据源层，P0） | AmapPOIProvider + 链账；`travel.hotel.search` ToolSpec 注册（高德兜底）；POI 门票/时长落 PlaceFacts；notice/event 保持契约位 | tools/、providers、spec.py+守护测试、capabilities.yaml 连带 |
| R2（图节点，P0） | 新增 `travel_lodging_expert` 节点（supervisor stage 表插入 lodging，weather 后 budget 前）；auxiliary_tasks 扩展 query_food 与返程衔接校验 | supervisor.py stage 枚举+decide、graph_builder、结构回归测试 |
| R3（V2 服务，P0） | Auto Optimizer 端点 + 批量选入；V2 工作台预订确认门接线 | travel_v2/services+api、前端入口 |
| R4（Skill 层，P1） | travel.food_search / travel.hotel_search 注册；rag_smoke 定向清单同步 | skills/、capabilities.yaml、.github/workflows/rag_smoke.yml |
| R5（外部依赖，BLOCKED） | rollinggo MCP 接入（酒店+机票）、12306 灰度开启 | config/mcp.py、能力中心 |

## 七、治理与测试要求（仓库纪律的连带）

1. 每个新 ToolSpec 进 `spec.py` 必须同步守护测试（副作用分级/确认要求/白名单断言）；
2. capabilities.yaml 与 spec.py "不重复不冲突"的既有约定保持；
3. 新链账（poi.search 换源）按 tool_failure_policy 的失败语义+realtime_disclosure 补失败用例；
4. supervisor stage 表改动必须跑 `stage_targets()` 结构回归；
5. 新功能入 rag_smoke 定向清单（AGENTS.md 第 8 条）；
6. LLM 键一律走 prompt 中心注册（新增键进 registry + defaults YAML + lock）。

## 八、一句话结论

**满足原型不需要推倒重来**：现有"slot_filler + 五专家 + validator/repair + reporter + auxiliary"已覆盖原型 8 阶段中的 7 个与 12 类对话场景中的 9 类；真正的重构增量是——**新增 Lodging Expert（图节点）与 Auto Optimizer（V2 服务）两个智能体，扩展 Dining/Intercity 两个查询编排，补 AmapPOIProvider 与 rollinggo 两个数据源（后者 BLOCKED），其余均为前端消费与接线**。
