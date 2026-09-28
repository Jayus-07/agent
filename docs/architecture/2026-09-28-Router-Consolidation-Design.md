# Router Consolidation Design — STOP B0

> 状态：设计冻结，未进入 STOP B 实施。  
> 日期：2026-09-28  
> 约束：本阶段只新增设计文档，不修改生产行为、状态协议或路由实现。

## 1. 设计目标与非目标

目标是减少“决定下一步”的生产决策点，同时保留已经稳定的域预过滤、
hierarchical 路由、legacy fallback、缓存、trace 和灰度行为。

本阶段不做以下事情：

- 不删除 `router_node`，不修改 LangGraph node id、checkpoint key、SSE frame 或 state 字段。
- 不重写 rule/vector/LLM router，不重新设计 CoarseIntentClassifier。
- 不在 `router_node` 中直接执行 Tool、Skill、Workflow 或业务数据库操作。
- 不一次删除 legacy router；新语义边界稳定并通过 evaluation 后，才允许逐步下线。

## 2. 最终职责边界

```text
GraphRunner
    ↓
router_node                    # 唯一 LangGraph 入口与兼容状态边界
    ├── DomainRouter            # 判断顶级业务域 / 域图入口
    ├── CapabilityRouter        # 当前域可用 capability 与候选
    └── ExecutionModeResolver   # direct / workflow / plan / domain_graph / general
             ↓
        route_selector          # 只把已确定的兼容 route_mode 映射到旧 node id
```

### 2.1 `router_node` 最终职责

`router_node(state) -> state_update` 保留为主图唯一入口，职责限定为：

1. 读取 query、`domain_hint`、routing context 和 Guard 结果。
2. 调用域入口策略（客服锁域、延续解析、旅游/选品/商务/预订 prefilter）。
3. 调用 DomainRouter，再调用 CapabilityRouter。
4. 调用 ExecutionModeResolver 形成统一决策。
5. 写回既有 `route_decision`、`route_mode`、`query_understanding` 与兼容平铺字段。

预过滤仍在此入口执行是为了保持优先级、灰度和域锁语义；后续 STOP B 只把实现
抽到策略模块，不改变调用顺序和判定结果。

### 2.2 DomainRouter

DomainRouter 只回答“请求属于哪个业务域”，不得选择 Tool、填充参数或执行能力。

```python
class DomainDecision(TypedDict):
    domain: str                 # customer_service / travel / selection / business / general / unknown
    subflow: str | None         # planning / commerce / booking 等
    confidence: float
    source: str                 # prefilter / rule / embedding / llm / continuation
    reasoning: str
```

实现复用当前策略：

```text
强域锁 / prefilter → rule hints → CoarseIntentClassifier embedding → 既有 LLM fallback
```

`travel_commerce`、`travel_booking` 在 DomainRouter 的语义层统一为
`domain=travel`，分别携带 `subflow=commerce|booking`；旧 `route_mode` 仍由兼容层保留。

### 2.3 CapabilityRouter

CapabilityRouter 只在已确定的 Domain 内选择 Capability 候选，不访问业务数据库，
不执行 Tool，也不决定最终参数。

```python
class CapabilityDecision(TypedDict):
    domain: str
    capability: str | None
    candidates: list[dict]       # name / score / risk / source
    confidence: float
    source: str                  # rule / vector / hierarchical / fallback
    reasoning: str
```

候选来源继续复用 `capabilities.yaml`、现有 `resolve_domain_tools()`、RuleRouter、
VectorRouter 和 FineToolRouter。CapabilityRouter 的输出只是候选与置信度，不能把
`skill_executor`、`workflow_executor` 或具体 Tool 当成自己的执行职责。

### 2.4 ExecutionModeResolver

ExecutionModeResolver 是纯规则决策器，把 Domain/Capability 结果和现有 override
归一为唯一的公开执行模式：

```text
direct       单能力直接执行
workflow     已登记的固定工作流
plan         动态 Capability DAG
domain_graph 有独立状态和生命周期的域图
general      无 Tool 的直接回答
```

Resolver 只决定执行方式，不替代 DomainRouter 或 CapabilityRouter 的领域判断。
`clarify` 是现有入口追问的兼容 route mode，不作为新的顶层执行模式；它最终映射
到 reporter 短路。

## 3. Before / After

### 当前

```text
router_node
  ├─ Domain prefilter
  ├─ hierarchical router
  ├─ legacy rule/vector/LLM router
  ├─ workflow / plan / direct 判断
  └─ state 写回与兼容回退
```

### 目标

```text
router_node
  ├─ DomainRouter
  │    └─ 复用 prefilter / hierarchical coarse / legacy fallback
  ├─ CapabilityRouter
  │    └─ manifest 候选 + rule/vector/fine selection
  └─ ExecutionModeResolver
       └─ 唯一输出 direct / workflow / plan / domain_graph / general
```

旧实现不会在 STOP B0 删除；STOP B 先把新边界作为主路径接入，旧 Router 只负责
兼容 fallback 和 shadow 对照。

## 4. 兼容性契约

以下值必须保持可读、可恢复、可评测：

| 契约 | B0 冻结要求 |
|---|---|
| LangGraph node id | `router`、`planner`、`critique`、`supervisor`、`general_chat` 及域图 node id 不变 |
| state | 保留 `route_decision`、`route_mode`、`query_understanding` 与现有平铺字段；新字段只能增量加入 |
| route mode | 现有 `direct`、`workflow`、`plan`、`general_chat`、`clarify`、域 route_mode 继续可消费 |
| checkpoint / trace | 不改 key、action、node id；新增决策元数据必须可序列化 |
| SSE | status/log/delta/done/error 帧协议不变 |
| fallback | hierarchical 异常、低置信、灰度 control 组仍回到 legacy 行为 |
| Domain lock | `domain_hint=customer_service` 仍优先客服；强旅游/选品信号的 redirect_main 语义不变 |

## 5. 迁移顺序（仅设计，不在 B0 执行）

1. STOP B：新增 DomainDecision、CapabilityDecision、ExecutionModeResolver 的
   纯函数/适配器测试，再由 `router_node` 接入，旧 Router 保留 fallback。
2. 观察 CS、Travel、SQL、RAG evaluation 以及 shadow metrics，确认 route mode、
   capability 命中率、延迟和错误率不回归。
3. 迁移 workflow 路由测试到 manifest/CapabilityRouter 后，确认
   `orchestration/workflow/router.py` 无生产引用，再删除 TaskRouter。
4. 连续版本无 fallback 告警且 evaluation 基线稳定后，才讨论删除 legacy router。

## 6. 测试与验收门

STOP B 必须新增或调整以下守护：

- `router_node` 仍是唯一主图入口，条件边和旧 node id 不变。
- DomainRouter 不 import Tool、Skill、Planner 或业务数据库。
- CapabilityRouter 只返回候选，不执行 Tool/Workflow。
- ExecutionModeResolver 的五种公开模式有确定映射；`clarify` 兼容映射不漂移。
- 客服锁域、旅游、选品、商务、预订 prefilter 的优先级和灰度行为保持不变。
- hierarchical 失败时 legacy fallback 可用，shadow 仍只观测不改变拍板结果。
- `/chat/stream` SSE、CS evaluation、Travel evaluation、SQL evaluation、RAG evaluation
  与现有 registry/layer/ADR 守护均通过。

## 7. STOP B0 验收结论

```text
ROUTER_CONSOLIDATION_DESIGN_FROZEN=true
PRODUCTION_BEHAVIOR_CHANGED=false
LEGACY_ROUTER_REMOVED=false
ROUTER_NODE_ID_CHANGED=false
```

后续进入 STOP B 前，必须以本设计文档为约束；若实现需要改变上述兼容契约，应先
新增设计变更记录，不得在代码提交中隐式扩大范围。
