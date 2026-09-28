# Architecture Simplification — STOP B Router Consolidation Report

日期：2026-09-29
范围：STOP B Step 0～Step 6 + Evaluation Runtime 隔离修复
结论：Router Consolidation 与 RAG Evaluation Runtime 隔离均通过；评估冷启动只读已有索引，真实 RAG smoke 已产出指标。

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
| Router focused regression（本次复验） | 112 passed |

### 业务评估

| 评估 | 结果 | 说明 |
|---|---:|---|
| CS evaluation | 通过 | `test_cs_runner_v2.py` 2 passed，离线 300 条全部通过 |
| Travel evaluation | 通过 | `test_quality_golden.py` 2 passed，聚合 34 条旅游金标 |
| SQL evaluation | 通过 | `test_sql_eval.py` 14 passed，离线 runner 无 fail |
| RAG 评测契约 | 通过 | suites/scope/migration 共 16 passed |
| RAG 真实离线 smoke | 通过 | 5/5，通过率 100%，使用 evaluation 只读模式 |

此前 RAG smoke 的实际错误是：

```text
数据库未配置可用的 embedding 供应商 API Key
RAGPipeline 构建失败
```

该问题已通过评估 CLI 启动时刷新数据库 LLM/embedding registry 覆盖层解决。当前日志确认 embedding 配置正常加载：

```text
[Embedding] Cloud 模式初始化完成 (model=qwen3.7-text-embedding, ...)
```

### Evaluation Runtime 隔离验收

- `RAGPipeline(mode="index")` 保留扫描、解析、chunk、embedding、metadata 与 vector upsert 能力。
- `RAGPipeline(mode="runtime")` 只加载已有向量/BM25 索引；运行时不构建 BM25、不写 vector。
- `RAGPipeline(mode="evaluation")` 只读已有索引，禁止自动 sync、metadata LLM 与 vector 写入。
- `RAGPipeline(mode="index", auto_sync=False)` 供显式 fixture 导入使用，避免构造 index shell 时误触发全库同步。
- 评估 scope 固定绑定 `kb_id + fixture_set + version_id`；baseline 快照为
  `rag_eval_kb / baseline / rag_eval_kb-baseline-2026-09-18`。
- chunk metadata 已包含 `doc_id`、`kb_id`、`fixture_set`、`dataset=rag_eval`、`version_id`。
- `python -m backend.evaluation.import_fixture baseline` 导入结果：16 documents、185 chunks、collection=`rag_eval_kb`。
- 只读启动对 BM25 内容执行集合级一致性校验，允许向量库与 BM25 返回顺序差异，不放宽内容或文档集合校验。

真实 smoke 命令：

```text
python -m backend.evaluation.cli rag --smoke --selection pr_baseline --no-ragas --no-resume
```

结果：

```text
5/5 通过（100%）
Recall@5 = 1.0000
MRR       = 0.7333
NDCG@10   = 0.8000
```

冷启动日志出现“加载已有 chunk/文档级向量库”和“evaluation 模式加载只读 BM25 索引”，未出现 docs 扫描、index sync 或 vector upsert。

## 5. 路由漂移判断

- 旧 `route_mode` 值及 `route_selector` 映射未改动；CS 锁域、旅游/选品优先级、澄清路径回归均通过。
- legacy/hierarchical 旧 `RouteDecision` 继续作为兼容事实源；新增对象是旁路快照，不覆盖旧字段。
- 本次 smoke 使用固定 baseline snapshot；未虚构与历史 live baseline 的差异，后续如需趋势对比应沿用同一 `kb_id + fixture_set + version_id`。

## 6. STOP B 判定

```text
ROUTER_CONSOLIDATION_PASS=true
```

验收依据：Router focused regression 112 passed；baseline fixture 可导入；evaluation 冷启动不会触发 index 同步；embedding registry 正常加载；RAG smoke 真实指标已生成。

本次未删除 legacy Router 或 TaskRouter，也未进入 STOP C；后续如需继续减法，仍应以独立 STOP C 任务为边界。
