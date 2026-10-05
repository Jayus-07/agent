# AI Runtime — LangGraph 编排细节

> 本文承接 README 的编排层细节：主图节点职责、垂直域图、客服锁域、跨轮状态契约。
> 主图事实源 = `backend/orchestration/graph/builder.py`；域图注册事实源 = `backend/domains/__init__.py`；
> capability→Skill 映射唯一事实源 = `backend/orchestration/router/capabilities.yaml`。

## 主图（LangGraph StateGraph）

固定 **9 个核心节点**（2026-09-25 口径对齐 `builder.py:142`），顺序与命名不得随意改动；Skill 节点与域图节点由自动发现加入，**不得手写进 builder**：

```
START → router ─┬─ 客服域锁（domain_hint=cs，跳过判域/灰度）→ 客服域图 → END
                ├─ 客服预过滤命中（CS_ENABLED + 灰度）     → 客服域图 → END
                ├─ 旅游预过滤命中（TRAVEL_ENABLED）        → 旅游域图 → END
                ├─ 选品预过滤命中（SELECTION_FUNNEL_ENABLED）→ 选品漏斗域图 → END
                └─ RoutingEngine（domain → intent → capability → policy → RouteDecision）→ route_selector
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
| Router | 入口门禁（GraphRunner Input Guard）之后执行域预过滤（客服域锁 / CS / 旅游 / 选品 / 商务 / 预订，纯正则）+ RoutingEngine：DomainRouter → IntentRouter → CapabilityRouter → ExecutionModeResolver → RouteDecision；规则、向量和 LLM 是证据提供者，基础设施故障走显式 LLM fallback |
| tool_selector | direct 路径首站：Function-Call 门控选工具 + 填参；失败/快路径直通零开销 |
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
| 客服 | `CS_ENABLED` | `state_loader → pending_handler → cs_supervisor → 5 专家（knowledge/query/action/complaint/handoff）→ cs_reporter`；`cs_supervisor` 承担 handoff 拦截、循环上限、LLM 兜底 |
| 旅游（Travel · planning 子流） | `TRAVEL_ENABLED` | `travel_slot_filler → travel_supervisor → poi/transit/budget/risk/weather 五专家 → travel_validator →（未过）travel_repair → travel_reporter`；validator 纯规则零 LLM 零 IO，只判定不修改（修复在 repair），四轴 = 时间/地理/体力/预算；error 级违反阻塞交付；局部修复只动被点名的天与条目，用户点名必去条目永不被静默丢弃（`kept_required`） |
| 选品漏斗 | `SELECTION_FUNNEL_ENABLED` | prefilter 已接线（`router_node` 内与旅游同层，2026-09-17）；仅受开关控制，无域锁通路 |
| 旅游商务（Travel · commerce 子流） | `TRAVEL_COMMERCE_ENABLED`（默认关） | `backend/travel/commerce/`，2026-09-24 STOP K |
| 旅游预订（Travel · booking 子流） | `TRAVEL_BOOKING_ENABLED`（默认关） | `backend/travel/booking/`，预订事务与幂等账本复用，2026-09-25 STOP L |

> **`travel_commerce` / `travel_booking` 是内部调度标识，不是独立业务域**：两者以 `route_mode` 形态存在的唯一身份是 prefilter → route_selector → 域图注册表（`domain_graph_registry`）的查表键，**永久保留不改名**——`route_selector` 查表未命中会**静默落入 planner 兜底**（非响亮失败）。语义投影在决策层已完成（STOP B 起）：DomainRouter 把两者归一为 `domain=travel + subflow=commerce/booking` 并随 trace metadata 持久化；注册表以 `DomainGraph.subflow` 展示字段表达同一语义，两层口径由 `backend/tests/orchestration/test_domain_semantic_consistency.py` 守护。

### 进入域图的两条独立通路

| 入口 | 触发方式 | 行为 |
|---|---|---|
| **客服窗口锁域** | 用户端客服抽屉 `CSDrawer` 每条消息带 `domain_hint=customer_service`（`frontend/src/hooks/useCSChat.ts`） | `router_node` 置 `cs_forced` → **跳过域检测门、跳过灰度判定（恒 treatment）、跳过旅游/选品 prefilter**，直接进客服管线。仍受 `CS_ENABLED` 总闸约束（关闭则降级回主路由） |
| **全局入口** | `domain_hint` 为空（普通对话页） | 在 router 内按序判定：CS 廉价规则预判 → 旅游正则 → 选品正则 → CS 完整检测（同为纯正则，与第一步同源）；CS 命中后还须过服务端灰度 `CS_ROLLOUT_PERCENT`（默认 100），落 control 组则回主图 |

预过滤优先级 **客服 > 旅游**（「订单里的行程单」按客服诉求处理）。

> **客服窗口为什么必须锁域**：此处**不存在「漏进主图」的 A/B 对照语义**（用户已显式进入客服窗口），而每条消息重新判域有两个实测代价——
> ① **召回漏判**：CS 规则阈值 `CS_RULE_MIN_HITS=2`，实测「东西坏了咋办」「我的订单三天前就显示已发货，为什么还没收到」规则命中**均仅 1** → 全局入口判非客服、落到 `route_mode=plan`，白跑一轮 Planner/LLM；
> ② **域错配**：非客服问法被甩到主图 plan 支线（实测「下周去大阪怎么玩」`cs规则=0` → `route_mode=plan`）。
> 锁域顺带把该窗口的 token 用量归因到 `component="customer_service"`（trace 打 `cs_domain_lock=1`）。
>
> ⚠️ **锁域几乎不省时间，别当性能优化看**：全部域预过滤合计 **< 0.1ms**（实测 CS 规则预判 14~35µs / 旅游正则 22~53µs / 选品正则 9~20µs）；CS 域检测自 2026-09-18 起已无向量通道，冷路径 ~21µs、命中缓存 ~1µs。
>
> **锁域不等于绝对**：域锁下若「无任何客服规则信号 **且** 命中旅游/选品强信号」，仍会走 `redirect_main` 正则阶段转出主路由——但该正则**只认种子城市（福州/厦门/杭州）**，故「去大阪怎么玩」这类问法仍留守客服管线（阶段二 LLM 语义仲裁默认 OFF，`CS_REDIRECT_MAIN_LLM_ENABLED`）。混合信号（如「订单里的行程单怎么退款」含客服规则）**仍守 CS 优先**。行为有测试守护：`backend/tests/orchestration/graph/test_router_prefilter_order.py`（19 例全绿）。

## 跨轮状态契约（checkpointer 关闭时同样必须遵守，域图通用）

1. `new_*_graph_input()` **只放本轮输入**，不预置产物/执行态默认值 —— checkpointer 会把 input 当对上轮状态的**更新**合并，预置 `brief: {}` 等于每轮清空成果
2. 读状态一律 `.get()` —— 本轮没写过的键不在最终状态里
3. `brief_fingerprint` 变 → 只在 slot_filler 里 `planning_reset()`；不清则 supervisor 会把**上一轮行程**当新需求输出

## Router 与其他「Router」的区分

「Router」在本仓库有三个互不相同的出现位置，文档与代码评审时不得混用：

| 名称 | 位置 | 职责 |
|---|---|---|
| **主图 Router**（`orchestration/graph/router_node.py`） | LangGraph 主图入口节点 | Input Guard 之后执行域预过滤 + RoutingEngine（domain→intent→capability→policy→RouteDecision）+ 拍板 route_mode（direct/workflow/plan/general_chat/clarify/域图） |
| RAG 查询路由（`rag/retrieval/query_router.py`） | rag-service 内检索管道 | 检索策略选择，属于 RAG 子系统内部实现 |
| SQL Schema Router（`sql/` 内） | SQL 子系统内部 | 自动选表，属于 NL2SQL 子系统内部实现 |

只有第一个是「README 架构图里的 Router」；后两者是子系统内部组件，不出现在平台架构图中。

## 相关文档

- [domain-service-map.md](domain-service-map.md) — 域图 × 专家 × 工具 × 第三方服务 × 凭据地图（依赖/降级/治理）
- [2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md](../2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md) — 四层定义与例外台账
- [2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md](../2026-09-16-新增Agent-Skill-Tool-MCP操作手册.md) — 新增资产 checklist
- [OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md](../OPTIMIZATION_P3_ASYNC_QUEUE_ARCHITECTURE.md) — Celery 异步运行时
- [system-overview.md](system-overview.md) — 部署拓扑与端口
