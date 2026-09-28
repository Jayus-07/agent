# Architecture Simplification — STOP B Router Consolidation Report

日期：2026-09-28
范围：STOP B Step 0～Step 6
结论：适配器已接入且旧行为回归通过；RAG 真实 smoke 被环境配置阻断，因此暂不宣告 STOP B 通过，也不进入 STOP C。

## 1. 本阶段完成内容

### 新增统一决策模型

- `backend/orchestration/router/models.py`
  - `DomainDecision`
  - `CapabilityDecision`
  - `ExecutionModeDecision`
- 三类对象均为普通字典/数据类，可序列化进入 LangGraph/checkpoint；不携带 Tool、Skill、Workflow 或业务服务对象。

### 新增职责适配器

- `backend/orchestration/router/domain_router.py`
  - 复用既有 prefilter 更新、粗分类器和 hierarchical 元数据；
  - 将 `travel_booking` / `travel_commerce` 归一为 `domain=travel` 加 `subflow`；
  - 不重复执行 prefilter，不复制正则，不选择 capability。
- `backend/orchestration/router/capability_router.py`
  - 复用 `resolve_domain_tools()` 与 `HierarchicalRouter.select_tool()`；
  - 可从既有 hierarchical/legacy 结果直接派生候选快照，避免二次向量检索；
  - 只返回候选和评分，不执行 Tool/Skill/Workflow。
- `backend/orchestration/router/execution_mode.py`
  - 按 override → general → domain graph → direct → plan 的兼容优先级归一执行方式；
  - `clarify` 保留为 `compat_route_mode`，没有扩展旧 `ExecutionMode` 枚举；
  - 域图模式优先读取 `domain_graph_registry`，无注册时才使用兼容静态映射。

### 主图接线

`router_node` 仍是唯一 LangGraph Router 入口。新增 `_with_router_decisions()` 只负责把三类决策对象增量写入 state：

- 既有 `route_decision`、`route_mode`、`query_understanding` 继续保持权威；
- 所有客服锁域、旅游、选品、预订、商务 prefilter 仍由原顺序执行；
- legacy `RouteDecision` 不再重复做 embedding，直接生成 `source=legacy` 快照；
- hierarchical prefilter 未放行时仍调用 `route_legacy()`；
- 适配器异常只写 `router_fallback_reason` / `legacy_used`，不阻断旧路径。

新增的 state 字段均为增量、普通可序列化值：

```text
domain_decision
capability_decision
execution_decision
router_fallback_reason
legacy_used
```

## 2. 明确没有删除的实现

以下实现全部保留：

- `backend/orchestration/router/router.py` legacy rule → vector → LLM Router；
- `backend/orchestration/router/hierarchical.py` 粗分类 → 域内细选；
- `rule_router.py`、`vector_router.py`、`llm_router.py`；
- CS、Travel、Selection、Booking、Commerce prefilter；
- `backend/orchestration/workflow/router.py::TaskRouter`。

`TaskRouter` 仍无生产调用方，只有其自身定义/示例和 3 组测试引用。本阶段未删除测试或文件，避免把路由收口与存量清理混为一个变更。

## 3. 兼容性验收

```text
ROUTER_NODE_ID_CHANGED=false
STATE_CONTRACT_CHANGED=false       # 仅增量字段，旧字段语义未变
SSE_CHANGED=false
CHECKPOINT_CHANGED=false
LEGACY_FALLBACK_AVAILABLE=true
```

证据：本次提交未触碰 `builder.py`、SSE 事件协议、checkpointer 实现或前端契约；`route_selector` 的旧映射未改动。

## 4. 测试与评估结果

### Router 与架构守护

| 范围 | 结果 |
|---|---:|
| STOP B 新增适配器契约测试 | 26 passed |
| Router/domain/hierarchical/shadow 回归 | 33 passed |
| router_node prefilter / understanding / clarify 回归 | 51 passed |
| registry/layer/ADR0001 一致性守护 | 36 passed |
| Python 编译检查 | 通过 |
| `git diff --check` | 通过 |

### 业务评估

| 评估 | 结果 | 说明 |
|---|---:|---|
| CS evaluation | 通过 | `test_cs_runner_v2.py` 2 passed，离线 300 条全部通过 |
| Travel evaluation | 通过 | `test_quality_golden.py` 2 passed，聚合 34 条旅游金标 |
| SQL evaluation | 通过 | `test_sql_eval.py` 14 passed，离线 runner 无 fail |
| RAG 评测契约 | 通过 | suites/scope/migration 共 16 passed |
| RAG 真实离线 smoke | 阻断 | 5 条均无法运行：数据库没有可用 embedding provider API Key |

RAG smoke 的实际错误是：

```text
数据库未配置可用的 embedding 供应商 API Key
RAGPipeline 构建失败
```

这是运行环境阻断，不是 Router 断言失败；但由于 RAG 真实链路没有得到有效结果，不能把最终门禁写成通过。

## 5. 路由漂移判断

- 旧 `route_mode` 值及 `route_selector` 映射未改动；CS 锁域、旅游/选品优先级、澄清路径回归均通过。
- legacy/hierarchical 旧 `RouteDecision` 继续作为兼容事实源；新增对象是旁路快照，不覆盖旧字段。
- 本阶段没有可用的同口径历史 live RAG baseline，因此没有虚构 capability/domain 数值差异；真实 RAG comparison 待 embedding 配置恢复后重跑。

## 6. STOP B 判定

```text
ROUTER_CONSOLIDATION_PASS=false
```

原因只有一项：RAG 真实 smoke 被 embedding provider 配置阻断，尚不能证明全链路 evaluation 不下降。代码层面已满足职责收口、旧字段兼容、legacy fallback 和域图 registry 动态解析要求。

恢复 embedding 配置后，必须重跑：

```text
python -m backend.evaluation rag --smoke --no-ragas --selection pr_baseline --no-resume
```

并补充同口径 baseline 对比；在该门禁通过前，不进入 STOP C，不删除 legacy Router 或 TaskRouter。
