# Phase 3 迁移审计报告 — Research / Planning / Optimization Agent 边界建立（只读，未改代码）

> 依用户指令：Phase 3 施工前先审计，**确认前不改任何代码**。基线：Phase 2（`242430d` / `df36bca`）。
> 审计对象：`backend/travel/experts/`（base 101 + poi 219 + transit 323 + weather 294 + budget 61 + risk 106 = 1104 行）+ 门禁层边界 + 全仓 Provider 调用点。
> 结论先行：**五专家是"节点编排 + 纯函数算法"两层结构，算法本体可干净下沉 service；真正的硬约束是 STOP F 冻结的执行框架（run_expert_safely/遥测形态逐字节冻结）与 repair→transit 的交叉依赖**。Provider 直连点共 3 处，全部可随本次边界建立收敛进 service 层（ProviderRouter 本体按计划仍属 Phase 4）。

---

## 一、当前五专家职责分析

### 1.1 共同结构（先说清楚，逐个分析才不失真）

每个专家文件 = **纯函数算法层**（可单测、零 IO 或仅经 Tool）+ **图节点函数**（`*_expert_node`：load_brief → 编排 → `run_expert_safely` 包装 → state 组装 + expert_history 追加）。

**不能移动（全局，适用于全部五专家）**：
- `experts/base.py` 的 `run_expert_safely` / `TravelExpertResult` / `TravelExpertStatus` / `TravelExpertType`——**STOP F（c2dc0a4）公开契约与遥测形态逐字节冻结**（签名/字段/status 枚举/`travel_expert_{name}` span 软失败 + start/done 日志，经 `core/node_runtime` 的 TravelExpertHooks）。Phase 3 一切改动必须在 `run_expert_safely` 包装**之内**发生，节点仍以五专家名调用它；
- 各节点的 `expert_history` 追加与 state 组装（遥测消费面，本阶段不动，`agent_history` 双写推迟）；
- `graph_state` 的 load_brief/load_itinerary/save_itinerary/data_snapshot_version；
- `models/` 契约、`planning.py`（resolve_must_go，poi 专家与 validator 共用）、`config/travel.py` 阈值；
- 节点级 `quality_metrics` 遥测调用（STOP I6 结构化事件，软失败包裹，留在节点体）。

### 1.2 poi expert（`experts/poi.py`，219 行）

| 职责 | 位置 | 归属 |
|---|---|---|
| 候选池检索：`search_poi`（Tool，纯函数读种子）+ 必去项腾讯补全（`get_place_provider` + `resolve_missing_places`，**Provider 直连**） | 节点 `_run` 前半 | **Research Agent**（数据获取面） |
| 骨架分配算法：`build_skeleton`（必去置顶→热度→均衡→地理聚类，双容量约束）+ `_build_notes`（取舍透明化）+ `Skeleton` dataclass | 49-131 行纯函数 | **Planning Agent**（经 poi_service） |
| must_go 三态解析（`resolve_must_go`，STOP I1 唯一产生点） | 节点内 177 行 | 调用保持（planning.py 原位） |
| 节点编排（state 组装/遥测/无候选如实披露） | `poi_expert_node` | 图节点（保留） |

判定：**poi 文件天然横跨 Research（检索）与 Planning（骨架）两个 Agent 边界**——这是五专家中唯一的跨边界者。service 化时 `poi_service.py` 同时持有 `retrieve_candidates`（检索+补全）与 `build_skeleton`（分配）两段，research_agent 与 planning_agent 各自暴露一侧，**poi 节点依序调用两个 Agent**（与现数据流一致：先检索后分配）。

### 1.3 transit expert（`experts/transit.py`，323 行）

| 职责 | 位置 | 归属 |
|---|---|---|
| 排程算法全家：`order_pois`（最近邻+开门起点）、`schedule_day`（时刻轴+午餐占位+TransitLeg，内部 `estimate_leg` 经 routing Tool 触达 live 通道）、`build_itinerary`（空白天压缩）、`rebuild_days`（**唯一重排实现**，repair 与 weather 复用） | 47-207 行纯函数 | **Optimization Agent**（经 transit_service） |
| 路段预热：`_prefetch_day_legs` → `tools/travel/live_map.prefetch_legs`（**腾讯传输层直连**） | 节点内 210-229 | Optimization（收敛进 transit_service） |
| 版本盖章 `stamp_version`（出生回答"基于哪个需求/数据/原因"） | 节点内 266-271 | 图节点编排（保留，版本链是 checkpoint 契约面） |
| 死代码 `reschedule_after_repair`（308-323，审计已确认无调用方） | — | 随迁 transit_service 并登记清理（Phase 8 删） |

判定：**transit 全部归 Optimization**。注意 `schedule_day` → `estimate_leg`（Tool→Provider 注入式）是**合规方向**（Service→Tool→Provider），不算违反点。

### 1.4 weather expert（`experts/weather.py`，294 行）

| 职责 | 位置 | 归属 |
|---|---|---|
| 预报获取与七态降级映射：`fetch_forecast`（**`get_weather_provider` 直连**，TIMEOUT/配额/NOT_FOUND→可读原因） | 66-91 行 | **Research Agent**（数据获取；经 weather_service） |
| 预报解析：`is_bad_weather` / `bad_weather_dates`（纯函数） | 60-108 | Research（解析）/ Optimization（使用）——放 weather_service，两 Agent 共用 |
| 坏天气适应：`plan_weather_swaps`（户外→室内替换，必去永不换，内部 `rebuild_days` 重排 + `estimate_cost`） | 111-193 纯函数 | **Optimization Agent**（Plan B 执行侧） |
| 编排：前置闸（开关/无行程/无日期）+ 预报窗口交集判定（OUT_OF_HORIZON 如实披露）+ repair_log 组装 | 节点 | 图节点（保留） |

判定：weather 与 poi 一样横跨边界——**取数归 Research、换点重排归 Optimization**，weather 节点依序调用两个 Agent。**跨专家依赖**：`plan_weather_swaps` import `experts.transit.rebuild_days`——service 化后改为 weather_service → transit_service（同层依赖，方向合法），transit_service 成为 `rebuild_days` 唯一真身。

### 1.5 budget expert（`experts/budget.py`，61 行）

| 职责 | 位置 | 归属 |
|---|---|---|
| 费用核算：`estimate_cost`（Tool，城市档位）填充 CostBreakdown；无预算时如实披露"未做预算校验" | 节点 | **Optimization Agent**（经 budget_service，只算不判纪律不变——超支判定在 validator 轴四） |

判定：**全归 Optimization**，最薄的一个。无 Provider 直连。

### 1.6 risk expert（`experts/risk.py`，106 行）

| 职责 | 位置 | 归属 |
|---|---|---|
| 溯源与免责：`assess_risks`（纯函数，seed 来源警告 + 三条能力边界声明） | 32-48 | **Research Agent**（经 risk_service） |
| 知识库摘录：`retrieve_travel_knowledge` + `build_knowledge_query`（**内部 RAG Tool**，软失败，非外部 Provider——不算违反点） | 节点内 66-81 | Research（检索面收敛进 risk_service） |
| sources/knowledge_refs 进行程契约 | 节点 | 图节点（保留） |

判定：**全归 Research**。

### 1.7 归属总表

| 专家 | Research | Planning | Optimization | service 承载 |
|---|---|---|---|---|
| poi | 检索+必去补全 | 骨架分配 | — | poi_service（两段） |
| transit | — | — | 全部（排程/重排/预热） | transit_service |
| weather | 预报获取+解析 | — | 坏天气替换重排 | weather_service |
| budget | — | — | 费用核算 | budget_service |
| risk | 溯源+知识摘录 | — | — | risk_service |

## 二、目标结构设计

```
backend/travel/
├── agents/                            # Phase 2 已建包
│   ├── research_agent.py              # 【新增】ResearchAgent：候选检索(经poi_service)/
│   │                                  #   天气预报获取(经weather_service)/溯源与知识摘录(经risk_service)
│   ├── planning_agent.py              # 【新增】PlanningAgent：日程骨架分配(经poi_service)
│   └── optimization_agent.py          # 【新增】OptimizationAgent：排程与重排(经transit_service)/
│                                      #   坏天气适应(经weather_service)/费用核算(经budget_service)
├── services/                          # Phase 2 已建包（requirement_service 已在）
│   ├── poi_service.py                 # 【新增】检索+必去补全 / build_skeleton 算法真身
│   ├── transit_service.py             # 【新增】order_pois/schedule_day/rebuild_days/build_itinerary
│   │                                  #   真身 + 路段预热（rebuild_days 全域唯一真身）
│   ├── weather_service.py             # 【新增】fetch_forecast（七态映射）/is_bad_weather/
│   │                                  #   bad_weather_dates/plan_weather_swaps 真身
│   ├── budget_service.py              # 【新增】费用核算真身
│   └── risk_service.py                # 【新增】assess_risks 真身 + 知识摘录封装
├── experts/                           # 【改造为"节点+兼容入口"（复刻 Phase 2 slot_filler 模式）】
│   ├── base.py                        # 【零改动】STOP F 冻结层
│   ├── poi.py / transit.py / weather.py / budget.py / risk.py
│   │                                  # 节点函数保留 + 纯函数 re-export（自 services）；
│   │                                  # 节点体改调 Agent（run_expert_safely 包装不变）
│   └── __init__.py                    # 【零改动】全部符号经 re-export 链继续可解析
├── validator.py / repair.py / reporter.py   # 【零改动】门禁层（§四）
└── graph_builder.py                   # 【零改动】五节点布线不动
```

**三层职责（冻结口径）**：

| 层 | 负责 | 禁止 |
|---|---|---|
| **Graph Node**（experts/*_node，5 个保留） | state 读写、load/save 契约转换、`run_expert_safely` 包装、expert_history/遥测、门禁装配 | 业务算法；直接 import providers/live_map（§三违反点全部消除） |
| **Agent**（research/planning/optimization） | 能力编排与语义命名（"检索候选""排程""坏天气适应"）、跨 service 组合的单一入口、无状态 | 直接 import providers/tools（只经 service）；import graph_builder/orchestration；互相调用 |
| **Service**（poi/transit/weather/budget/risk） | 算法真身（逐字搬运零改动）；**唯二允许触达 Tool/Provider facade 的层**（现有 routing/cost/live_map/knowledge 调用全部收拢于此） | state 读写；import experts/graph_builder；LLM |

依赖方向：`Node → Agent → Service → Tool/Provider`，单向无环。同层例外：weather_service → transit_service（rebuild_days 复用，与现状 plan_weather_swaps→transit 的依赖同构，只是换到 service 层）。

## 三、Provider 审计

### 3.1 当前违反点（Agent/Node 层直接触达外部数据）

| # | 位置 | 现状调用链 | 级别 |
|---|---|---|---|
| V1 | `experts/poi.py:152-163` | 节点 → `providers.travel.live.get_place_provider` → `tencent.resolve_missing_places`（腾讯 Place） | 违反（Node→Provider） |
| V2 | `experts/weather.py:74-77` | 专家函数 → `providers.travel.live.get_weather_provider().forecast_payload`（腾讯主/和风备） | 违反（Agent层→Provider） |
| V3 | `experts/transit.py:219-227` | 节点 → `tools.travel.live_map.prefetch_legs`（腾讯路线传输封装，含节流/熔断） | 违反（Node→传输 Tool） |

**合规现状（不属违反，保持）**：`schedule_day` → `routing.estimate_leg`（Tool 内注入 provider，Service→Tool→Provider 方向）；`risk` → `knowledge`（内部 RAG Tool，非外部 Provider）；commerce/booking 域的 provider 契约使用（冻结交易域，不在本阶段范围）。

### 3.2 迁移方案（Phase 3 范围 = 调用点收敛；Router 抽象 = Phase 4）

- V1/V2/V3 的调用代码**原样搬入对应 service**（poi_service.retrieve_candidates 内补全 / weather_service.fetch_forecast / transit_service 的预热随 build_itinerary 前置），Agent 与 Node 层从此**零 providers/live_map import**（Commit C 静态扫描锁定）；
- Agent 拿到的是 service 返回的**已映射结果**（weather：`(forecast|None, 降级原因)` 二元组语义不变）——"Agent 不知道腾讯"在边界层即刻成立；
- **ProviderRouter（primary→fallback→stale→七态）仍是 Phase 4**：本阶段不做路由抽象、不改 TTL/超时/缓存配置（qweather 备用源 parity 等 Phase 4 验收）。Phase 3 只保证"调用点全部位于 service 层"，使 Phase 4 的 Router 插入点唯一且明确；
- Evidence 附加（v4 §10 Phase 3 项：候选池/天气/知识三处）：**建议随 Phase 4**（Evidence 的 verified_at/expire_at 依赖 Provider TTL 与 Router 语义，Phase 3 先做会返工）——列为本报告决策点 D3。

## 四、Validator / Repair / Reporter 边界确认

| 门禁 | 现状依赖 | Phase 3 处置 |
|---|---|---|
| `validator.py`（655 行） | import `planning.resolve_must_go`；被 repair/reporter/评测消费 | **零改动、独立保留**。v4 计划的 3 项新 warning（POI_UNVERIFIED/SOURCE_STALE/PREFERENCE_VIOLATION）建议随 Evidence 推迟 Phase 4（决策点 D3）——SOURCE_STALE 依赖 Evidence 字段，Phase 3 无从判起 |
| `repair.py`（481 行） | **`from backend.travel.experts.transit import rebuild_days`（repair.py:26）**——与 transit 的唯一交叉 | **零逻辑改动、独立保留**。rebuild_days 真身迁 transit_service 后，experts/transit.py re-export 使 repair 的 import 语句**零改动继续工作**（Phase 2 兼容模式复刻）；Phase 8 收口时统一切 service 路径 |
| `reporter.py`（303 行） | import `slot_filler.build_clarification`（与五专家无关） | **零改动、独立保留** |

**禁止 Agent 吞并**落为 Commit C 边界测试：三个门禁模块不得被任何 agent/service import（静态断言）；门禁节点在 graph_builder 的注册与执行顺序（poi→transit→weather→budget/risk→validate→repair→report）零变更。

## 五、最小修改方案

**新增（8+1 文件）**：
| 文件 | 内容 |
|---|---|
| `services/poi_service.py` | search_poi 编排 + 必去补全（V1 收敛）+ build_skeleton/_build_notes/Skeleton 真身 |
| `services/transit_service.py` | order_pois/schedule_day/rebuild_days/build_itinerary/_prefetch_day_legs（V3 收敛）/reschedule_after_repair（死代码随迁登记） |
| `services/weather_service.py` | fetch_forecast（V2 收敛）+ is_bad_weather/bad_weather_dates + plan_weather_swaps（rebuild 改调 transit_service） |
| `services/budget_service.py` | 费用核算封装 |
| `services/risk_service.py` | assess_risks 真身 + 知识摘录封装 |
| `agents/research_agent.py` | ResearchAgent（retrieve_candidates / fetch_forecast / assess_risks+知识摘录） |
| `agents/planning_agent.py` | PlanningAgent（build_skeleton） |
| `agents/optimization_agent.py` | OptimizationAgent（build_itinerary+预热 / plan_weather_swaps / estimate_cost） |
| `tests/travel/test_agent_service_boundary.py`（Commit C） | 契约+边界测试（§六 Commit C） |

**修改（5 文件，复刻 Phase 2 兼容模式）**：五个 expert 文件——纯函数体搬 service、节点体改调 Agent、原符号 re-export（`experts/__init__.py` 与 `repair.py:26` 的 import **零改动**经 re-export 链解析）。

**不需要 shim 的部分**：`experts/__init__.py`/`graph_builder.py`/`repair.py`/`validator.py`/`reporter.py`/主图全部（re-export 链即 shim）。

**测试影响**：存量测试经 `experts/__init__` 或专家模块 import 纯函数（test_pool_contract 的 build_skeleton、test_weather_expert 的 is_bad_weather 等）→ re-export 承接**零改动**；节点行为测试（travel_graph/scenarios/p0_mvp/quality_metrics/persistence）→ 编排语义不变**零改动**；金标 34 条 → 算法逐字搬运**必须零回退**（本阶段最高验收门槛）。预计回归面 ≈ 550 例（Phase 2 的 650 + provider_layer/pool_contract/weather_expert 等专家专项）。

**风险**：
| # | 风险 | 级 | 缓解 |
|---|---|---|---|
| 1 | STOP F 遥测形态漂移（span/日志/expert_history） | 高 | run_expert_safely 包装留在节点、调用参数不动；node_runtime parity 40 例每 commit 必跑 |
| 2 | 算法搬运手误（540+ 行纯函数） | 高 | 逐函数原样剪切 + 金标 34 条零回退门 + parity 测试（同输入同输出） |
| 3 | weather_service→transit_service 跨 service 依赖形成环 | 中 | 单向（weather→transit），transit 不回依赖 weather；boundary 测试断言 import 方向 |
| 4 | 节点改调 Agent 后 expert_history/notes 顺序漂移 | 中 | 节点组装代码逐字保留，只有"算法调用"一行换成 Agent；分块回归盯 travel_graph/quality_metrics |
| 5 | reschedule_after_repair 死代码迁移引发误用 | 低 | 随迁 + docstring 标记死代码 + Phase 8 删除清单 |
| 6 | 并行会话冲突 | 中 | pathspec 双重限定；三 commit 各自可独立 revert |

## 六、Phase 3 实施顺序（三 Commit，各自测试+验收+commit）

| Commit | 内容 | 验收门 |
|---|---|---|
| **A Service 边界建立** | 5 个 service 文件落真身（算法逐字搬运）；5 个 expert 文件改"节点+re-export"；repair/import 零改动验证 | 存量分块全绿（≈550 例）+ 金标 34 条零回退 + node_runtime parity 40 例 + 一致性三门 |
| **B Agent Wrapper 接入** | 3 个 Agent 文件；5 个节点体改调 Agent（编排/遥测/expert_history 逐字保留） | 同上回归门 + 节点行为 parity（同 state 输入 → 同 update 输出断言） |
| **C Contract 测试** | `test_agent_service_boundary.py`：①依赖方向静态扫描（agents 零 providers/tools import；services 零 experts/graph_builder import；validator/repair/reporter 无人 import）；②rebuild_days 单一真身断言；③parity（Agent 路径 vs 旧符号路径同输入同输出）；④五节点注册与顺序冻结断言；⑤reschedule_after_repair 死代码标记 | 全绿 + Phase 3 Completion Report（八项格式） |

## 七、待确认决策点

| # | 问题 | 建议 |
|---|---|---|
| D1 | **五图节点本阶段保留**（poi/transit/weather/budget/risk 节点不动，supervisor stage 机零改动；Research/Planning/Optimization 以 Agent 边界存在）——图收敛（3 节点 + stage 重映射）延后 | 建议保留（复刻 Phase 2"边界先行、门牌不动"模式；图收敛与 supervisor 意图层强耦合，宜与 Phase 5/6 意图收编同批设计） |
| D2 | planning.py 保持原位（新结构无 planning/ 包，Phase 2 时"延到 Phase 3"的迁移动机消失；其消费方 validator/poi 专家/评测不受边界建立影响） | 建议原位不动 |
| D3 | v4 计划中 Phase 3 的 Evidence 附加 + validator 3 项 warning **推迟到 Phase 4**（与 ProviderRouter/TTL 同批，避免字段语义返工） | 建议推迟 |
| D4 | services 按 expert 命名（poi/transit/weather/budget/risk_service.py，替代 v3 的 candidate_pool/skeleton/... 拆分命名） | 按你给的结构执行（目录划分不冻结，1:1 命名迁移风险最低） |
