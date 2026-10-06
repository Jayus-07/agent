# AI Runtime — LangGraph 编排细节

> 本文承接 README 的编排层细节：主图节点职责、垂直域图、客服锁域、Runtime 语义收口、跨轮状态契约。
> 主图事实源 = `backend/orchestration/graph/builder.py`；域图注册事实源 = `backend/domains/__init__.py`（runtime 元数据 = `backend/orchestration/domain_registry.py`）；
> capability→Skill 映射唯一事实源 = `backend/orchestration/router/capabilities.yaml`。

## 主图（LangGraph StateGraph）

固定 **9 个核心节点**（2026-09-25 口径对齐 `builder.py:140-151`），顺序与命名不得随意改动；Skill 节点与域图节点由自动发现加入，**不得手写进 builder**：

```
START → router ─┬─ 客服域锁（domain_hint=cs，跳过判域/灰度）→ 客服域图 → END
                ├─ 客服预过滤命中（CS_ENABLED + 灰度）     → 客服域图 → END
                ├─ 旅游预过滤命中（TRAVEL_ENABLED）        → 旅游域图 → END
                ├─ 选品预过滤命中（SELECTION_FUNNEL_ENABLED）→ 选品漏斗域图 → END
                └─ RoutingEngine（entry_gate → domain → intent → capability → policy → route_decision）→ route_selector
                      ├─ direct   → tool_selector → skill_executor → reporter → END
                      ├─ workflow → workflow_executor → reporter → END
                      ├─ general_chat（寒暄/能力咨询）→ 主 LLM 直答 → END
                      ├─ clarify  → 澄清 reporter → END
                      ├─ domain_graph → 对应有状态域图 → END
                      └─ plan     → planner → critique → supervisor（Send 并行）
                                                    → reporter → END
```

### 节点职责边界

| 节点 | 职责边界 |
|------|----------|
| Router | 入口门禁（GraphRunner Input Guard）之后执行域预过滤（客服域锁 / CS / 旅游 / 选品 / 商务 / 预订，纯正则）+ RoutingEngine 六阶段固定序 `entry_gate → domain → intent → capability → policy → route_decision`（DomainRouter / IntentRouter / CapabilityRouter / ExecutionModeResolver 各只出一类决策）；规则、向量和 LLM 只是证据提供者；基础设施故障结构化四分类 `domain_classifier_degraded / vector_index_mismatch / vector_unavailable / llm_failure`（向量故障不在请求内重建索引） |
| tool_selector | direct 路径首站：Function-Call 门控选工具 + 填参；失败/快路径直通零开销；**首候选分数兜底已删除（2026-10-06 Tool Governance Runtime，`b6b7973`）**——FC 失败/模型拒绝（`fit=no_match` 或 declined）/单候选无明确 fit/selector 预算耗尽一律 `clarify`，不再按候选分数自动执行（旧 `TOOL_SELECTOR_TOP_CANDIDATE_*` 行为与 `source=router_top_candidate` 已废弃）；模型不可提供 auto 参数；选出的 capability 执行必须经 `core/tool_governance/guard.py::GovernanceRuntime` 门（候选/域/intent_fit/Schema/确认/预算/限流熔断） |
| Planner | 只做任务拆解 → Capability DAG，**禁调 Tool/Skill/DB** |
| Critique | 规则校验优先，仅 anomaly 才调 LLM；含计划深度上限（≤8） |
| Supervisor | 纯规则 DAG 调度，`Send[]` 并行 + 注入 `previous_outputs` |
| skill_executor / workflow_executor | 单能力直调 / 工作流执行，均绕过 Planner |
| general_chat | 寒暄/能力咨询直答（2026-09-22 接线）：主 LLM 直连节点，不进 Planner/Skill 链路 |
| Reporter | `step_results` → Markdown + 引用格式化 |

**主链路 LLM 决策节点仅 4 个**（Planner / Critique / Reporter / general_chat 直答）；RoutingEngine 的 IntentRouter 是结构化分类阶段，不新增 LLM 调用，只消费规则/域判断证据；仅在域分类或能力索引故障时走显式 fallback，Supervisor 本身是纯规则调度器。

## 垂直域图（Domain Graph）

**语义口径（STOP E，2026-09-29）**：架构上只有 **3 个顶级业务域**——Customer Service / Travel / Selection——Travel 含 **planning / commerce / booking 三个子流**。3 个顶级域落地为 **5 个物理域图**（commerce / booking 保留独立生命周期与独立开关，物理不合并）。

5 个物理域图的**代码默认全关**（`CS_ENABLED` / `TRAVEL_ENABLED` / `SELECTION_FUNNEL_ENABLED` / `TRAVEL_COMMERCE_ENABLED` / `TRAVEL_BOOKING_ENABLED` 均为 `false`，新 clone 拿到的是这个）；当前仓库根 `.env` 客服 / 旅游 / 选品三个已打开。

| 域图 | 开关 | 节点序列 |
|---|---|---|
| 客服 | `CS_ENABLED` | `state_loader → pending_handler → cs_supervisor → 5 专家（knowledge/query/action/complaint/handoff）→ cs_reporter`；`cs_supervisor` 承担 handoff 拦截、循环上限、LLM 兜底；v2 决策链（`CS_DECISION_V2`）在守卫之后、意图路由之前有 **L4.5 分诊直出**：寒暄→`chat_fallback` 一次 LLM 人设（`CS_CHAT_FALLBACK_ENABLED`）、出域→固定话术零 LLM（`CS_WINDOW_STANDALONE`；v1 回退路径不接出口）；`pending_handler` 在 need_info 补槽期**优先放行显式转人工**（命中 handoff 触发即释放 pending 回 supervisor 重分诊，审计 `need_info_handoff_escape`，2026-10-05 a65a167——追问不再吞掉转人工诉求） |
| 旅游（Travel · planning 子流） | `TRAVEL_ENABLED` | `travel_slot_filler → travel_supervisor → poi/transit/budget/risk/weather 五专家 → travel_validator →（未过）travel_repair →（已有行程逐条改单）travel_partial_replan → travel_reporter`（11 节点，2026-10-07 `4ddc3c3` 增 `travel_partial_replan`：局部重规划只动被点名天与条目+强制重验证，见「旅游会话意图」节）；validator 纯规则零 LLM 零 IO，只判定不修改（修复在 repair），四轴 = 时间/地理/体力/预算；error 级违反阻塞交付；局部修复只动被点名的天与条目，用户点名必去条目永不被静默丢弃（`kept_required`） |
| 选品漏斗 | `SELECTION_FUNNEL_ENABLED` | prefilter 已接线（`router_node` 内与旅游同层，2026-09-17）；仅受开关控制，无域锁通路 |
| 旅游商务（Travel · commerce 子流） | `TRAVEL_COMMERCE_ENABLED`（默认关） | `backend/travel/commerce/`，2026-09-24 STOP K |
| 旅游预订（Travel · booking 子流） | `TRAVEL_BOOKING_ENABLED`（默认关） | `backend/travel/booking/`，预订事务与幂等账本复用，2026-09-25 STOP L |

**客服订单回指**（`customer_service/context/context_resolver.py`，2026-10-05 a65a167 同批）：列表查询结果落 `recent_order_ids`（≤20 条）后，「第N个订单」按序号解析（中文/阿拉伯数字；**越界不猜**——索引出界即不注入，照常路由）；代词回指仍要求 代词+谓词 双条件命中；当前轮显式订单号恒优先于继承（05ae6aa 显式序号订单指代）。

旅游域的实时增强检索也遵循既有 Agent → Service → Tool 边界：Research Agent 按用户明确请求调用 `map_merchant_search_tool`（高德 `types=050000` 餐饮 / `types=100000` 住宿），Planning Agent 调用 `travel_train_search_tool`（12306 MCP）。`requirement.interpreted`、`tool.started`、`tool.result` 通过旅游 SSE 旁路投影到用户端；Tool 失败、空结果和未配置保持不同状态，不用静态演示数据补齐。

> **`travel_commerce` / `travel_booking` 是内部调度标识，不是独立业务域**：两者以 `route_mode` 形态存在的唯一身份是 prefilter → route_selector → 域图注册表（`domain_graph_registry`）的查表键，**永久保留不改名**——`route_selector` 查表未命中会**静默落入 planner 兜底**（非响亮失败）。语义投影在决策层已完成（STOP B 起）：DomainRouter 把两者归一为 `domain=travel + subflow=commerce/booking` 并随 trace metadata 持久化；注册表以 `DomainGraph.subflow` 展示字段表达同一语义，两层口径由 `backend/tests/orchestration/test_domain_semantic_consistency.py` 守护。

### 进入域图的两条独立通路

| 入口 | 触发方式 | 行为 |
|---|---|---|
| **客服窗口锁域** | 用户端客服抽屉 `CSDrawer` 每条消息带 `domain_hint=customer_service`（`frontend/src/hooks/useCSChat.ts`） | `router_node` 置 `cs_forced` → **跳过域检测门、跳过灰度判定（恒 treatment）、跳过旅游/选品 prefilter**，直接进客服管线。仍受 `CS_ENABLED` 总闸约束（关闭则降级回主路由） |
| **全局入口** | `domain_hint` 为空（普通对话页） | 在 router 内按序判定：CS 廉价规则预判 → 旅游正则 → 选品正则 → CS 完整检测（同为纯正则，与第一步同源）；CS 命中后还须过服务端灰度 `CS_ROLLOUT_PERCENT`（默认 100），落 control 组则回主图 |

预过滤优先级 **客服 > 旅游**（「订单里的行程单」按客服诉求处理）。

### 域入口模式与 handoff 引导（多域隔离收官 2026-10-06）

全局入口 prefilter 命中后的分派由三个模式开关控制：`CS/TRAVEL/SELECTION_GLOBAL_ENTRY_MODE`（`execute|guide`，**默认 execute＝行为零变化**）：

- `execute`：照旧进入域图执行（基线等价）。
- `guide`：主图**短路为 router→reporter**，reporter 直出契约自带引导话术（零 LLM），SSE 发 **AUX 帧 `handoff`**——契约 `orchestration/contracts/handoff.py::HandoffPayloadV1`（目标域＋参数包＋原因），与前端 `frontend/src/types/handoff.ts` 同构（共享 fixture `backend/tests/fixtures/handoff_payload_v1.json` 对齐测试）。前端 `HandoffCard` 提供三入口带参跳转（旅游页预填 / 选品页带参 / CSDrawer 预填＋`cs-drawer:open` 事件），点击埋点 `POST /observability/handoff/click`，指标 `agent_handoff_total{target_domain,phase}`。

不受模式开关影响的两条通路：**旅游一次性查询**（「福州有什么景点」类，passthrough 落回主路由）与**客服窗口锁域**（`domain_hint` 强制进 CS 管线）。UI 归宿「四扇门」：主聊天 / 旅游页 / 客服抽屉 / `/selection-funnel` 选品专属页（选品漏斗的用域图载体实现的固定工作流范式）。验收：`docs/reports/2026-10-06-多域隔离收官验收报告.md`。

> **客服窗口为什么必须锁域**：此处**不存在「漏进主图」的 A/B 对照语义**（用户已显式进入客服窗口），而每条消息重新判域有两个实测代价——
> ① **召回漏判**：CS 规则阈值 `CS_RULE_MIN_HITS=2`，实测「东西坏了咋办」「我的订单三天前就显示已发货，为什么还没收到」规则命中**均仅 1** → 全局入口判非客服、落到 `route_mode=plan`，白跑一轮 Planner/LLM；
> ② **域错配**：非客服问法被甩到主图 plan 支线（实测「下周去大阪怎么玩」`cs规则=0` → `route_mode=plan`）。
> 锁域顺带把该窗口的 token 用量归因到 `component="customer_service"`（trace 打 `cs_domain_lock=1`）。
>
> ⚠️ **锁域几乎不省时间，别当性能优化看**：全部域预过滤合计 **< 0.1ms**（实测 CS 规则预判 14~35µs / 旅游正则 22~53µs / 选品正则 9~20µs）；CS 域检测自 2026-09-18 起已无向量通道，冷路径 ~21µs、命中缓存 ~1µs。
>
> **锁域不等于绝对**：域锁下若「无任何客服规则信号 **且** 命中旅游/选品强信号」，仍会走 `redirect_main` 正则阶段转出主路由——但该正则**只认种子城市（福州/厦门/杭州）**，故「去大阪怎么玩」这类问法仍留守客服管线（阶段二 LLM 语义仲裁默认 OFF，`CS_REDIRECT_MAIN_LLM_ENABLED`）。混合信号（如「订单里的行程单怎么退款」含客服规则）**仍守 CS 优先**。行为有测试守护：`backend/tests/orchestration/graph/test_router_prefilter_order.py`（19 例全绿）。

## Runtime 语义收口（V2 决策与投影，2026-10-06）

路由事实源升级为 **`RouteDecisionV2`**（`orchestration/router/types.py` + `orchestration/runtime_types.py::RuntimeType/RuntimeTarget`），五类 Runtime 家族 `agent/workflow/plan/direct/generic_runtime`；旧 `route_mode`、`route_decision` 与平铺字段**只由唯一 Projection 生成**——`orchestration/router/projection.py::project_route_decision_to_legacy_state()` 统一写出 route_decision_v2、兼容 route_decision、route_mode、domain 平铺、candidate/tool route 与 clarification 字段。AST 守卫 `backend/tests/orchestration/graph/test_legacy_route_writer.py` 禁止在 projection 之外新增生产写入口；prefilter、continuation、clarify、handoff、general_chat 一律经 Projection 落字段。

**域图注册表（`domain_registry.py`）扩展 runtime descriptor**：注册期校验 runtime_id 唯一、alias 不冲突、子流父域存在且不自指；`DomainGraph` 声明 `runtime_id / runtime_type / aliases / capabilities / supports_checkpoint / supports_interrupt / supports_streaming / result_contract_version / entry_modes / continuation_policy`（CS：checkpoint=true/interrupt=false；Travel：checkpoint=true/interrupt=true；Selection：均 false）。Router 的域族、入口模式、prefilter domain 与 subflow 元数据从 Registry 活视图派生（`resolve_alias` / `route_mode_to_runtime_target` / `route_mode_to_family` / `route_mode_to_entry_mode`），域字面量静态守卫 `test_router_domain_literal_guard.py` 保证**新增域不需要改 Router 文件**（注册面 ≤3 处）。

**域图出口 RuntimeResult 归一**：`orchestration/runtime_result_adapter.py::attach_runtime_result()` 在 CS/Travel/Selection 域图节点返回既有 state update 前调用归一，写入可序列化的 state `runtime_result`（answer、answer_type、sources、clarification、handoff、tool_calls、metadata 不丢）；主 reporter 不读取域 runtime_result 做二次 LLM 改写，域图仍直连 END。

**State canonical 化**：`orchestration/state_projection.py` 提供 canonical readers（`canonical_route_decision` / `resolved_params` / `clarification_request`），V2 decision 优先、旧字段缺失时可由 V2 投影读取；unknown state key 守卫为 0（`test_state_key_guard.py`）。

**Trace 观测 13 字段**：domain、subflow、runtime_type、runtime_id、interaction_mode、execution_mode、workflow_id、capability、skill_id、tool_id、prompt_version、confidence、source（`orchestration/router/router_trace.py`）；Skill/Tool 归因装饰器埋 `record_runtime_attribution(skill_id/tool_id)`，与既有 llm_attribution_scope 叠加。

**机械验收门** `python -m backend.scripts.verify_runtime_arch_v2 --stage G --final`：A–G 七阶段门 + node_id/SSE 事件/checkpoint/前端节点映射兼容门 + 全局回归（direct/workflow/plan、三域图、clarify、handoff、general_chat、Travel interrupt→pending→resume、Plan Send payload），全部由真实测试退出码计算、禁止手写 PASS；终态 447 passed、`STATE_UNKNOWN_KEY_TOTAL=0`、`PRODUCTION_BEHAVIOR_CHANGED=false`、`AGENT_RUNTIME_ARCH_V2_READY=true`。设计稿：[2026-10-06-runtime-semantic-closure-design.md](../superpowers/specs/2026-10-06-runtime-semantic-closure-design.md)；实施方案与执行状态：[2026-10-06-runtime-semantic-closure.md](../superpowers/plans/2026-10-06-runtime-semantic-closure.md)。

## 跨轮状态契约（checkpointer 关闭时同样必须遵守，域图通用）

1. `new_*_graph_input()` **只放本轮输入**，不预置产物/执行态默认值 —— checkpointer 会把 input 当对上轮状态的**更新**合并，预置 `brief: {}` 等于每轮清空成果
2. 读状态一律 `.get()` —— 本轮没写过的键不在最终状态里
3. `brief_fingerprint` 变 → 只在 slot_filler 里 `planning_reset()`；不清则 supervisor 会把**上一轮行程**当新需求输出

## 追问防循环（clarify，工作区在途）

主图 clarify 追问从「每会话 10 分钟一次性」重设计为防循环语义（`orchestration/graph/clarify_content.py`）：① 同一问题归一化去重（30 分钟窗口，跨会话隔离）——窗口内重复同问不再重复给追问卡；② 会话滑动窗口封顶 2 次（`_SESSION_CLARIFY_LIMIT`）；③ 去重缓存故障 fail-open（不阻塞主链）。另增 SQL 空结果 / SQL 倾向的定向追问卡（`build_sql_empty_clarify`，挂路由拒答兜底）。配套未答台账与点击漏斗（迁移 070/071、`/admin/unanswered`、`/admin/clarify`，见 API.md 与 DATABASE.md）。**随代码合入本节生效**。

## Router 与其他「Router」的区分

「Router」在本仓库有三个互不相同的出现位置，文档与代码评审时不得混用：

| 名称 | 位置 | 职责 |
|---|---|---|
| **主图 Router**（`orchestration/graph/router_node.py`） | LangGraph 主图入口节点 | Input Guard 之后执行域预过滤 + RoutingEngine 六阶段（entry_gate→domain→intent→capability→policy→route_decision）产出 `RouteDecisionV2`；旧 route_mode（direct/workflow/plan/general_chat/clarify/域图）等兼容字段只由唯一 Projection 生成（见上节「Runtime 语义收口」） |
| RAG 查询路由（`rag/retrieval/query_router.py`） | rag-service 内检索管道 | 检索策略选择，属于 RAG 子系统内部实现 |
| SQL Schema Router（`sql/` 内） | SQL 子系统内部 | 自动选表，属于 NL2SQL 子系统内部实现 |

只有第一个是「README 架构图里的 Router」；后两者是子系统内部组件，不出现在平台架构图中。

## 旅游会话意图（v3 P0-A）

`travel_slot_filler` 先做意图分类——**代码默认纯规则**；开关 `TRAVEL_LLM_INTENT_ENABLED`（默认关，`TRAVEL_LLM_INTENT_TIMEOUT_MS` 默认 3000）开启后，仅词表盲区（词表分类返回 None 的消息）交 LLM 补判一次 intent 家族：输出只取枚举 family 的结构守卫（其余键一律丢弃），失败/超时/无绑定一律回落词表，词表能接住的消息永不过 LLM。再合并需求，最后检查缺槽。已有行程的逐条改单优先；明确规划动作压过疑问；动态与静态问答优先于「城市＋天数」简写。能结构化解析的改单走 `parse_partial_request` → `travel_partial_replan` **局部重规划**（六操作 replace/remove/add/pace/end_time/hard_constraint，纯规则；只动被点名天与条目、强制重验证，`validation_failed` → failed；新地点缺候选只补候选不重排，`4ddc3c3`）；无法解析的改动信号仍走既有指纹变化整体重排。`travel_supervisor` 的意图门禁先于缺槽判断，问答与探索直接到 reporter，不调规划子 Agent。

意图共**八类**（PLAN / MODIFY / QUERY_DYNAMIC / QUERY_STATIC / DISCOVER / SOCIAL / META / **OUT_OF_SCOPE**；2026-10-07 `4ddc3c3` 增 `SOCIAL`/`META` 轻交互类，与 QUERY 家族同属 `NON_PLANNING_INTENTS`——只走 reporter 轻量出口 `_answer_social`/`_answer_meta`，不得进完整规划链），其中 **`OUT_OF_SCOPE` 出域引导**（2026-10-03 `24dfb24`，M2-G）为最高优先级：非旅游域强信号词首中即拦，reporter 走引导出口（`_answer_out_of_scope`）——明确告知不属旅游域、不硬解析不硬排，引导回主对话/客服链路，避免旅游域图硬接非旅游诉求。

城市名录只用于识别与消歧，不能作为 live Provider 的支持范围闸门。目的地、出发地、路线和预过滤共用带负向词与后缀守卫的扫描入口；种子模式仍如实展示种子覆盖范围。静态问答的一次只读攻略检索在 slot_filler 侧完成，输出 available/empty/unavailable 三态；reporter 只渲染既有结果。问答对外不重复发布 checkpoint 中的旧行程，也不创建规划 pending。

需求抽取侧的节奏口径（#80）：显式 pace 词优先；未提 pace 时按同行人群派生默认档位（requirement_agent 词表：老人/爸妈/带娃/亲子→relaxed，特种兵/暴走/学生党→intense；slot_filler 经 `extract_group_pace` 并入合并），经既有 pace 容量约束传导到排程，不新增排程分支。规划会话恢复：旅游域追问中断后的**纯槽位值回答**（「8万日元」「住难波」类，不含旅游/延续信号词）由 `TravelPendingResolver`（插在 ContinuationResolver 之前，纯规则零 LLM）判定短路回旅游域图，`TRAVEL_PENDING_RESUME_ENABLED` 默认 true；客服强信号仍优先放行。

最后验证：2026-10-07 · 见 [P0-A 收尾验收](../reports/2026-10-02-旅游灵感式规划v3-P0-A收尾验收.md)；本节意图分类口径已按 `TRAVEL_LLM_INTENT_ENABLED` 开关状态改写（默认纯规则不变）。2026-10-07 增量：意图六类→八类（增 SOCIAL/META）、逐条改单接 `travel_partial_replan` 局部重规划（`4ddc3c3`）；路由表与六阶段链对齐 Runtime V2（`8d4dbb7` 收口的补充）。

## 旅游数据源与版本链（2026-10-02）

- **POI 候选池默认 live**：`TRAVEL_POI_SOURCE` 默认 `live`（腾讯 LBS 实时检索）；种子库下线为 legacy 通道，仅 `TRAVEL_POI_SOURCE=seed` 或显式回退（`TRAVEL_POI_FALLBACK_SEED`，默认关）时启用，live 失败如实披露、不静默回退种子。依赖事实源在 `backend/travel/services/poi_service.py`（专家层经 services，不直连 `tools/travel/poi.py` 的种子加载）。
- **规划产物版本链**：`plan_version` 修复重排 +1、`parent_plan_version` 指针；`TRAVEL_PLAN_VERSIONS_ENABLED` 默认开、保留 20 版（agent_memory 库运行时幂等建表）。对外 `/plans` 端点族见 [API.md](../API.md)「历史规划与版本链」。
- **城际车票摘要**：transit 专家把 12306 实时车票摘要写入 `itinerary.intercity`（前 6 车次，票价并查前 2）；空 = 未触发车票查询，非规划硬依赖，旧 checkpoint 兼容。
- **方案档位与预算协商**（2026-10-03 M3，`2a404ef`）：`brief.tier`（`economy`/`comfortable`，缺省 `economy`）进契约；预算超档位上限时 budget 专家按 `BUDGET_POLICY` 压缩顺序自动降档，经济档仍超时输出缺口数据（`floor_total_cny`/`gap_cny`），协商过程写 `rationale.budget_negotiation`。档位画像 `TIER_PROFILES` 为 `backend/config/travel.py` 代码配置，非数据库表。
- **城市指南与通用 Tool 缓存**（同批）：`GET /api/travel/city-guide` 轻端点（不进域图，四级内容链：文档摘要→RAG travel 库→知乎→暂无，7 天缓存）；旅游域 live 检索 Tool 走通用缓存层（`TRAVEL_TOOL_CACHE_ENABLED` 默认开 / TTL 默认 86400s）。
- **POI 候选池三源与三路并发**（2026-10-04）：LBS 主源之上并入两个默认开启的新源——高德景点类目（`TRAVEL_POI_AMAP_SOURCE_ENABLED` 默认 true：rating/营业时间/检索时刻 open_status 标注与必去警告，单源失败只损失评分不损失腾讯候选）与 travel RAG 库本地攻略文档名解析补池（`TRAVEL_POI_LOCAL_DOC_ENABLED` 默认 true，≤5 条）；每次关键词检索 20 条、候选池上限 120。专家侧 candidates/guides/hotel 三路 ThreadPool 并行检索（ContextVar 经 copy_context 快照传播到工作线程，单路失败独立降级留痕）；美食商户按「就近+品类+评分」综合排序（品类加权表 `TRAVEL_FOOD_CATEGORY_BOOSTS` 默认空不启用）。
- **抵达车票自动查询（#7a）**：有出发地+出发日期即自动查询，无需「查高铁」类触发词（触发词不再门控，`message` 参数仅为调用方签名兼容保留）；重复查询由 provider 共享缓存与 tool_cache 兜底，二次规划零成本。
- **到达时间缓冲与 brief 契约扩展（2026-10-07 `4ddc3c3`）**：首日活动开始不得早于 `brief.arrival_time` + `TRAVEL_ARRIVAL_BUFFER_MINUTES`（默认 30）；brief 新增 `budget_constraint`（`hard`=不得超出预算（默认）/ `soft`=可超需说明）与 `weather_conditions`；会话级 trace 写 `travel_semantics` 投影（`travel/trace_semantics.py`：planning_mode / interaction_mode / base·active·draft 版本 / semantic_change / modified_days 等）。
- **分类候选表**（2026-10-05 验收 #10，`721c9e4`）：POI 专家产出的候选池落 graph `state.candidates`（随 checkpoint 持久化），`GET /api/travel/candidates` 经域图单例读取（thread_id 与规划链同源）→ 前端 CandidatesPanel 分类展示（组内 rating 降序、每组 ≤12 条）；换入走 canvas_replace 草案管线，decision `entry=candidates_panel`；checkpoint 不可达如实 `available=false`，不伪造候选。

最后验证：2026-10-06 · `TRAVEL_POI_SOURCE` 默认值、`TRAVEL_PLAN_VERSIONS_*` 配置、`itinerary.intercity` 契约字段、M3 档位/协商/城市指南实测；2026-10-05 增量补记 POI 候选池三源与三路并发 / 抵达车票自动触发 / slot_filler LLM 意图补判开关 / #80 人群节奏派生与旅游 pending resume；2026-10-06 增量补记客服 L4.5 分诊直出 / need_info 转人工逃生 / 订单序号回指 / 旅游分类候选表状态管线。

## 相关文档

- [domain-service-map.md](domain-service-map.md) — 域图 × 专家 × 工具 × 第三方服务 × 凭据地图（依赖/降级/治理）
- [2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md](../2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md) — 四层定义与例外台账
- [2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md](../2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md) — 新增资产 checklist
- [OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md](../OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md) — Celery 异步运行时
- [system-overview.md](system-overview.md) — 部署拓扑与端口
- [2026-10-06-runtime-semantic-closure-design.md](../superpowers/specs/2026-10-06-runtime-semantic-closure-design.md) — Runtime 语义收口设计稿（RouteDecisionV2 / Projection / Registry descriptor）
- [2026-10-06-runtime-semantic-closure.md](../superpowers/plans/2026-10-06-runtime-semantic-closure.md) — 实施方案与 STOP A–G 执行状态（机械验收门口径）
