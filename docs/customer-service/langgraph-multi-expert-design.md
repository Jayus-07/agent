# 客服系统 LangGraph 多专家架构重构设计方案

> **状态**: 设计讨论阶段 (Draft)
> **日期**: 2026-09-04
> **约束**: 本阶段禁止修改代码，仅输出设计方案

---

## 目录

1. [当前架构事实确认](#1-当前架构事实确认)
2. [当前 CS 架构问题](#2-当前-cs-架构问题)
3. [为什么现在需要 CS Supervisor](#3-为什么现在需要-cs-supervisor)
4. [目标架构](#4-目标架构)
5. [Router / CS Supervisor / Expert 职责边界](#5-router--cs-supervisor--expert-职责边界)
6. [Expert Agent 设计](#6-expert-agent-设计)
7. [CSGraphState 与 cs_graph_node 设计](#7-csgraphstate-与-cs_graph_node-设计)
8. [三套状态机与 LangGraph State 的关系](#8-三套状态机与-langgraph-state-的关系)
9. [Command 设计](#9-command-设计)
10. [Checkpointer / Persistence 设计](#10-checkpointer--persistence-设计)
11. [Pending / Confirmation / Handoff 设计](#11-pending--confirmation--handoff-设计)
12. [Reporter 重构方案](#12-reporter-重构方案)
13. [Trace 重构方案](#13-trace-重构方案)
14. [Main Supervisor 与 CS Supervisor 边界](#14-main-supervisor-与-cs-supervisor-边界)
15. [A/B/C 方案对比](#15-abc-方案对比)
16. [最终推荐方案](#16-最终推荐方案)
17. [完整 LangGraph 拓扑图](#17-完整-langgraph-拓扑图)
18. [Migration 分阶段方案](#18-migration-分阶段方案)
19. [文件级修改清单](#19-文件级修改清单)
20. [测试方案](#20-测试方案)
21. [风险与回滚方案](#21-风险与回滚方案)
22. [明确禁止事项](#22-明确禁止事项)
23. [最终结论](#23-最终结论)

---

# 1. 当前架构事实确认

> 以下所有事实均来自代码扫描，以当前代码为唯一事实来源。

## 1.1 LangGraph 图结构

| 事实 | 代码位置 | 说明 |
|------|---------|------|
| 仅 1 张 `StateGraph(CSAgentState)` | `backend/orchestration/graph/builder.py:107` | 整个系统共用一张图 |
| 无 Checkpointer | `builder.py:195` `return wf.compile()` | 无参数，无持久化 |
| 无 `Command` | 全项目 grep 无 `from langgraph.types import Command` | 完全未使用 |
| 无 `interrupt` | 全项目 grep 无 `GraphInterrupt`/`interrupt` | 无 HITL 暂停机制 |
| 使用 `Send` | `backend/orchestration/supervisor/scheduler.py:217-260` | Supervisor 并行分发 Skill |
| 无 SubGraph | 所有节点平铺在同一张图 | CS 节点直接注册在主图 |

## 1.2 当前拓扑

```
START → router ──[route_selector]──→ {
    planner → critique ──[route_after_critique]──→ {supervisor, reporter}
    skill_executor → reporter
    workflow_executor → reporter
    cs_knowledge → reporter
    cs_business_query → reporter
    cs_business_action → reporter
    cs_pending → reporter
    cs_complaint → reporter
    cs_handoff → reporter
    cs_handoff_intercept → reporter
}

supervisor ──[route_after_supervisor]──→ list[Send](skill_node) | "reporter"
每个 skill_node → supervisor (循环, MAX_SUPERVISOR_LOOPS=10)
reporter → END
```

## 1.3 CSAgentState 字段

**文件**: `backend/orchestration/state.py:44-91`

继承自 `AgentState(TypedDict)` 的字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `question` | `str` | 用户问题 |
| `kb_id` | `str` | 知识库 ID |
| `plan` | `dict` | DAG 执行计划 |
| `step_results` | `Annotated[dict, _merge_step_results]` | 步骤结果 (自定义 reducer) |
| `current_step_id` | `str` | 当前步骤 ID |
| `messages` | `Annotated[list, add_messages]` | LangGraph 消息列表 |
| `final_answer` | `str` | 最终回复 |
| `route_decision` | `str` | 路由决策 |
| `route_mode` | `str` | 路由模式 (plan/direct/workflow/customer_service) |
| `executor_error` | `str` | 执行器错误 |
| `executor_mode` | `str` | 执行器模式 |
| `alerts` | `list` | 告警列表 |
| `_supervisor_loop_count` | `int` | Supervisor 循环计数 |
| `_degraded_steps` | `Annotated[set, operator.or_]` | 降级步骤集合 |

`CSAgentState` 新增字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `cs_context` | `dict` | CS 上下文 (无 schema) |
| `cs_action_result` | `dict` | CS 动作执行结果 |
| `cs_audit_entries` | `list[dict]` | CS 审计记录 |

`cs_context` 内部结构 (文档注释 `state.py:80-89`):

```python
{
    "authenticated_user_id": str,
    "conversation_id": str,
    "handoff_state": str,           # AI_ACTIVE | HANDOFF_REQUESTED | ...
    "pending_action": dict | None,  # 待确认的业务操作
    "cs_route": dict,               # CS Router 输出 (domain + intent)
    "confirmation_state": str,      # NOT_REQUIRED | PENDING | CONFIRMED | ...
    "retry_count": int,
}
```

实际运行时还包含: `cs_target` (节点名), `session_id`, `answer_meta`, `query_meta`, `action_result` 等。

## 1.4 CS 节点清单

| 节点名 (Graph) | 函数名 | 文件 | 行号 | 职责 |
|----------------|--------|------|------|------|
| `cs_knowledge` | `cs_knowledge_node` | `customer_service/graph/nodes.py` | 16 | RAG 知识问答 |
| `cs_business_query` | `cs_business_query` | 同上 | 113 | 只读业务查询 (订单/物流) |
| `cs_business_action` | `cs_business_action` | 同上 | 274 | 写操作 + 确认门控 |
| `cs_pending` | `cs_pending_node` | 同上 | 75 | 占位桩 (功能建设中) |
| `cs_complaint` | `cs_complaint` | 同上 | 664 | 投诉检测 + 工单 |
| `cs_handoff` | `cs_handoff` | 同上 | 748 | 转人工 |
| `cs_handoff_intercept` | `cs_handoff_intercept` | 同上 | 844 | 人工状态拦截 |

所有 CS 节点:
- 签名: `(state: dict) -> dict`
- 直接写入 `state["final_answer"]`
- 通过 `trace_middleware.wrap_sync_node()` 包装
- 固定边到 `reporter` (`builder.py:168-174`)

## 1.5 三套状态机

### Conversation State Machine

**文件**: `backend/customer_service/state_machine.py`

| 维度 | 状态值 |
|------|--------|
| `ConvStatus` | `open`, `pending`, `resolved`, `snoozed` |
| `HandlingMode` | `ai`, `human`, `waiting_human` |

- 双维度独立转换，带不变量校验 (`_check_invariants`)
- 纯函数，不持久化

### Confirmation State Machine

**文件**: `backend/customer_service/confirmation.py`

| 状态 | 说明 |
|------|------|
| `NOT_REQUIRED` | 无需确认 |
| `PENDING_CONFIRMATION` | 等待用户确认 |
| `USER_CONFIRMED` | 用户已确认 |
| `USER_CANCELLED` | 用户取消 |
| `EXECUTING` | 执行中 |
| `SUCCESS` | 执行成功 |
| `FAILED` | 执行失败 |
| `EXPIRED` | 已过期 |

- 终态: `SUCCESS`, `FAILED`, `CANCELLED`, `EXPIRED`
- `PENDING → EXECUTING` 是非法的 (必须经过 `CONFIRMED`)

### Handoff State Machine

**文件**: `backend/customer_service/handoff.py`

| 状态 | 说明 |
|------|------|
| `AI_ACTIVE` | AI 处理中 |
| `HANDOFF_REQUESTED` | 已请求转人工 |
| `WAITING_HUMAN` | 等待人工接入 |
| `HUMAN_ACTIVE` | 人工处理中 |
| `CLOSED` | 已关闭 (终态) |

- 拦截状态: `HANDOFF_REQUESTED`, `WAITING_HUMAN`, `HUMAN_ACTIVE`
- 自动触发: 连续低置信度 >= 2 或连续失败 >= 限制

## 1.6 路由体系

```
用户消息
  │
  ▼
DomainDetector (双通道: 规则 + 向量)
  │ is_cs?
  ▼
CSRouter (CSCoarseRouter → CSFineRouter)
  │
  ▼
CSRouteResult {domain, intent, confidence, route_path, kb_ids, ...}
  │
  ▼
route_path → cs_target 映射:
  knowledge_query → cs_knowledge
  business_query → cs_business_query
  business_action → cs_business_action
  complaint_flow → cs_complaint
  human_handoff → cs_handoff
  else → cs_pending
```

**关键文件**:
- `backend/customer_service/router/domain_detector.py` — 保守双通道门控
- `backend/customer_service/router/cs_router.py` — 两级分类
- `backend/customer_service/router/coarse_router.py` — 粗分类 (规则→向量→hint)
- `backend/customer_service/router/fine_router.py` — 细分类 (域条件)
- `backend/customer_service/router/intents.py` — 19 个意图, 6 个域
- `backend/orchestration/graph/router_node.py` — Graph Router 节点

## 1.7 CS 与主系统的关系

**CS 路径完全绕过 Planner/Critique/Supervisor/ToolRegistry**:

- CS 节点不调用任何 `BaseSkill`
- CS 节点不使用 `tool_registry`
- CS 节点直接调用 `customer_service/service/` 下的业务服务单例
- CS 有自己独立的服务层: `OrderService`, `LogisticsService`, `RefundService`, `AfterSalesService`, `ComplaintService` 等

## 1.8 Trace 体系

- **ContextVar 传播**: `trace_collector.current()` 通过 ContextVar 获取当前 trace，不通过 Graph State
- **自动埋点**: `trace_middleware.wrap_sync_node()` 为每个节点创建 span
- **SpanKind**: 已有 CS 专用类型 (`CS_ROUTING`, `CS_KNOWLEDGE`, `CS_BUSINESS_QUERY` 等)
- **持久化**: Redis Streams → SQLite / PG mirror
- **CS 关联**: `record_cs_turn()` 将 `trace_id` 写入 `CSConversation` 和 `CSMessage`

---

# 2. 当前 CS 架构问题

## 2.1 核心问题

| # | 问题 | 影响 | 严重度 |
|---|------|------|--------|
| P1 | **单轮单节点**: Router 一次选定一个 CS 节点，直接到 Reporter | 无法实现"查询→确认→执行"多步协作 | 高 |
| P2 | **无 Supervisor 决策层**: CS 域内没有循环/重试/降级能力 | 复杂场景无法处理 | 高 |
| P3 | **cs_context 是无 schema 的 dict** | 字段散落各处，无类型安全 | 中 |
| P4 | **状态机与 Graph State 割裂**: 状态机在 PG，Graph State 是内存快照 | 跨请求恢复时可能不一致 | 高 |
| P5 | **无 Checkpointer**: 每次请求都是全新 Graph 执行 | 无法实现"等待确认→恢复执行" | 高 |
| P6 | **cs_pending_node 是空桩**: 确认流程硬编码在 `cs_business_action` 内部 | 确认逻辑与业务动作耦合 | 中 |
| P7 | **Trace 粒度不足**: CS 节点只有单 span | 前端无法可视化多步决策过程 | 低 |
| P8 | **CS 与 Skill 体系完全隔离**: 不复用 RAG/SQL/Tool 能力 | 重复实现 | 低 (当前可接受) |

## 2.2 不急于重构的部分

- 三套状态机本身设计合理，纯函数 + 外部持久化，转换规则清晰
- Router 体系 (DomainDetector + CSRouter) 分类准确率高
- 安全层 (InputGuard / OutputGuard / PermissionChecker) 工作正常
- Store 层 (ConfirmationStore / HandoffStore) L1 + DB 双层架构合理
- 业务服务层 (OrderService / LogisticsService / RefundService 等) 可直接复用

---

# 3. 为什么现在需要 CS Supervisor

## 3.1 业务驱动

当前架构能满足**单轮单意图**场景，但以下场景无法处理:

1. **组合意图**: "我的订单到了吗？如果到了帮我退款" → 需要先查订单，再判断条件，再执行退款
2. **多轮确认**: 用户说"帮我退款" → 系统需要确认细节 → 用户确认 → 执行。当前靠 `cs_business_action` 内部状态机硬撑，但 Graph 已执行完毕
3. **Expert 协作**: 物流查询结果可能影响投诉分类，当前无法串联
4. **动态降级**: 某个 Expert 执行失败后，无法由 Supervisor 决策重试或转人工

## 3.2 技术驱动

- LangGraph `Command` 提供了结构化路由能力，比 `route_selector` + `cs_target` dict 更清晰
- Checkpointer 可以天然解决"跨请求恢复"问题
- 独立 CS Graph 可以让 CS 域保持独立性，不污染主图

## 3.3 不需要 Supervisor 的场景

- 简单知识问答 (直接 RAG)
- 单步查询 (直接 SQL)
- 纯转人工 (直接 Handoff)

这些场景 Supervisor 只增加一次 LLM 调用延迟。设计时需考虑**快速通道**绕过 Supervisor。

## 3.4 哪些复杂度是真正解决业务问题的

| 复杂度引入 | 解决的业务问题 | 是否必要 |
|-----------|--------------|---------|
| CS Supervisor | 组合意图、多步协作、动态降级 | 必要 |
| Expert Agent | 解耦能力，支持复用和独立测试 | 必要 |
| Command | 结构化路由，比 dict 条件边更清晰 | 必要 |
| 独立 CS Graph | 隔离 CS 域，不污染主图 | 必要 |
| Checkpointer | 解决跨请求确认恢复 (核心痛点) | 必要 |
| StateTransitionService | 解决状态一致性问题 | 必要 |

---

# 4. 目标架构

## 4.1 最终拓扑

```
                         API
                          │
                          ▼
                    ┌───────────┐
                    │ Main Graph│
                    └─────┬─────┘
                          │
                          ▼
                    ┌───────────┐
                    │Main Router│
                    └─────┬─────┘
                          │
        ┌─────────────────┼──────────────────┐
        │                 │                  │
        ▼                 ▼                  ▼
     Planner           Workflow        cs_graph_node
        │                                      │
        ▼                                      ▼
Main Supervisor                          ┌──────────┐
        │                                │ CS Graph │
     Skills                              └────┬─────┘
                                              │
                                         CS Router
                                              │
                                        CS Supervisor
                                              │
                            ┌─────────────────┼────────────────┐
                            ▼                 ▼                ▼
                      KnowledgeExpert    QueryExpert    ActionExpert
                            │                 │                │
                            ▼                 ▼                ▼
                           RAG             Service       Confirmation
                                                             │
                                                        Action Service
                                                             │
                                                             ▼
                                                        CS Reporter
                                                             │
                                                             ▼
                                                       CSGraphResult
                                                             │
                                                             ▼
                                                      cs_graph_node
                                                             │
                                                             ▼
                                                       Main State
                                                             │
                                                             ▼
                                                            END
                                                             │
                                                             ▼
                                                            API
```

## 4.2 关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| CS 域实现方式 | **独立 CS Graph** (不是 SubGraph) | 独立 State、Checkpointer、生命周期 |
| Main Router 调用方式 | `add_conditional_edges` → `cs_graph_node` | Router 只负责路由，不负责执行 |
| cs_graph_node 职责 | Main State ↔ CS Graph 适配器 | 职责分离，避免 Router 职责过重 |
| CS Graph 输出 | `CSGraphResult` (契约) | Contract-based integration，不是 State 共享 |
| Supervisor 位置 | CS Graph 内部 | CS 域内循环不影响 Main Graph |
| Expert 与 Service 关系 | Expert 调用现有 Service | 不重复实现能力 |
| 状态机保留 | 保留三套状态机，不迁入 LangGraph State | 它们是业务状态，不是执行状态 |
| Checkpointer | CS Graph 独立启用 | 解决跨请求确认恢复 |
| Command | CS Graph 内部 Supervisor → Expert | 结构化路由 |
| Main Supervisor | **不参与** CS 内部调度 | 进入 CS Graph 后，控制权完全由 CS Supervisor 接管 |

---

# 5. Router / CS Supervisor / Expert 职责边界

## 5.1 Main Router (已有，不修改)

**职责**: 判断请求属于哪个**大域**

```
输入: user_message
输出: route_mode ∈ {plan, direct, workflow, customer_service}
```

**不做**: CS 域内的细粒度分类

## 5.2 CS Router (已有，微调)

**职责**: CS 域内的**粗分类** — 判断意图类型

```
输入: user_message
输出: CSRouteResult {domain, intent, confidence, route_path, kb_ids, ...}
```

**当前实现**: DomainDetector + CSCoarseRouter + CSFineRouter
**保留**: 全部保留，作为 CS Supervisor 的前置信息

## 5.3 CS Supervisor (新增)

**职责**: CS 域内的**细粒度决策** — 结合状态 + 上下文决定下一步

### 5.3.1 分层决策模型 (规则优先，LLM 兜底)

CS Supervisor **不应该每一步都调用 LLM**。采用三层决策模型:

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 规则/状态快速决策 (无 LLM, < 1ms)                 │
│                                                             │
│  - handoff_state != ai_active → 拦截转人工                   │
│  - confirmation_state == PENDING → 进入 pending_handler     │
│  - expert_loop_count > MAX → 强制结束                        │
│  - cs_route.confidence < threshold → 降级到 knowledge        │
│  - 明确的单步意图 (confidence > 0.9) → 直接分发 Expert       │
│                                                             │
│  命中即返回，不进入 Layer 2                                   │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: 状态组合决策 (无 LLM, < 5ms)                      │
│                                                             │
│  - expert_history 非空 + 上一步成功 → 判断是否需要后续步骤    │
│  - 组合意图拆解 (基于 intent 规则表)                         │
│  - 确认流程状态推进 (PENDING → CONFIRMED → EXECUTING)        │
│                                                             │
│  命中即返回，不进入 Layer 3                                   │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: LLM 决策 (仅复杂场景, 200-500ms)                  │
│                                                             │
│  - 模糊/多意图混合，规则无法判定                              │
│  - 需要理解上下文才能决定下一步                               │
│  - Expert 返回异常，需要智能降级决策                          │
│  - 用户表述与已有 intent 不匹配                              │
│                                                             │
│  仅在 Layer 1/2 未命中时触发                                 │
└─────────────────────────────────────────────────────────────┘
```

### 5.3.2 Supervisor 输入输出

```
输入:
  - latest_user_message
  - cs_route (来自 CS Router)
  - conversation_state (来自 ConversationManager)
  - handoff_state (来自 HandoffStore)
  - confirmation_state (来自 ConfirmationStore)
  - expert_history (本轮已执行的 Expert 列表)
  - last_expert_result (上一个 Expert 的输出)

输出: CSSupervisorDecision (结构化)
  - next_action: ExpertAction 枚举
  - next_expert: ExpertType 枚举 | None
  - decision_layer: 1 | 2 | 3          # 记录由哪层决策 (可观测性)
  - reason: str
  - requires_confirmation: bool
  - requires_handoff: bool
  - is_finished: bool
  - context_updates: dict
```

**不做**:
- 不执行任何业务逻辑
- 不直接操作数据库
- 不做域外路由 (那是 Main Router 的事)
- 不生成最终回复 (那是 Reporter 的事)
- 不在 Layer 1/2 可决策时调用 LLM

## 5.4 Expert Agent (新增)

**职责**: 执行具体的 CS 能力

```
输入:
  - user_message
  - cs_route (路由信息)
  - conversation_context
  - skill_inputs (来自 Supervisor 的参数)

输出: ExpertResult
  - status: success / failed / needs_confirmation / needs_handoff
  - data: dict (查询结果、操作提案等)
  - response_draft: str | None         # 结构化草稿 (Reporter 组装用)
  - evidence: list[dict] | None        # 引用/证据 (知识问答场景)
  - action_result: dict | None         # 操作执行结果 (业务动作场景)
  - service_calls: list (调用了哪些 Service)
  - next_suggestion: str | None (建议下一步做什么)
```

**不做**:
- 不自己实现 SQL/RAG/Tool (调用现有 Service)
- 不决定路由 (那是 Supervisor 的事)
- 不生成最终用户回复 (那是 Reporter 的事，Expert 只提供 response_draft)
- 不直接修改状态机 (通过 State 更新请求 Supervisor 处理)

---

# 6. Expert Agent 设计

## 6.1 当前节点分析

| 当前节点 | 当前职责 | 本质 | 是否保留 | 升级为 Expert | Expert 职责 | 依赖 |
|---------|---------|------|---------|--------------|-----------|------|
| `cs_knowledge_node` | 调用 RAGPipeline 回答 | Node + 薄封装 | 保留逻辑 | `KnowledgeExpert` | 知识问答 | `CSKnowledgeService` → `RAGPipeline` |
| `cs_business_query` | 只读查询订单/物流 | Node + 服务调度 | 保留逻辑 | `QueryExpert` | 业务查询 | `OrderService`, `LogisticsService` |
| `cs_business_action` | 写操作 + 确认门控 | Node + 状态机 + 服务 | **拆分** | `ActionExpert` | 业务动作 | `RefundService`, `AfterSalesService`, `ConfirmationStateMachine` |
| `cs_pending_node` | 占位桩 | 空实现 | **删除** | 不需要 | 由 Supervisor 的 pending 决策替代 | 无 |
| `cs_complaint` | 投诉检测 + 工单 | Node + 服务 | 保留逻辑 | `ComplaintExpert` | 投诉处理 | `ComplaintService` |
| `cs_handoff` | 转人工 | Node + 状态机 | 保留逻辑 | `HandoffExpert` | 转人工 | `HandoffStateMachine`, `HandoffStore` |
| `cs_handoff_intercept` | 人工状态拦截 | 拦截器 | **迁入 Supervisor** | 不独立为 Expert | 由 Supervisor 入口检查替代 | `HandoffStore` |

## 6.2 Expert 与 Service 的关系

```
CS Supervisor
    │
    ├── KnowledgeExpert ──→ CSKnowledgeService ──→ RAGPipeline (已有)
    │
    ├── QueryExpert ──→ OrderService / LogisticsService (已有)
    │
    ├── ActionExpert ──→ RefundService / AfterSalesService (已有)
    │                  ──→ ConfirmationStateMachine (已有)
    │                  ──→ ConfirmationStore (已有)
    │
    ├── ComplaintExpert ──→ ComplaintService (已有)
    │
    └── HandoffExpert ──→ HandoffStateMachine (已有)
                         ──→ HandoffStore (已有)
```

**关键原则**: Expert 是**编排层**，不是**能力层**。所有实际能力继续复用现有 `customer_service/service/` 下的单例服务。

## 6.3 Expert 不重复实现的能力

| 能力 | 当前实现 | Expert 如何复用 |
|------|---------|---------------|
| RAG | `RAGPipeline` (`backend/rag/pipeline.py`) | `CSKnowledgeService` 已封装 |
| 权限校验 | `PermissionChecker` (`security/permission.py`) | Expert 调用前由 Supervisor 统一校验 |
| 输入/输出安全 | `InputGuard` / `OutputGuard` | 在 Expert 入口/出口统一调用 |
| 审计 | `build_audit_entry` + `append_audit` | Expert 执行后记录审计 |

---

# 7. CSGraphState 与 cs_graph_node 设计

## 7.1 设计原则

1. **CS Graph 使用独立的 State** (`CSGraphState`)，与 Main Graph 的 `CSAgentState` 完全分离
2. **持久化状态** (conversation/confirmation/handoff) 不进 Graph State，只放引用 ID
3. **Graph State 只放执行态**: 当前决策需要的上下文
4. **类型安全**: 所有字段用 TypedDict + Enum 约束
5. **Contract-based integration**: CS Graph 通过 `CSGraphResult` 契约与 Main Graph 通信，不是 State 共享

## 7.2 CSGraphState 定义

```python
# backend/customer_service/graph_state.py (新文件)

class CSGraphState(TypedDict):
    # === 输入 (从 cs_graph_node 传入) ===
    user_message: str                          # 最新用户消息
    user_id: str                               # 用户 ID
    session_id: str                            # 会话 ID
    conversation_id: str                       # 对话 ID
    cs_route: dict                             # CS Router 输出 (CSRouteResult dump)

    # === 执行态 (Supervisor + Expert 读写) ===
    supervisor_decision: dict                  # CSSupervisorDecision dump
    expert_history: list[dict]                 # 已执行 Expert 记录列表
    last_expert_result: dict                   # 上一个 Expert 的输出
    current_expert: str                        # 当前执行的 Expert 名称
    expert_loop_count: int                     # Expert 循环计数 (防无限循环)

    # === 状态快照 (从 Store 加载，Expert/Supervisor 可读) ===
    conversation_status: str                   # open/pending/resolved/snoozed
    handling_mode: str                         # ai/human/waiting_human
    handoff_state: str                         # ai_active/handoff_requested/...
    confirmation_state: str                    # not_required/pending/confirmed/...
    pending_action: dict | None                # 待确认的操作 (引用，非 Source of Truth)

    # === 输出 (CS Reporter 生成) ===
    final_answer: str                          # 最终回复
    cs_context: dict                           # CS 上下文快照
    cs_audit_entries: list[dict]               # 审计记录
    cs_action_result: dict                     # 动作执行结果
```

## 7.3 cs_graph_node 适配器设计

`cs_graph_node` 是 Main Graph 与 CS Graph 之间的**适配器**，职责明确:

```python
# backend/orchestration/graph/cs_graph_node.py (新文件)

def cs_graph_node(state: CSAgentState) -> dict:
    """
    Main Graph 与 CS Graph 的适配器
    
    职责:
    1. 将 Main State (CSAgentState) 转换为 CS Graph Input
    2. 调用 CS Graph
    3. 将 CS Graph Output (CSGraphResult) 转换回 Main State
    """
    
    # 1. 构建 CS Graph 输入
    cs_graph_input = {
        "user_message": state["question"],
        "user_id": state.get("cs_context", {}).get("authenticated_user_id"),
        "session_id": state.get("session_id"),
        "conversation_id": state.get("cs_context", {}).get("conversation_id"),
        "cs_route": state.get("cs_context", {}).get("cs_route", {}),
    }
    
    # 2. 调用 CS Graph
    cs_graph = get_cs_graph()
    cs_result: CSGraphResult = cs_graph.invoke(cs_graph_input)
    
    # 3. 将 CSGraphResult 写回 Main State
    return {
        "final_answer": cs_result["final_answer"],
        "cs_context": {
            **state.get("cs_context", {}),
            "conversation_id": cs_result["conversation_id"],
            "handoff_state": cs_result.get("handoff_state"),
            "confirmation_state": cs_result.get("confirmation_state"),
        },
        "cs_action_result": cs_result.get("action_result"),
        "cs_audit_entries": cs_result.get("audit_entries", []),
    }
```

**关键原则**:
- `cs_graph_node` **不执行任何业务逻辑**，只做 State 转换
- `cs_graph_node` **不负责异常处理**，CS Graph 内部处理自己的异常
- `cs_graph_node` 的输入输出都是 `CSGraphResult` 契约，不是完整的 CS State

## 7.4 CSGraphResult 契约

```python
# backend/customer_service/models/graph_result.py (新文件)

class CSGraphResult(TypedDict):
    """CS Graph 输出契约"""
    final_answer: str                          # 最终回复 (必填)
    conversation_id: str                       # 对话 ID (必填)
    action_result: dict | None                 # 业务动作结果 (可选)
    answer_meta: dict | None                   # 回复元数据 (可选，如 evidence)
    handoff_state: str | None                  # 转人工状态 (可选)
    confirmation_state: str | None             # 确认状态 (可选)
    audit_entries: list[dict]                  # 审计记录 (可选)
    status: str                                # success / failed / needs_handoff
```

**为什么需要 CSGraphResult?**
- **解耦**: Main Graph 不需要知道 CS Graph 的内部 State 结构
- **稳定性**: CS Graph 内部 State 变化不影响 Main Graph
- **可测试性**: 可以独立测试 CS Graph 的输出契约
- **类型安全**: 明确的输入输出类型定义

## 7.5 Main Graph 路由配置

```python
# backend/orchestration/graph/builder.py

def build_main_graph():
    wf = StateGraph(CSAgentState)
    
    # 注册节点
    wf.add_node("router", router_node)
    wf.add_node("planner", planner_node)
    wf.add_node("supervisor", supervisor_node)
    wf.add_node("workflow_executor", workflow_executor_node)
    wf.add_node("skill_executor", skill_executor_node)
    wf.add_node("cs_graph_node", cs_graph_node)  # 新增
    wf.add_node("reporter", reporter_node)
    
    # 路由配置
    wf.add_conditional_edges(
        "router",
        route_selector,
        {
            "plan": "planner",
            "workflow": "workflow_executor",
            "direct": "skill_executor",
            "customer_service": "cs_graph_node",  # CS 路径
        },
    )
    
    # CS 路径: cs_graph_node → END (不经过 reporter)
    wf.add_edge("cs_graph_node", END)
    
    # 其他路径: 最终到 reporter
    wf.add_edge("planner", "supervisor")
    wf.add_edge("workflow_executor", "reporter")
    wf.add_edge("skill_executor", "reporter")
    wf.add_edge("reporter", END)
    
    return wf.compile()
```

## 7.6 State 字段设计表

| 字段 | 类型 | 来源 | 谁写 | 谁读 | 持久化 | 原因 |
|------|------|------|------|------|--------|------|
| `user_message` | `str` | cs_graph_node | cs_graph_node | Expert, Supervisor | 否 (DB 有) | 当前轮用户输入 |
| `user_id` | `str` | cs_graph_node | cs_graph_node | Expert, Supervisor | 否 (DB 有) | 权限校验 |
| `conversation_id` | `str` | cs_graph_node | cs_graph_node | Expert, Supervisor | 否 (DB 有) | 关联对话 |
| `cs_route` | `dict` | CS Router | CS Router | Supervisor, Expert | 否 | 路由决策结果 |
| `supervisor_decision` | `dict` | CS Supervisor | Supervisor | Expert, Reporter | 否 | 决策透明性 |
| `expert_history` | `list[dict]` | Expert | 每个 Expert 追加 | Supervisor | 否 | 多轮协作上下文 |
| `last_expert_result` | `dict` | Expert | 当前 Expert | Supervisor | 否 | 决策依据 |
| `current_expert` | `str` | Supervisor | Supervisor | Trace | 否 | 追踪 |
| `expert_loop_count` | `int` | Supervisor | Supervisor | Supervisor | 否 | 防无限循环 |
| `conversation_status` | `str` | ConversationManager | Supervisor 加载 | Supervisor, Expert | **是 (PG)** | 业务状态 |
| `handling_mode` | `str` | ConversationManager | Supervisor 加载 | Supervisor | **是 (PG)** | 业务状态 |
| `handoff_state` | `str` | HandoffStore | Supervisor 加载 | Supervisor, Expert | **是 (PG)** | 业务状态 |
| `confirmation_state` | `str` | ConfirmationStore | Supervisor 加载 | Supervisor, Expert | **是 (PG)** | 业务状态 |
| `pending_action` | `dict\|None` | ConfirmationStore | Supervisor 加载 | Expert | **是 (PG)** | 业务数据 |
| `final_answer` | `str` | Reporter | Reporter | cs_graph_node | 否 (DB 有) | 回复内容 |
| `cs_context` | `dict` | 多来源汇总 | Supervisor | cs_graph_node | 部分 (回传快照) | 向后兼容 |
| `cs_audit_entries` | `list[dict]` | Expert | Expert | cs_graph_node→DB | 否 (写时入库) | 审计 |

## 7.7 Source of Truth 结论

> **PostgreSQL 是业务状态的唯一 Source of Truth。Checkpoint 只是可恢复执行游标，不是 Source of Truth。**

具体规则:
- `conversation_status`, `handling_mode`, `handoff_state`, `confirmation_state`, `pending_action` → **PostgreSQL 是唯一 Source of Truth**
- Graph State 中的这些字段是**快照**，在 CS Graph 入口处从 Store 加载，在 Expert 执行后通过 Store 写回
- `expert_history`, `supervisor_decision`, `last_expert_result` → **CSGraphState 是唯一 Source of Truth** (纯执行态，不需要持久化)
- `final_answer`, `cs_audit_entries` → **PostgreSQL 是 Source of Truth** (通过 `record_cs_turn` 持久化)

**Checkpoint 的角色**:
- Checkpoint 只保存 `CSGraphState` 的完整快照 (包括执行态和业务态快照)
- 恢复时:**必须从 PostgreSQL 重新加载业务状态快照**，不信任 Checkpoint 中的业务态
- Checkpoint 的作用是**记录执行到哪一步** (expert_history, current_expert)，不是记录业务状态
- 业务状态变更必须通过 `StateTransitionService` 写入 PostgreSQL，Checkpoint 不参与业务状态管理

## 7.8 主图 CSAgentState 变更

主图 `CSAgentState` 保持现有字段不变 (向后兼容)，但 `cs_context` 的内部结构由 `CSGraphResult` 回传时统一填充。

---

# 8. 三套状态机与 LangGraph State 的关系

## 8.1 分类

| 状态机 | 属于 | 理由 |
|--------|------|------|
| Conversation (open/pending/resolved/snoozed + ai/human/waiting_human) | **业务状态** | 跨请求持久化，影响业务逻辑 |
| Confirmation (pending/confirmed/executing/success/failed/expired) | **业务状态** | 跨请求持久化，涉及资金操作 |
| Handoff (ai_active/handoff_requested/waiting_human/human_active/closed) | **业务状态** | 跨请求持久化，影响路由 |

**结论**: 三套状态机全部属于业务状态，全部保留在 PostgreSQL 中，不迁入 LangGraph State。

## 8.2 与 LangGraph State 的关系

```
┌─────────────────────────────────────────────────────┐
│                   CSGraphState                       │
│                                                     │
│  conversation_status ◄── 快照自 ── ConversationDB   │
│  handling_mode       ◄── 快照自 ── ConversationDB   │
│  handoff_state       ◄── 快照自 ── HandoffDB        │
│  confirmation_state  ◄── 快照自 ── ConfirmationDB   │
│  pending_action      ◄── 快照自 ── ConfirmationDB   │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │  Expert / Supervisor 需要修改状态时:         │    │
│  │                                             │    │
│  │  1. 不直接修改 Graph State 中的状态字段      │    │
│  │  2. 通过 StateTransitionRequest 表达意图     │    │
│  │  3. 由 StateTransitionService 统一执行:      │    │
│  │     a. 调用状态机 transition() 验证          │    │
│  │     b. 写入 PostgreSQL                      │    │
│  │     c. 更新 Graph State 快照                │    │
│  └─────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## 8.3 状态转换规则

| 问题 | 回答 |
|------|------|
| 状态转换函数是否保留？ | **保留**。三套状态机的 `transition()` 纯函数全部保留 |
| Supervisor 是否可以直接修改状态？ | **不可以**。Supervisor 输出 `StateTransitionRequest`，由 `StateTransitionService` 执行 |
| Expert 是否可以直接修改状态？ | **不可以**。同上 |
| 统一修改入口 | 新增 `StateTransitionService` |
| 如何避免不一致？ | Graph State 中的状态字段只读，写操作必须通过 `StateTransitionService`，该服务同时更新 DB 和 Graph State |

## 8.4 StateTransitionService 设计

```python
# backend/customer_service/state_transition.py (新文件)

class StateTransitionRequest(TypedDict):
    conversation_id: str
    user_id: str
    # 以下可选，至少一个非空
    new_conversation_status: str | None
    new_handling_mode: str | None
    new_handoff_state: str | None
    new_confirmation_state: str | None
    pending_action: dict | None
    reason: str                            # 转换原因 (审计用)

class StateTransitionResult(TypedDict):
    success: bool
    conversation_status: str               # 转换后的实际值
    handling_mode: str
    handoff_state: str
    confirmation_state: str
    errors: list[str]
```

**职责**:
1. 接收转换请求
2. 调用对应状态机的 `transition()` 验证合法性
3. 写入 PostgreSQL (通过 ConversationManager / ConfirmationStore / HandoffStore)
4. 返回转换后的状态快照 (供 Graph State 更新)

---

# 9. Command 设计

## 9.1 当前状态

项目完全没有 `Command`，使用 `Send` 做并行分发，使用 `add_conditional_edges` + 路由函数做条件分发。

## 9.2 Command 引入分析

| 问题 | 回答 |
|------|------|
| Supervisor 是否应该返回 Command？ | **是**。Supervisor 的 `CSSupervisorDecision` 天然适合用 `Command(goto=...)` 表达 |
| Expert 是否应该返回 Command？ | **否**。Expert 返回普通 State 更新，由 Supervisor 决策下一步 |
| 哪些情况用普通 State 更新？ | Expert 执行结果、状态快照更新 |
| 哪些情况用 `Command(goto=...)` | Supervisor 决策后路由到 Expert / Reporter / Pending |
| 是否需要 `Command.PARENT`？ | **CS Graph 内部不需要**。Expert 完成后通过固定 `add_edge` 回到 Supervisor，不需要 `Command.PARENT`。CS Graph 是独立图，不存在父子关系 |
| 是否需要跨图？ | 不需要。CS Graph 通过 `cs_graph_node` 适配器与 Main Graph 通信，使用 `CSGraphResult` 契约 |
| 是否还需要 `add_conditional_edges`？ | CS Graph 内部用 Command 替代；Main Graph 到 CS Graph 的入口仍用 `add_conditional_edges` |
| 是否保留 route_selector？ | **保留**。Main Graph 的 route_selector 不变 |
| Command 和 Send 如何共存？ | CS Graph 内部用 Command (串行决策)；Main Graph 的 Supervisor 继续用 Send (并行分发) |
| 是否会破坏 Main Planner→Supervisor→Skill 体系？ | **不会**。CS Graph 完全独立 |

## 9.3 CS Graph 内部 Command 使用方案

```python
# CS Supervisor 节点返回 Command
def cs_supervisor_node(state: CSGraphState) -> Command:
    decision = make_supervisor_decision(state)

    if decision["is_finished"]:
        return Command(goto="cs_reporter", update={
            "supervisor_decision": decision,
        })

    if decision["requires_confirmation"] and not _is_confirmed(state):
        return Command(goto="cs_pending_handler", update={
            "supervisor_decision": decision,
        })

    if decision["requires_handoff"]:
        return Command(goto="cs_handoff_expert", update={
            "supervisor_decision": decision,
        })

    next_expert = decision["next_expert"]
    return Command(goto=next_expert, update={
        "supervisor_decision": decision,
        "current_expert": next_expert,
    })
```

```python
# Expert 节点返回普通 State 更新
def knowledge_expert(state: CSGraphState) -> dict:
    result = execute_knowledge(state)
    return {
        "last_expert_result": result,
        "expert_history": [*state["expert_history"], result],
    }
```

```python
# Expert → Supervisor 的边 (固定边)
cs_graph.add_edge("cs_knowledge_expert", "cs_supervisor")
cs_graph.add_edge("cs_query_expert", "cs_supervisor")
cs_graph.add_edge("cs_action_expert", "cs_supervisor")
cs_graph.add_edge("cs_complaint_expert", "cs_supervisor")
cs_graph.add_edge("cs_handoff_expert", "cs_supervisor")
```

## 9.4 推荐方案总结

| 层 | 路由方式 | 原因 |
|----|---------|------|
| Main Graph Router → 各域 | `add_conditional_edges` + `route_selector` | 已有，稳定 |
| Main Graph Supervisor → Skill | `Send` (并行) | 已有，适合 DAG 并行 |
| CS Graph Supervisor → Expert | `Command(goto=...)` | 串行决策，结构化路由 |
| CS Graph Expert → Supervisor | 固定 `add_edge` | 所有 Expert 都回到 Supervisor |
| Main Graph → CS Graph | `cs_graph_node` 适配器 + `CSGraphResult` 契约 | 独立图，契约式通信 |

---

# 10. Checkpointer / Persistence 设计

## 10.1 三种持久化需求分析

| 需求 | 当前方案 | 问题 | 目标方案 |
|------|---------|------|---------|
| **Conversation persistence** | PostgreSQL `CSConversation` + `CSMessage` | 无问题 | **保留** |
| **Business state persistence** | `ConfirmationStore` + `HandoffStore` (L1+PG) | 无问题 | **保留** |
| **Agent execution checkpoint** | **不存在** | 无法实现"等待确认→恢复执行" | **引入 LangGraph Checkpointer** |

## 10.2 Checkpointer 引入方案

```python
# 仅 CS Graph 使用 Checkpointer
from langgraph.checkpoint.postgres import PostgresSaver

cs_graph = cs_graph.compile(
    checkpointer=PostgresSaver.from_pool(pg_pool),
)
```

**Checkpoint 内容**: `CSGraphState` 的完整快照
**Checkpoint Key**: `thread_id = conversation_id`

## 10.3 三者关系

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  PostgreSQL (业务层)          LangGraph Checkpoint (执行层)  │
│  ┌────────────────────┐      ┌────────────────────────┐     │
│  │ CSConversation     │      │ CSGraphState 快照      │     │
│  │ CSMessage          │      │ (expert_history,       │     │
│  │ CSConfirmation     │      │  supervisor_decision,  │     │
│  │ CSHandoff          │      │  pending_action, ...)  │     │
│  └────────────────────┘      └────────────────────────┘     │
│           ▲                            ▲                     │
│           │                            │                     │
│    StateTransitionService       Checkpointer                │
│    (业务状态写入)               (执行状态保存/恢复)          │
│                                                              │
│  职责分离:                                                   │
│  - 业务状态 → PostgreSQL 是唯一 Source of Truth              │
│  - 执行状态 → Checkpoint 是唯一 Source of Truth              │
│  - 两者通过 conversation_id 关联                             │
│  - Graph 恢复时: 从 Checkpoint 恢复执行态，                  │
│    从 PostgreSQL 重新加载业务态快照                           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## 10.4 跨请求恢复流程 (双保险机制)

**不依赖 Checkpoint Resume 作为唯一方案**。支持两种恢复路径:

### 路径 A: Checkpoint Resume (首选)

```
请求 1: "帮我退款"
  → Main Router → cs_graph_node → CS Graph 启动
  → Supervisor → ActionExpert → 构建提案 → 需要确认
  → StateTransitionService: confirmation_state = PENDING_CONFIRMATION
  → StateTransitionService: pending_action 写入 ConfirmationStore (PostgreSQL)
  → Checkpointer: 保存 Graph State (暂停在 cs_pending_handler)
  → CSGraphResult 传回 Main Graph → cs_graph_node → END
  → 返回用户: "请确认退款操作..."

请求 2: "确认"
  → Main Router → cs_graph_node → CS Graph
  → Checkpointer: 检测到 conversation_id 有活跃 checkpoint
  → 恢复 Graph State (expert_history, supervisor_decision 等)
  → 重新从 PostgreSQL 加载业务状态快照 (确保最新)
  → cs_pending_handler: 检测用户意图 = CONFIRM
  → StateTransitionService: confirmation_state = USER_CONFIRMED → EXECUTING → SUCCESS
  → Supervisor: is_finished = true
  → CS Reporter → CSGraphResult → cs_graph_node → END
  → Checkpointer: 清除 checkpoint (对话完成)
```

### 路径 B: DB State Reconstruction (降级方案)

**当 Checkpoint 不可用或损坏时**，从 PostgreSQL 重建执行上下文:

```
请求 2: "确认" (Checkpoint 丢失/损坏)
  → Main Router → cs_graph_node → CS Graph
  → Checkpointer: 无活跃 checkpoint
  → cs_state_loader 节点:
    1. 从 ConfirmationStore 读取: confirmation_state = PENDING_CONFIRMATION
    2. 从 ConfirmationStore 读取: pending_action = {退款提案}
    3. 从 ConversationManager 读取: conversation 历史
    4. 重建 CSGraphState:
       - confirmation_state = "pending"
       - pending_action = {...}
       - expert_history = [] (丢失，但不影响)
       - current_expert = "cs_pending_handler" (根据 confirmation_state 推断)
  → cs_pending_handler: 检测用户意图 = CONFIRM
  → StateTransitionService: PENDING → CONFIRMED → EXECUTING → SUCCESS
  → Supervisor: is_finished = true
  → CS Reporter → CSGraphResult → cs_graph_node → END
```

### 双保险设计原则

| 原则 | 说明 |
|------|------|
| Checkpoint 是**优化**，不是**依赖** | 有 Checkpoint 可以恢复 expert_history 等执行态；没有也能从 DB 重建核心业务态 |
| PostgreSQL 是**唯一 Source of Truth** | 所有业务状态 (confirmation_state, pending_action) 必须写入 PostgreSQL，不能只存在 Checkpoint 中 |
| 恢复时**重新加载业务态** | 无论走哪条路径，都必须从 PostgreSQL 重新加载业务状态，不信任 Checkpoint 中的业务态快照 |
| expert_history 可丢失 | 即使 Checkpoint 丢失导致 expert_history 为空，只要 DB 中的 confirmation_state 和 pending_action 完整，就能继续执行 |

## 10.5 不需要 Checkpoint 的场景

- 简单知识问答: 单次执行完毕，无需暂停
- 纯查询: 无状态操作，无需恢复
- 转人工: 状态写入 HandoffStore 即可

**设计**: Checkpointer 只在 `cs_pending_handler` (等待确认) 节点触发暂停。其他路径正常执行完毕。

---

# 11. Pending / Confirmation / Handoff 设计

## 11.1 场景 1: 普通知识问答

```
用户: "退货政策是什么？"
  │
  ▼
Main Router → customer_service → cs_graph_node
  │
  ▼
CS Router → intent=k_policy, route_path=knowledge_query
  │
  ▼
CS Graph 入口
  │
  ▼
CS Supervisor:
  decision = {
    next_expert: "cs_knowledge_expert",
    is_finished: false,
  }
  │
  ▼
KnowledgeExpert:
  → CSKnowledgeService.answer(kb_ids=["cs_policy"])
  → result = {answer: "7天无理由退货...", confidence: 0.92,
              response_draft: "7天无理由退货...", evidence: [...]}
  │
  ▼
CS Supervisor:
  decision = {
    is_finished: true,
    reason: "知识问答已完成"
  }
  │
  ▼
CS Reporter → 组装最终回复 → CSGraphResult
  │
  ▼
cs_graph_node → Main Graph State → END
```

## 11.2 场景 2: 查询订单

```
用户: "我的订单在哪里？"
  │
  ▼
Main Router → cs_graph_node → CS Graph
  │
  ▼
CS Router → intent=t_order_status, route_path=business_query
  │
  ▼
CS Supervisor:
  → 从 Store 加载: conversation_status=open, handoff_state=ai_active
  decision = {
    next_expert: "cs_query_expert",
    is_finished: false,
  }
  │
  ▼
QueryExpert:
  → OrderService.get_user_orders(user_id)
  → result = {orders: [...], formatted: "## 您的订单\n...",
              response_draft: "## 您的订单\n..."}
  │
  ▼
CS Supervisor:
  decision = { is_finished: true, reason: "查询完成" }
  │
  ▼
CS Reporter → CSGraphResult → cs_graph_node → END
```

## 11.3 场景 3: 查询 + 后续业务动作 (Supervisor 的真正价值)

```
用户: "我的订单到了吗？如果到了就帮我退款"
  │
  ▼
Main Router → cs_graph_node → CS Graph
  │
  ▼
CS Router → intent=t_order_status (首要意图)
  │
  ▼
CS Supervisor (第 1 轮):
  → 分析: 用户有组合意图 (查询 + 条件退款)
  decision = {
    next_expert: "cs_query_expert",
    is_finished: false,
    reason: "先查询订单状态"
  }
  │
  ▼
QueryExpert:
  → OrderService.get_order(order_id)
  → result = {status: "delivered", order: {...},
              response_draft: "您的订单已送达"}
  │
  ▼
CS Supervisor (第 2 轮):
  → 分析: 订单已送达，用户条件满足 ("如果到了就退款")
  → last_expert_result.status = "delivered"
  decision = {
    next_expert: "cs_action_expert",
    is_finished: false,
    requires_confirmation: true,
    reason: "订单已送达，满足退款条件，需用户确认"
  }
  │
  ▼
ActionExpert 构建退款提案
  → StateTransitionService: confirmation_state = PENDING_CONFIRMATION
  → Checkpoint 保存
  → CSGraphResult → cs_graph_node → 返回: "您的订单已送达。确认退款 XXX 元？"
  │
  ▼
  [... 等待用户下一条消息 ...]
  │
  ▼
用户: "确认"
  │
  ▼
Main Router → cs_graph_node → CS Graph 从 Checkpoint 恢复
  → cs_pending_handler: detect_confirmation_intent("确认") = CONFIRM
  → StateTransitionService: PENDING → CONFIRMED → EXECUTING
  │
  ▼
ActionExpert:
  → RefundService.execute_refund(...)
  → result = {status: "success", refund_id: "RF-xxx",
              response_draft: "退款已处理，退款编号 RF-xxx"}
  → StateTransitionService: EXECUTING → SUCCESS
  │
  ▼
CS Supervisor (第 3 轮):
  decision = { is_finished: true, reason: "退款已完成" }
  │
  ▼
CS Reporter → CSGraphResult → cs_graph_node → END
```

## 11.4 Handoff 设计

### cs_handoff_intercept 是否还作为独立 Node？

**推荐: 不再作为独立 Node，逻辑迁入 CS Supervisor 入口检查。**

理由:
1. 当前 `cs_handoff_intercept` 的逻辑很简单 (读 handoff_state → 返回固定回复)
2. 迁入 Supervisor 后，每轮开始自然检查 handoff_state，逻辑更清晰
3. 减少一个 Expert 类型，降低复杂度

实现:
```python
def cs_supervisor_node(state: CSGraphState) -> Command:
    # 入口拦截检查
    if state["handoff_state"] not in ("ai_active", ""):
        return Command(goto="cs_reporter", update={
            "supervisor_decision": {
                "next_action": "intercept_handoff",
                "is_finished": True,
                "reason": f"handoff intercept: {state['handoff_state']}",
            },
        })
    # 正常决策流程...
```

### 用户在人工处理中继续发消息

```
用户: "有人处理了吗？"
  │
  ▼
Main Router → cs_graph_node → CS Graph → CS Supervisor:
  → handoff_state = handoff_requested / human_active
  → 不分配 Expert，直接:
  decision = { is_finished: true, next_action: "intercept_handoff" }
  │
  ▼
CS Reporter:
  → handoff_requested → "正在为您转接..."
  → human_active → "当前由人工客服为您服务..."
  │
  ▼
CSGraphResult → cs_graph_node → END
```

---

# 12. Reporter 重构方案

## 12.1 新增 CS Reporter

**新增 `cs_reporter` 节点** (CS Graph 内部)，不修改 Main Graph 的 `reporter`。

**核心原则**: Reporter **不猜测业务结果**，只负责:
1. **组装**: 从 Expert 的结构化输出 (`response_draft`, `evidence`, `action_result`) 组装最终回复
2. **安全审查**: 执行 Output Guard，过滤敏感信息
3. **话术格式化**: 将业务结果转为用户友好的自然语言

```python
# backend/customer_service/reporter.py (新文件)

def cs_reporter_node(state: CSGraphState) -> dict:
    decision = state["supervisor_decision"]

    # 1. 拦截场景 (固定话术，不需要 Expert 输出)
    if decision.get("next_action") == "intercept_handoff":
        return {"final_answer": _handoff_intercept_reply(state)}

    # 2. 从 Expert 输出组装 (核心逻辑)
    expert_result = state.get("last_expert_result", {})

    # 2a. 优先使用 Expert 提供的 response_draft
    if expert_result.get("response_draft"):
        answer = expert_result["response_draft"]

    # 2b. 如果有 evidence (知识问答)，格式化引用
    elif expert_result.get("evidence"):
        answer = _format_with_evidence(
            draft=expert_result.get("response_draft", ""),
            evidence=expert_result["evidence"],
        )

    # 2c. 如果有 action_result (业务动作)，格式化结果
    elif expert_result.get("action_result"):
        answer = _format_action_result(
            action=expert_result["action_result"],
        )

    # 2d. 降级: 如果 Expert 没有提供 response_draft，才由 Reporter 基于 data 生成
    else:
        answer = _summarize_expert_results(state)

    # 3. Output Guard (安全审查)
    guard_result = get_output_guard().check(answer, state.get("cs_context", {}))
    if guard_result.blocked:
        answer = guard_result.fallback

    # 4. 话术润色 (可选，仅在需要时调用 LLM)
    if _needs_polishing(answer):
        answer = _polish_answer(answer)

    return {
        "final_answer": answer,
        "cs_context": _build_cs_context_snapshot(state),
    }
```

## 12.2 Reporter 规则

| 规则 | 说明 |
|------|------|
| **Expert 必须提供 `response_draft`** | Expert 输出结构化草稿，Reporter 基于此组装，不凭空猜测业务结果 |
| **Expert 提供 `evidence`** | 知识问答场景，Expert 提供引用来源，Reporter 负责格式化 |
| **Expert 提供 `action_result`** | 业务动作场景，Expert 提供执行结果，Reporter 负责格式化 |
| Expert 禁止直接生成 `final_answer` | Expert 只写 `last_expert_result` (含 `response_draft`) |
| Supervisor 不生成回复 | Supervisor 只做决策 |
| `final_answer` 由 `cs_reporter` 统一生成 | 保证 Output Guard 统一执行 |
| Reporter 不猜测业务结果 | 如果 Expert 没有提供 `response_draft`，Reporter 只基于 `data` 字段总结，不编造 |

## 12.3 主图 Reporter 与 CS Reporter 的关系

```
Main Graph reporter: 处理 Planner→Supervisor→Skill 路径的回复
CS Graph cs_reporter: 处理 CS Supervisor→Expert 路径的回复

两者独立，不共享。
CS Graph 的输出通过 CSGraphResult 契约传回 Main Graph 的 cs_graph_node，
Main Graph 直接使用 final_answer，不再经过 Main Graph reporter。
```

---

# 13. Trace 重构方案

## 13.1 当前 Trace 结构

```
trace
  ├── router span (CS_ROUTING)
  │   ├── cs_router span
  │   └── domain_detector span
  └── cs_xxx span (CS_KNOWLEDGE / CS_BUSINESS_QUERY / ...)
```

## 13.2 目标 Trace 结构

```
trace
  ├── router span (CS_ROUTING)
  │   ├── cs_router span
  │   └── domain_detector span
  │
  ├── cs_supervisor span (CS_SUPERVISOR) ← 新增 SpanKind
  │   ├── decision: {next_expert, reason}
  │   └── duration: 200ms
  │
  ├── cs_knowledge_expert span (CS_EXPERT) ← 新增 SpanKind
  │   ├── input: {kb_ids, intent}
  │   ├── cs_knowledge_service span (CS_KNOWLEDGE)
  │   │   └── rag_pipeline span (existing)
  │   ├── output: {answer, confidence}
  │   └── duration: 350ms
  │
  ├── cs_supervisor span (CS_SUPERVISOR) ← 第 2 轮
  │   └── decision: {is_finished: true}
  │
  └── cs_reporter span (CS_REPORTER) ← 新增 SpanKind
      └── duration: 50ms
```

## 13.3 新增 SpanKind

```python
# backend/observability/tracer.py 新增:
class SpanKind:
    ...
    CS_SUPERVISOR = "cs_supervisor"
    CS_EXPERT = "cs_expert"
    CS_REPORTER = "cs_reporter"
    CS_STATE_TRANSITION = "cs_state_transition"
```

## 13.4 前端展示

Supervisor → Expert 循环在前端展示为:

```
┌─ 客服对话 ──────────────────────────────┐
│                                         │
│  1. CS Router: 意图=知识问答 (0.92)      │
│     ↓                                   │
│  2. CS Supervisor: → KnowledgeExpert     │
│     ↓                                   │
│  3. KnowledgeExpert: 命中 (conf=0.92)    │
│     ↓ 调用 RAGPipeline                  │
│  4. CS Supervisor: 完成                  │
│     ↓                                   │
│  5. CS Reporter: 生成回复                │
│                                         │
└─────────────────────────────────────────┘
```

多轮场景:
```
┌─ 客服对话 ──────────────────────────────┐
│                                         │
│  1. CS Supervisor: → QueryExpert         │
│  2. QueryExpert: 订单已送达              │
│  3. CS Supervisor: → ActionExpert (确认) │
│  4. ActionExpert: 等待用户确认           │
│  5. [Checkpoint 等待用户]                │
│  ─── 用户: "确认" ───                    │
│  6. [Resume] ActionExpert 执行退款       │
│  7. CS Supervisor: 完成                  │
│  8. CS Reporter: 退款成功                │
│                                         │
└─────────────────────────────────────────┘
```

---

# 14. Main Supervisor 与 CS Supervisor 边界

## 14.1 架构关系

```
                    Main Router
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
       Planner      Direct/Workflow   cs_graph_node
          │                              │
    Main Supervisor                  CS Graph
          │                              │
    ┌─────┼─────┐              ┌────────┼────────┐
    ▼     ▼     ▼              ▼        ▼        ▼
  SQL   RAG  Report         Knowledge  Query   Handoff
  Skill Skill Skill         Expert   Expert   Expert
                                │        │       │
                                └────────┼───────┘
                                         ▼
                                   CS Services
                              (OrderService, etc.)
```

## 14.2 职责边界

| 维度 | Main Supervisor | CS Supervisor |
|------|----------------|---------------|
| 位置 | Main Graph | CS Graph 内部 |
| 输入 | `plan` (DAG) | `cs_route` + 业务状态 |
| 决策依据 | DAG 依赖关系 + 能力可用性 | 用户意图 + 对话状态 + Expert 历史 |
| 输出 | `Send(skill_node, state)` | `Command(goto=expert)` |
| 并行能力 | 支持 (Send 并行分发) | 不支持 (串行决策) |
| 循环控制 | `MAX_SUPERVISOR_LOOPS=10` | `EXPERT_MAX_LOOPS=5` (更严格) |
| 降级策略 | `execute_degradation()` | 转人工 |
| 状态管理 | `step_results` (Graph State) | PostgreSQL (业务状态) |

## 14.3 不出现的情况

```
X Main Supervisor → CS Supervisor → Main Supervisor (嵌套循环)
X CS Graph → Main Graph Supervisor (CS Graph 不回 Main Graph)
X CS Supervisor 调用 Main Supervisor 的 Skill
X Main Supervisor 知道 CS Expert 的存在
```

两者完全独立，通过 Main Router 的 `route_mode` 分隔。CS Graph 是独立图，通过 `cs_graph_node` 适配器与 Main Graph 通信，**CS Graph 执行完毕后直接到 END，不会回到 Main Graph 的 Supervisor 或其他节点**。

---

# 15. A/B/C 方案对比

## 15.1 方案 A: 单一 Main Graph (CS 节点平铺在主图)

```
Main Graph (StateGraph(CSAgentState))
  ├── router
  ├── planner → supervisor → skills
  ├── cs_knowledge → reporter
  ├── cs_business_query → reporter
  ├── cs_business_action → reporter
  ├── cs_complaint → reporter
  ├── cs_handoff → reporter
  └── ...
```

| 维度 | 评价 |
|------|------|
| State 管理 | **差** — CS 字段继续污染 CSAgentState，cs_context 无 schema |
| Checkpointer | **不可行** — 无法仅对 CS 路径启用 Checkpointer |
| 多轮确认 | **差** — 无法暂停/恢复，只能靠业务层硬撑 |
| 组合意图 | **差** — 单轮单节点，无法 Supervisor 循环 |
| 与 Planner 体系隔离 | **差** — 共享同一张图，互相影响 |
| 测试 | **差** — 需要全图测试，CS 变更影响全局 |
| 可维护性 | **差** — 主图越来越臃肿 |
| 代码改动量 | 小 — 在现有架构上微调 |

**结论**: 无法解决核心业务问题 (组合意图、多轮确认)，不推荐。

## 15.2 方案 B: Main Graph + 独立 CS Graph (推荐)

```
Main Graph (StateGraph(CSAgentState))
  ├── router
  ├── planner → supervisor → skills → reporter
  ├── workflow_executor → reporter
  ├── skill_executor → reporter
  └── cs_graph_node → END    ← 适配器节点
            │
            ▼ (CSGraphResult 契约)
         CS Graph (StateGraph(CSGraphState))  ← 独立图
            ├── cs_supervisor ◄── Expert (固定边)
            ├── cs_reporter → END
            └── 独立 Checkpointer
```

| 维度 | 评价 |
|------|------|
| State 管理 | **优** — CSGraphState 完全独立，不污染 CSAgentState |
| Checkpointer | **优** — 仅 CS Graph 启用，不影响 Main Graph |
| 多轮确认 | **优** — Checkpoint 暂停/恢复 + DB 双保险 |
| 组合意图 | **优** — CS Supervisor 循环调度 Expert |
| 与 Planner 体系隔离 | **优** — 完全独立的图，互不影响 |
| 测试 | **优** — CS Graph 可独立测试，不影响 Main Graph |
| 可维护性 | **优** — CS 域完全封装，独立演进 |
| 契约式集成 | **优** — CSGraphResult 明确定义输入输出 |
| 代码改动量 | 中 — 新建 CS Graph + cs_graph_node 适配器 |
| 对主图侵入性 | **低** — Main Graph 只增加一个 cs_graph_node 和一条 conditional edge |

**结论**: 最佳平衡点。CS 域完全独立，对 Main Graph 低侵入，契约式集成保证稳定性。

## 15.3 方案 C: 双 Endpoint + 独立 CS Graph

```
API Layer
  ├── /api/chat          → Main Graph (分析/规划/执行)
  └── /api/cs-chat       → CS Graph (客服独立入口)
```

| 维度 | 评价 |
|------|------|
| State 管理 | **优** — 完全独立 |
| Checkpointer | **优** — CS Graph 独立管理 |
| 多轮确认 | **优** — 同方案 B |
| 组合意图 | **优** — 同方案 B |
| 与 Planner 体系隔离 | **优** — 物理隔离，不同 API |
| 测试 | **优** — 完全独立测试 |
| 可维护性 | **中** — 两套 API、两套部署、两套监控 |
| 前端集成 | **差** — 前端需要判断调用哪个 API，增加复杂度 |
| 共享能力 | **差** — RAG、权限校验、审计等需要各自实现或抽取公共层 |
| 代码改动量 | **大** — 新建 API + 新建 Graph + 前端适配 |
| 运维成本 | **高** — 两套服务、两套 Checkpointer、两套 Trace |

**结论**: 隔离过度。CS 与 Main 共享大量基础设施 (RAG、权限、审计、Trace)，双 Endpoint 导致重复建设和运维成本翻倍。

## 15.4 方案对比总结

| 维度 | A. 单一 Main Graph | B. Main + 独立 CS Graph | C. 双 Endpoint |
|------|-------------------|------------------------|---------------|
| 解决核心业务问题 | ✗ | ✓ | ✓ |
| State 隔离 | ✗ | ✓ | ✓ |
| Checkpointer | ✗ | ✓ | ✓ |
| 对主图侵入性 | — | **低** | 无 (但代价高) |
| 基础设施复用 | ✓ | ✓ | ✗ |
| 前端改动 | 小 | **小** (同一 API) | 大 (双 API) |
| 运维成本 | 低 | **低** | 高 |
| 代码改动量 | 小 | **中** | 大 |
| 可独立演进 | ✗ | ✓ | ✓ |

## 15.5 推荐

**推荐方案 B: Main Graph + 独立 CS Graph**

原因:
1. CS 域与 Main 域的职责、状态模型、生命周期完全不同，需要独立 Graph
2. 通过 `cs_graph_node` 适配器 + `CSGraphResult` 契约集成，对 Main Graph **业务逻辑低侵入**
3. 共享同一套 API、基础设施 (RAG、权限、审计、Trace)，避免重复建设
4. CS Graph 有独立的 State、Checkpointer、Supervisor、Expert，可独立演进
5. 前端无需改动 — 用户仍然调用同一个 `/api/chat`，由 Main Router 自动分流

---

# 16. 最终推荐方案

## 16.1 方案概述

**Main Graph + 独立 CS Graph + CS Supervisor + Expert Agents + Command + Checkpointer**

- CS 域构建为独立 LangGraph Graph (`CSGraph`)，与 Main Graph 完全分离
- Main Graph 通过 `cs_graph_node` 适配器节点调用 CS Graph
- 两图之间通过 `CSGraphResult` 契约通信，不共享 State
- CS Graph 内部: CS Supervisor 串行决策 → Expert 执行 → 回到 Supervisor
- 使用 `Command(goto=...)` 实现 CS Graph 内部 Supervisor → Expert 路由
- 引入 `PostgresSaver` Checkpointer 支持跨请求暂停/恢复 (仅 CS Graph)
- 三套状态机保留在 PostgreSQL，通过 `StateTransitionService` 统一管理
- Main Graph 业务逻辑低侵入 (仅增加 `cs_graph_node` 适配器和一条 conditional edge)

---

# 17. 完整 LangGraph 拓扑图

## 17.1 Main Graph

```
START
  │
  ▼
router ──[route_selector]──┬──→ planner → critique ──→ supervisor ──[Send]──→ {skill_nodes}
                           │                                                    │
                           │                                                    └──→ supervisor
                           │
                           ├──→ skill_executor ──→ reporter
                           ├──→ workflow_executor ──→ reporter
                           │
                           └──→ cs_graph_node ──→ END
                                  │
                                  ▼ (invoke)
                              CS Graph (独立)
                                  │
                                  ▼ (CSGraphResult)
                              写回 Main State
```

## 17.2 CS Graph (独立)

```
START
  │
  ▼
cs_state_loader ──→ cs_supervisor ◄──────────────────────────────────┐
                     │                                               │
                     ├──[Command]──→ cs_knowledge_expert ────────────┤
                     ├──[Command]──→ cs_query_expert ────────────────┤
                     ├──[Command]──→ cs_action_expert ───────────────┤
                     ├──[Command]──→ cs_complaint_expert ────────────┤
                     ├──[Command]──→ cs_handoff_expert ──────────────┤
                     ├──[Command]──→ cs_pending_handler ─────────────┤
                     │                                               │
                     └──[Command]──→ cs_reporter ──→ END             │
                                                                       │
                     每个 Expert ──→ cs_supervisor (固定边) ──────────┘

CS Graph 输出: CSGraphResult (final_answer, conversation_id, ...)
     │
     ▼
cs_graph_node 适配器 → 写回 Main Graph State → END
```

---

# 18. Migration 分阶段方案

## Phase 0: 架构准备

**目标**: 基础设施就绪，零行为变更

| 操作 | 文件 | 说明 |
|------|------|------|
| CREATE | `backend/customer_service/graph_state.py` | 定义 `CSGraphState` TypedDict |
| CREATE | `backend/customer_service/state_transition.py` | 定义 `StateTransitionService` |
| CREATE | `backend/customer_service/models/graph_result.py` | 定义 `CSGraphResult` 契约 |
| CREATE | `backend/customer_service/graph_builder.py` | `build_cs_graph()` 骨架 |
| CREATE | `backend/customer_service/supervisor.py` | CS Supervisor 骨架 |
| CREATE | `backend/customer_service/reporter.py` | CS Reporter 骨架 |
| CREATE | `backend/orchestration/graph/cs_graph_node.py` | cs_graph_node 适配器 |
| MODIFY | `backend/observability/tracer.py` | 新增 CS SpanKind 枚举值 |

**风险**: 无 (新增文件)
**回归测试**: 现有 578 个 CS 测试全部通过
**验收标准**: 新文件可 import，不破坏现有功能

---

## Phase 1: 统一 CS State / Context

**目标**: 将 `cs_context` 从散乱 dict 升级为结构化 TypedDict

| 操作 | 文件 | 说明 |
|------|------|------|
| CREATE | `backend/customer_service/context.py` | `CSContext` TypedDict 定义 |
| MODIFY | `backend/orchestration/state.py` | `cs_context` 类型注释更新 |
| MODIFY | `backend/orchestration/graph/router_node.py` | 使用 `CSContext` 构建 |
| MODIFY | `backend/customer_service/graph/nodes.py` | 使用 `CSContext` 读写 |

**风险**: 低 (类型注释变更，运行时无变化)
**验收标准**: 所有 CS 测试通过 + 类型检查通过

---

## Phase 2: 引入 CS Supervisor (保留旧 CS Node)

**目标**: CS Supervisor 作为新节点加入，但旧路径仍可用

| 操作 | 文件 | 说明 |
|------|------|------|
| CREATE | `backend/customer_service/supervisor.py` | CS Supervisor 实现 |
| CREATE | `backend/customer_service/experts/knowledge.py` | KnowledgeExpert |
| CREATE | `backend/tests/customer_service/test_cs_supervisor.py` | Supervisor 测试 |
| MODIFY | `backend/customer_service/graph_builder.py` | 构建 CS Graph |

**风险**: 中 (新逻辑并行存在)
**验收标准**: Supervisor 单元测试通过 + 旧路径不受影响

---

## Phase 3: 逐步迁移 Expert

**目标**: 逐个将 CS 节点逻辑迁移到 Expert

| 操作 | 文件 | 说明 |
|------|------|------|
| CREATE | `backend/customer_service/experts/query.py` | QueryExpert |
| CREATE | `backend/customer_service/experts/action.py` | ActionExpert |
| CREATE | `backend/customer_service/experts/complaint.py` | ComplaintExpert |
| CREATE | `backend/customer_service/experts/handoff.py` | HandoffExpert |
| DELETE | `cs_pending_node` 逻辑 | 由 `cs_pending_handler` 替代 |

**风险**: 中 (逐个迁移，每个 Expert 独立验证)
**验收标准**: 每个 Expert 迁移后通过对应场景的集成测试

---

## Phase 4: 引入 Command

**目标**: CS Supervisor 使用 Command 路由

| 操作 | 文件 | 说明 |
|------|------|------|
| MODIFY | `backend/customer_service/supervisor.py` | 返回 Command |
| MODIFY | `backend/customer_service/graph_builder.py` | 调整边配置 |

**风险**: 中 (路由机制变更)
**验收标准**: CS Graph 端到端测试通过

---

## Phase 5: 引入 Checkpointer / Pending Resume

**目标**: 支持跨请求确认恢复

| 操作 | 文件 | 说明 |
|------|------|------|
| CREATE | `backend/customer_service/pending_handler.py` | 确认处理节点 |
| MODIFY | `backend/customer_service/graph_builder.py` | 添加 Checkpointer |
| MODIFY | `backend/orchestration/graph/cs_graph_node.py` | 支持 Checkpoint 恢复 |

**风险**: 高 (跨请求状态管理)
**验收标准**: 多轮确认场景端到端测试通过

---

## Phase 6: 迁移 Handoff / Confirmation

**目标**: 将 Handoff 拦截逻辑迁入 Supervisor

| 操作 | 文件 | 说明 |
|------|------|------|
| MODIFY | `backend/customer_service/supervisor.py` | 入口 handoff 检查 |
| MODIFY | `backend/orchestration/graph/router_node.py` | 移除 handoff 拦截逻辑 |
| MODIFY | `backend/orchestration/graph/builder.py` | 移除 `cs_handoff_intercept` 节点 |

**风险**: 中
**验收标准**: Handoff 场景测试通过

---

## Phase 7: 删除旧 Router / route_selector 逻辑

**目标**: 清理旧 CS 节点，完全切换到 CS Graph

| 操作 | 文件 | 说明 |
|------|------|------|
| MODIFY | `backend/orchestration/graph/builder.py` | 移除旧 CS 节点注册 |
| MODIFY | `backend/orchestration/graph/builder.py` | `route_selector` 中 CS 路径指向 `cs_graph_node` |
| DELETE | `backend/customer_service/graph/nodes.py` 中旧节点函数 | 清理 |

**风险**: 高 (最终切换)
**验收标准**: 全量 CS 回归测试通过 + 性能无退化

---

# 19. 文件级修改清单

## 新增文件

| 文件 | 说明 | 阶段 |
|------|------|------|
| `backend/customer_service/graph_state.py` | `CSGraphState` TypedDict | Phase 0 |
| `backend/customer_service/state_transition.py` | `StateTransitionService` | Phase 0 |
| `backend/customer_service/models/graph_result.py` | `CSGraphResult` 契约 | Phase 0 |
| `backend/customer_service/context.py` | `CSContext` TypedDict | Phase 1 |
| `backend/customer_service/graph_builder.py` | `build_cs_graph()` | Phase 0 |
| `backend/customer_service/supervisor.py` | CS Supervisor | Phase 2 |
| `backend/customer_service/reporter.py` | CS Reporter | Phase 0 |
| `backend/customer_service/pending_handler.py` | 确认处理 | Phase 5 |
| `backend/orchestration/graph/cs_graph_node.py` | cs_graph_node 适配器 | Phase 0 |
| `backend/customer_service/experts/__init__.py` | Expert 包 | Phase 2 |
| `backend/customer_service/experts/base.py` | Expert 基类/接口 | Phase 2 |
| `backend/customer_service/experts/knowledge.py` | KnowledgeExpert | Phase 2 |
| `backend/customer_service/experts/query.py` | QueryExpert | Phase 3 |
| `backend/customer_service/experts/action.py` | ActionExpert | Phase 3 |
| `backend/customer_service/experts/complaint.py` | ComplaintExpert | Phase 3 |
| `backend/customer_service/experts/handoff.py` | HandoffExpert | Phase 3 |
| `backend/tests/customer_service/test_cs_supervisor.py` | Supervisor 测试 | Phase 2 |
| `backend/tests/customer_service/test_cs_experts.py` | Expert 测试 | Phase 3 |
| `backend/tests/customer_service/test_cs_graph.py` | CS Graph 集成测试 | Phase 4 |
| `backend/tests/customer_service/test_state_transition.py` | 状态转换测试 | Phase 0 |
| `backend/tests/customer_service/test_checkpoint.py` | Checkpoint 测试 | Phase 5 |

## 修改文件

| 文件 | 修改内容 | 阶段 | 风险 |
|------|---------|------|------|
| `backend/observability/tracer.py` | 新增 CS SpanKind | Phase 0 | 低 |
| `backend/orchestration/state.py` | cs_context 类型注释 | Phase 1 | 低 |
| `backend/orchestration/graph/router_node.py` | CS 路径指向 cs_graph_node | Phase 7 | 高 |
| `backend/orchestration/graph/builder.py` | 移除旧 CS 节点，添加 cs_graph_node | Phase 7 | 高 |
| `backend/orchestration/graph/cs_graph_node.py` | Checkpoint 恢复适配 | Phase 5 | 中 |
| `backend/customer_service/graph/nodes.py` | 旧节点标记废弃 | Phase 3 | 低 |
| `backend/customer_service/graph/__init__.py` | 导出更新 | Phase 7 | 低 |

## 删除文件

| 文件 | 说明 | 阶段 |
|------|------|------|
| `backend/customer_service/graph/nodes.py` 中的 `cs_pending_node` | 由 pending_handler 替代 | Phase 3 |
| `backend/customer_service/graph/nodes.py` 中的 `cs_handoff_intercept` | 迁入 Supervisor | Phase 6 |
| `backend/customer_service/graph/nodes.py` 中所有旧节点函数 | 完全迁移后清理 | Phase 7 |

---

# 20. 测试方案

## 20.1 单元测试

### Supervisor Decision

```python
# test_cs_supervisor.py

def test_supervisor_routes_to_knowledge_expert():
    """简单知识问答 → KnowledgeExpert"""

def test_supervisor_routes_to_query_expert():
    """业务查询 → QueryExpert"""

def test_supervisor_detects_combined_intent():
    """查询+退款组合 → 先 Query 再 Action"""

def test_supervisor_requires_confirmation():
    """高风险操作 → 需要确认"""

def test_supervisor_intercepts_handoff():
    """handoff_state != ai_active → 拦截"""

def test_supervisor_max_loops():
    """expert_loop_count > 5 → 强制结束"""

def test_supervisor_empty_route():
    """cs_route 为空 → 降级到 knowledge"""
```

### Expert

```python
# test_cs_experts.py

def test_knowledge_expert_calls_rag():
    """KnowledgeExpert 调用 CSKnowledgeService"""

def test_knowledge_expert_low_confidence():
    """置信度低 → 返回谨慎回复"""

def test_query_expert_order_not_found():
    """订单不存在 → OrderNotFoundError"""

def test_query_expert_unauthorized():
    """未认证 → AuthenticationError"""

def test_action_expert_builds_proposal():
    """ActionExpert 构建退款提案"""

def test_action_expert_high_risk():
    """高风险 → requires_human_review"""

def test_complaint_expert_creates_ticket():
    """投诉 → 创建工单"""

def test_handoff_expert_transitions_state():
    """转人工 → 状态转换"""
```

### State Transition

```python
# test_state_transition.py

def test_transition_valid_conversation_status():
    """open → pending: 合法"""

def test_transition_invalid_confirmation():
    """PENDING → EXECUTING: 非法 (必须经过 CONFIRMED)"""

def test_transition_handoff_terminal():
    """CLOSED → 任何: 非法"""

def test_transition_updates_both_db_and_state():
    """StateTransitionService 同时更新 DB 和返回快照"""
```

## 20.2 Graph 测试

```python
# test_cs_graph.py

def test_cs_graph_knowledge_flow():
    """Router → Supervisor → KnowledgeExpert → Supervisor → Reporter"""

def test_cs_graph_query_flow():
    """Router → Supervisor → QueryExpert → Supervisor → Reporter"""

def test_cs_graph_action_with_confirmation():
    """Supervisor → ActionExpert → Pending → (resume) → ActionExpert → Reporter"""

def test_cs_graph_handoff_flow():
    """Supervisor → HandoffExpert → Reporter"""

def test_cs_graph_handoff_intercept():
    """handoff_state=human_active → Supervisor 直接拦截 → Reporter"""
```

## 20.3 多轮测试

```python
def test_multi_turn_confirmation_resume():
    """
    Turn 1: "帮我退款" → Supervisor → ActionExpert → Pending (checkpoint)
    Turn 2: "确认" → Resume → ActionExpert → SUCCESS → Reporter
    """

def test_multi_turn_cancel_confirmation():
    """
    Turn 1: "帮我退款" → Pending
    Turn 2: "算了" → Cancel → Reporter
    """

def test_multi_turn_query_then_action():
    """
    Turn 1: "订单到了吗？到了就退款" → Query → Supervisor → Action (确认)
    Turn 2: "确认" → Action → SUCCESS
    """
```

## 20.4 Handoff 测试

```python
def test_handoff_full_lifecycle():
    """AI_ACTIVE → HANDOFF_REQUESTED → WAITING_HUMAN → HUMAN_ACTIVE → CLOSED"""

def test_handoff_user_messages_during_handoff():
    """handoff_state != ai_active 时用户发消息 → 拦截回复"""

def test_handoff_auto_trigger():
    """连续低置信度 → 自动触发转人工"""

def test_handoff_complaint_escalation():
    """投诉 → 自动触发转人工"""
```

## 20.5 Confirmation 测试

```python
def test_confirmation_full_lifecycle():
    """PENDING → CONFIRMED → EXECUTING → SUCCESS"""

def test_confirmation_expiry():
    """超过 TTL → EXPIRED"""

def test_confirmation_duplicate():
    """用户重复确认 → 幂等处理"""

def test_confirmation_cancel():
    """用户取消 → CANCELLED"""
```

## 20.6 异常测试

```python
def test_expert_failure_supervisor_retries():
    """Expert 失败 → Supervisor 决策重试"""

def test_expert_failure_supervisor_degrades():
    """Expert 多次失败 → Supervisor 转人工"""

def test_llm_timeout_in_supervisor():
    """Supervisor LLM 超时 → 规则降级"""

def test_invalid_supervisor_output():
    """Supervisor 输出非法 → Pydantic 校验失败 → 降级"""

def test_command_to_nonexistent_node():
    """Command 指向不存在节点 → Graph 编译时捕获"""

def test_db_state_diverges_from_graph_state():
    """DB 状态与 Graph State 不一致 → 以 DB 为准重新加载"""

def test_checkpoint_restore_failure():
    """Checkpoint 恢复失败 → 当作新请求处理"""

def test_user_messages_during_human_handling():
    """人工处理中用户继续发消息 → 拦截"""
```

---

# 21. 风险与回滚方案

## 21.1 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| CS Graph 引入后 Main Graph 性能 | 低 | 中 | cs_graph_node 仅在 CS 路径触发，其他路径不变 |
| Checkpointer 与现有 Store 冲突 | 中 | 高 | 使用不同表/命名空间 |
| Command 路由错误导致死循环 | 中 | 高 | `expert_loop_count` 硬限制 |
| 状态转换 Service 引入单点故障 | 低 | 高 | 保持纯函数，无状态 |
| 迁移期间新旧逻辑并行 | 中 | 中 | Feature flag 控制 |

## 21.2 回滚方案

每个 Phase 独立可回滚:
- Phase 0-1: 删除新文件即可
- Phase 2-4: Feature flag 关闭 CS Graph，走旧路径
- Phase 5: Checkpointer 可独立关闭
- Phase 6-7: 恢复旧节点注册

---

# 22. 明确禁止事项

```
X 一个 Supervisor Prompt 塞进所有业务规则
X Expert 自己实现 SQL/RAG/Tool
X Supervisor 直接操作数据库
X State 和 DB 双向随意修改
X 每个 Expert 都直接进入 Reporter
X Router 和 Supervisor 重复做意图识别
X 为了使用 Command 强行使用 Command.PARENT
X 为了多 Agent 强行创建多个 LangGraph
X 一次性重写整个 Customer Service
X 删除现有状态机而没有迁移方案
X 没有 Checkpoint 就实现跨请求 Agent Resume
X CS Supervisor 嵌套在 Main Supervisor 内部
X Main Supervisor 感知 CS Expert 的存在
X CS Graph 执行后回到 Main Graph Supervisor (必须直接到 END)
X CS Graph 与 Main Graph 共享 State (通过 CSGraphResult 契约通信)
X Expert 绕过 Service 层直接访问数据库
X 在 Graph State 中存储完整消息历史 (用 conversation_id 引用)
X 修改主图 CSAgentState 的字段定义 (向后兼容)
X 删除 cs_handoff_intercept 而不迁入 Supervisor
X 在 Expert 中直接调用其他 Expert
X 让 PostgreSQL 和 Checkpoint 成为双 Source of Truth (PostgreSQL 是唯一)
```

---

# 23. 最终结论

## 23.1 核心结论

1. **推荐方案 B (Main Graph + 独立 CS Graph)**: CS 域构建为独立 LangGraph Graph，通过 `cs_graph_node` 适配器 + `CSGraphResult` 契约与 Main Graph 通信，对主图业务逻辑低侵入
2. **引入 CS Supervisor**: 解决组合意图、多步协作、动态降级等核心业务问题
3. **Expert 是编排层不是能力层**: 复用现有 Service，不重复实现
4. **Command 用于 CS Graph 内部路由**: 结构化决策，替代 dict 条件边，不使用 Command.PARENT
5. **Checkpointer 解决跨请求恢复**: 核心痛点，仅对 CS Graph 启用，配合 DB 双保险
6. **三套状态机保留在 PostgreSQL**: PostgreSQL 是唯一 Source of Truth，通过 StateTransitionService 统一管理
7. **分 7 个 Phase 渐进迁移**: 每个 Phase 独立可回滚
8. **CS Graph 不回 Main Graph**: CS Graph 执行完毕后直接到 END，不回到 Main Graph 的 Supervisor 或其他节点

## 23.2 等待下一步

本设计方案完成。等待确认后进行实现。
