# Architecture Simplification — STOP F 准备审计：Node Runtime Contract Closure

日期：2026-09-29
基线：`main@8ecf7fc`（STOP E 已结案，`DOMAIN_BOUNDARY_PASS=true`）
性质：**只读设计审计，未修改任何生产代码**。
结论先行：

```text
STOP_F_READY=true
NEXT_ACTION=IMPLEMENT_STOP_F        # 按第六部分收窄后的范围实施
NODE_RUNTIME_REWRITE=false          # 不是 Runtime 重写，只提取公共执行生命周期
EXPERT_PUBLIC_CONTRACT_FREEZE=true  # run_expert_safely / ExpertResult / TravelExpertResult 签名与字段冻结
```

---

## 1. 当前所有 Node 类型与落点

### 1.1 Domain graph node（主图适配器，5 个 = STOP E 冻结口径）

| 适配器 | 位置 | 领域特有职责 |
|---|---|---|
| `cs_graph_node` | `orchestration/graph/cs_graph_node.py` | CS 子图调用 + 拒答追问判定（:100）+ 异常降级兜底 final_answer（:51） |
| `travel_graph_node` | `orchestration/graph/travel_graph_node.py` | checkpoint 前缀 `travel:{tenant:user}:{conv}`（:398-430）+ 跨轮 pending/恢复 + 适配转换 |
| `selection_funnel_graph_node` | `orchestration/graph/selection_funnel_graph_node.py` | 漏斗域图调用 + 异常降级兜底（:53-59）+ `funnel_report` trace 标签（:122） |
| `travel_commerce_graph_node` | `travel/commerce/graph_node.py` | 子图调用 + 异常降级兜底（:30-33）+ trace 标签软失败（:53） |
| `travel_booking_graph_node` | `travel/booking/graph_node.py` | 同模式（预订事务域） |

共同形态：**state 转换 → 子图 invoke → catch-all → 兜底 `final_answer`**。5 个适配器在主图层已被 `TraceMiddleware` 包裹（`builder.py:147`），本身不再手写 span/计时。

### 1.2 Expert node（域内专家与同层节点）

| 域 | 节点 | 位置 | 生命周期来源 |
|---|---|---|---|
| CS | 5 专家（knowledge/query/action/complaint/handoff） | `customer_service/experts/*.py` | 各节点函数自调 `run_expert_safely`（`experts/base.py:50`） |
| CS | state_loader / supervisor（Command 路由）/ pending_handler / reporter | `customer_service/` | 无公共包装，纯业务 |
| Travel | 5 专家（poi/transit/weather/budget/risk） | `travel/experts/*.py` | 各节点函数自调 `run_expert_safely`（`travel/experts/base.py:46`） |
| Travel | validator（四轴，纯规则零 LLM）/ repair / slot_filler / supervisor | `travel/validator.py`、`travel/repair.py`、`travel/slot_filler.py`、`travel/supervisor.py` | **各写各的**：validator 手写 per-axis span（:102），repair/validator 手写 `quality_metrics`（qm）软失败遥测（validator.py:497-512、repair.py:431） |
| Selection | 5 stages（screener/ranker/economist/verifier/pool_builder） | `selection_funnel/stages/*.py` | **无任何公共包装**：直接返回 status 字典（`ok/need_info/empty`），异常模型靠适配器兜底 |

**关键拓扑事实**：两个域子图 builder 均为**裸 `add_node`**（`travel/graph_builder.py:67-76`、`customer_service/graph_builder.py:135-144`），`TraceMiddleware` 只包主图——子图内部节点的 trace/计时/异常包装全部靠手写补位。这就是重复的根源。

### 1.3 Skill executor（主图 direct/workflow 支线）

- `orchestration/graph/direct_executor.py:162` `skill_executor_node`、`:355` `workflow_executor_node`。
- 生命周期：`builder.py:135-136` 的 `TraceMiddleware` 包裹（span+计时+异常**穿透**）+ 业务层 try/except → `step_results` failed-step 契约（f11：失败步骤留痕供 reporter/trace 消费）。
- Skill 本体执行在 `skills/base.py`（governed/legacy 双路，见 1.4）。

### 1.4 Tool executor

- **治理层已存在**：`backend/core/tool_runtime/`（executor/models/policy/deadline/retry/circuit_breaker/bulkhead/error_mapper/metrics 九件套）——Deadline/Timeout/Retry/CircuitBreaker/Bulkhead/ErrorMapper/Metrics 统一治理，经 `skills/base.py::_execute_governed` 接入（`:352`），旧循环 `_execute_legacy_loop`（`:494`）作紧急回滚开关。
- 输出契约：`tools/map/_base.py` 的 `ok/fail/not_configured`。
- **Tool 层不需要 STOP F 动它**——它已经是四类节点里唯一有完整公共运行时的。

### 1.5 既有的公共生命周期层（盘点，避免重复建设）

| 层 | 位置 | 覆盖 | 已负责 |
|---|---|---|---|
| `TraceMiddleware` | `observability/trace_middleware.py` | 主图 9 节点 + 5 域图适配器 + 12 skill 节点（builder.py:134-157） | span 起止、计时、error status、`bind_from_state` 请求上下文重绑（Send 线程） |
| `core/tool_runtime` | `backend/core/tool_runtime/` | 全部 Tool 调用 | 治理七件套 |
| `core/request_context` | `backend/core/request_context.py` | 全链路 | 请求身份/`checkpoint_safe()` |

**不存在** `ExecutionContext` / `NodeRunner` / `NodeResult` 之类的节点级公共运行时（全仓 grep 为 0）——设计空间干净，且 `core/tool_runtime` 已确立「`core/` 放横切运行时」的先例与先邻。

## 2. 哪些属于公共生命周期（重复面实证）

以 `customer_service/experts/base.py` 与 `travel/experts/base.py` 逐行对比为主轴，横向对照其余节点类型：

| 生命周期关注点 | TraceMiddleware（主图） | CS base | Travel base | validator | 域图适配器 | tool_runtime |
|---|---|---|---|---|---|---|
| 计时（monotonic→ms） | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| start/done 日志 | ✗ | ✓ | ✓ | ✗ | ✗ | ✓ |
| 异常→不穿透结果 | ✗（穿透 re-raise） | ✓→`status=failed` | ✓→`status=failed` | ✓（软失败） | ✓→兜底 final_answer | ✓（ErrorMapper） |
| 结果包装（expert/status/duration_ms） | ✗ | ✓（:148-151） | ✓（:66-68） | ✗ | ✗ | ✗（step_results 在上层） |
| span | ✓ | **✗（CS 专家无 span）** | ✓（手写 :88-117） | ✓（手写 per-axis） | 部分（trace 标签） | ✓（tool_span） |
| metrics | ✗ | ✓（`record_cs_expert_result`） | span metrics 搭载 | ✓（qm） | ✗ | ✓（metrics.py） |
| 上下文传播 | ✓ bind_from_state | ✓（`contextvars.copy_context` :93） | 不需要（无线程） | n/a | n/a | deadline/request ctx |

→ **公共生命周期 = `bind/log_start → timer → invoke → exception_policy → result_wrap → metrics/span_end/log_done` 六段**，当前存在 **6 份手写变体**（CS base、Travel base、validator、repair 遥测、TraceMiddleware、tool_runtime 各一）。其中 STOP F 的收敛对象是**前两个专家 base**；其余四个是有既定消费契约的独立层（见第 5 节禁改理由）。

## 3. 哪些必须保留领域差异（禁强行统一）

| 域 | 必须保留的差异 | 原因（生产实证） |
|---|---|---|
| CS | **per-call 线程池限时 + `contextvars.copy_context()`**（base.py:80-93）、`TIMEOUT` 状态、`response_draft/evidence/action_result` 字段、`record_cs_expert_result` 领域 metrics、无 span | ①共享线程池限时会饿死排队（2026-09-17 P2.3 实证）；②线程不携 contextvars 会丢 Context Budget 业务 pin（2026-09-23 B4 修复）；③supervisor 的 handoff 拦截/循环上限/LLM 兜底与 confirmation（pending_handler）是 CS 调度语义，不属于节点生命周期 |
| Travel | span 软失败生命周期（noop when 无 trace）、`data/notes` 通用出口、**无 timeout 参数**、validator 四轴独立 span + qm 质量遥测、`kept_required` 修复契约、supervisor `planning_reset`、`travel:` checkpoint 跨轮恢复 | ①专家是纯规则快路径，限时无意义；②span 命名 `travel_expert_{name}` 与 validator span 已是观测口径；③结构化 POI/行程对象硬套 CS 字段会造成假复用（travel base 模块注释开宗明义） |
| Selection | `ok/need_info/empty` status 字典、无异常包装、适配器单点兜底 | 漏斗是数据管道语义，stage 失败语义 = 状态标记而非异常；给 stages 加包装是无收益的行为变更 |
| 主图 | **异常穿透**（TraceMiddleware re-raise，交 LangGraph）+ `step_results` 契约 | 主图靠图级错误通道与 reporter 降级，吞异常反而破坏 SSE error 帧 |
| Tool | 治理七件套 + `ok/fail/not_configured` 输出契约 | 已收口，`Phase2/STOP` 冻结 |

**结论**：统一的是**机制**（计时/异常策略/包装/钩子接口），不是**遥测形态**（CS metrics-only vs Travel span+metrics）、不是**结果类型**（三类 Result TypedDict 各自保留）、不是**异常策略选择**（穿透 vs 吞掉是 per-node-class 的 policy，不是谁能对谁错）。

## 4. 设计目标（实施蓝图，本 STOP 不动码）

```
backend/core/node_runtime/            # 与 core/tool_runtime 平级，同先例
├── __init__.py                       # 导出公共 API
├── models.py      # NodeResult: status/error/duration_ms/data——通用四字段，业务字段禁入
├── context.py     # ExecutionContext: node_name/domain 标签/deadline/tags，冻结 dataclass
├── runner.py      # NodeRunner.run(ctx, fn, *, policy, hooks, timeout=None)
│                  #   六段公共生命周期：log_start→timer→invoke(可选 TimeoutStrategy)→
│                  #   exception→wrap→log_done；异常策略由 policy 决定，runner 不吞不吐
├── error_policy.py# ErrorPolicy: RAISE_THROUGH | SWALLOW_TO_STATUS | FALLBACK(value)
│                  # TimeoutStrategy: NONE | THREAD_ISOLATED(copy_context=True)  ← P2.3/B4 语义唯一实现
└── hooks.py       # ObservabilityHooks: on_start/on_success(status,duration)/on_error(duration,err)
                   # 默认 noop；CsExpertHooks=metrics；TravelExpertHooks=span(软失败)+span metrics
```

**迁移形态（F 实施范围）**：两个 `experts/base.py` 的 `run_expert_safely` **内部**改写为 `NodeRunner` + 各自 `ErrorPolicy`/`Hooks` 的薄适配——**公开签名、TypedDict 字段、status 枚举值、日志前缀、metrics 名、span 命名逐字节不变**（CS 12 处调用点 + tests 15 处引用零改动）。验收加 parity 测试：同一 fn 在旧/新路径下 status/error/duration_ms/包装字段一致、异常不穿透、CS 超时路径 thread 隔离 + ctx 拷贝行为不变。

**明确不迁移**：TraceMiddleware（主图覆盖面冻结，其内部是否改用 NodeRunner 属独立后续，span 形态逐字节一致是前提）、域图适配器 5 个、tool_runtime、validator/repair、selection stages、一切 supervisor 调度语义。

## 5. 禁改清单（永久红线，全部经代码定位证实）

LangGraph node id（含子图 `add_node` 名）｜checkpoint（`travel:` 前缀与三处 `_build_checkpointer`）｜state schema（`OrchestratorState`/三域 state/`funnel_context`/`travel_context`）｜SSE 帧序｜router（prefilter 顺序/route_mode）｜domain registry（STOP E 冻结）｜capability metadata（STOP C SSOT）｜tool schema（含 `tools/map/_base.py` 输出契约）｜`TraceMiddleware` 的 builder 接线与 span 形态。

## 6. 风险评估与方案收窄

| # | 风险 | 判定 | 处置 |
|---|---|---|---|
| 1 | CS 超时路径承载两次生产事故教训（P2.3 线程池饿死 / B4 contextvars 丢失） | 高——统一实现若合并进共享池或漏拷 ctx 即静默回归 | `THREAD_ISOLATED(copy_context=True)` 作为**唯一**实现，注释携带两案号；parity 测试锁行为 |
| 2 | 给子图节点补 `TraceMiddleware` 会双重 span（travel 专家已手写 span）并改变 trace 形态 | 高——观测面是行为变更 | F 不动任何 builder 接线；专家 span 命名与粒度逐字节保持 |
| 3 | 统一后 CS 专家获得 span（补齐不对称）看似改进，实为 trace 形态变更 | 中 | 归入 hooks 差异：CS hooks 保持 metrics-only；「CS 补 span」登记为 Deferred，须独立评审 |
| 4 | `run_expert_safely` 两个同名函数签名不同（CS 带 timeout_s） | 低但易踩 | 统一只发生在 `core/node_runtime` 新命名空间；两个 base 的模块级签名原样保留 |
| 5 | NodeResult 泛化后业务字段渗入（假复用回潮） | 中——正是 travel base 注释反对的 | models.py 硬约束：仅 status/error/duration_ms/data 四字段；域 TypedDict 不动 |

**收窄后的 F 实施范围**：新增 `core/node_runtime` 五文件 + 重写两个专家 base 内部 + parity/契约测试 + 既有专家测试全绿。**除此之外一律不做**。按此范围，风险全部可控——

## 7. 最终判定

```text
STOP_F_READY=true
NEXT_ACTION=IMPLEMENT_STOP_F
SCOPE=node_runtime 提取 + 双专家 base 内部重构；公开契约/遥测形态/接线全冻结
NODE_RUNTIME_REWRITE=false
TOOL_RUNTIME_TOUCHED=false
TRACE_MIDDLEWARE_TOUCHED=false
ADAPTER_TOUCHED=false
```

审计证据补充说明：`core/tool_runtime` 的存在使 STOP F 的合理定位进一步收窄——Tool 执行已治理化，节点级公共运行时只需要服务「专家/域内节点」这一层的六段生命周期，不需要也不应该发明第二套治理语义（retry/breaker/bulkhead 是 Tool 层关切，专家层只有 timeout 一个执行关切）。
