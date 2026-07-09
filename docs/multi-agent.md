# Multi-Agent 工作流

> LangGraph 实现的工作流编排。涉及 Planner / Supervisor / Workers / Reporter / State / ToolRegistry。

## 1. 工作流总览

```
START → Planner (DAG 规划) → Supervisor (调度)
  → route_after_supervisor 返回 list[Send] → Workers 并行执行
    ├─ SQL Worker    → sql_agent
    ├─ RAG Worker    → retrieval/pipeline.py
    └─ Report Worker → report_agent
  → 结果返回 Supervisor → 循环或 → Reporter → END
```

## 2. Planner — 任务规划 (`multi_agent/planner.py`)

输入用户问题 → LLM 分解为 capability + params 的 DAG（nodes + edges）。

**关键设计**：
- **只输出 capability**，不指定具体 tool；tool 选择由 Supervisor + ToolRegistry 完成
- Planner prompt 包含完整数据库 schema（4 张表）和能力选择指南，区分"纯数据类问题→SQL"和"制度/规范/经验→RAG"

**关键函数**：

| 函数 | 作用 |
|---|---|
| `_normalize_plan()` | 校验 capability 合法性（从 ToolRegistry 获取白名单）、规范化 nodes/edges 结构 |
| `_filter_plan()` | 后置规则过滤器 — 混合计划（SQL + RAG）中，如果问题不含知识库关键词（`_KNOWLEDGE_KEYWORDS`），自动移除冗余 RAG 步骤 |
| `_fallback_plan()` | 空计划兜底 — 自动创建 `search_knowledge` 步骤 |
| `is_knowledge_question()` | 公共函数，Supervisor 也用它判断是否触发 RAG 降级 |
| `_extract_json()` | 从 LLM 输出中提取 JSON（处理 markdown 代码块包裹） |

## 3. State 与并发合并 (`multi_agent/state.py`)

`AgentState.step_results` 定义为 `Annotated[dict[str, StepResult], _merge_step_results]`，自定义 reducer 处理并行 Worker 并发写入，解决 LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE` 错误。

`StepResult` 包含：
- `step_id`, `capability`, `description`
- `status` (pending / running / success / failed / skipped)
- `output`, `error`, `retries`
- `started_at`, `finished_at`
- `row_count`, `is_empty`, `error_type`

### 3.1 不可变状态规则

`_degraded_steps` 字段用 `Annotated[set[str], operator.or_]`（set union）作为 reducer。**节点必须返回新 set**（用 `s = s | {x}` 运算），禁止原地 `.add()` 修改 — 否则 reducer 看不到变化。

```python
# ✅ 正确
degraded_steps = degraded_steps | {fallback_id}

# ❌ 错误（破坏 LangGraph state 不可变语义）
degraded_steps.add(fallback_id)
```

## 4. ToolRegistry (`multi_agent/tool_registry.py`)

capability → worker 映射表（全局单例）：

| capability | worker 节点 |
|---|---|
| `query_database` | `sql_worker` |
| `search_knowledge` | `rag_worker` |
| `generate_report` | `report_worker` |

每个 capability 注册了描述和参数 schema，Planner prompt 从中动态生成。**新增能力只需在此添加映射**。

## 5. Supervisor — 调度与降级 (`multi_agent/supervisor.py`)

- `supervisor_node()`：检查依赖（edges），将就绪步骤设为 `running` 并收集到 `_ready_dispatch`
- `route_after_supervisor()`：返回 `list[Send]`（LangGraph 并行执行 Worker）或 `"reporter"`
- `MAX_SUPERVISOR_LOOPS = 10`：循环上限保护
- `_check_sql_fallback()`：SQL 空结果降级逻辑，**有条件触发**：
  1. 检查计划中是否已有 RAG 步骤（有则不重复添加）
  2. 检查问题是否含知识库关键词（`is_knowledge_question()`）
  3. 满足条件则动态注入 `{step_id}_rag_fallback` 步骤

### 5.1 降级链 (`multi_agent/degradation.py`)

```
DEGRADATION_CHAIN = {
    "query_database":   ["search_knowledge"],  # SQL 空 → RAG
    "search_knowledge": ["query_database"],   # RAG 无结果 → SQL
    "generate_report":  ["search_knowledge"], # 报告缺数据 → RAG
}
```

`MAX_DEGRADATION_PER_STEP = 1`：每个步骤最多降级 1 次，防止无限降级循环。

## 6. Reporter — 汇总 + Context Filter (`multi_agent/reporter.py`)

- LLM 汇总所有 step_results，生成 Markdown 最终回答
- **Context Filter** (`_filter_step_results()`)：对 RAG 步骤输出用 CrossEncoder 以**原始问题**为 query 重新打分，低于阈值 (0.35) 的输出折叠为 `<details>` 并标记过滤
- `_extract_rag_references()`：从 RAG 输出按文件名去重提取参考文献，追加到 LLM 回答末尾
- `_format_step_outputs()`：格式化步骤输出（剥离参考文献 → 统一追加）
- 降级模式：LLM 调用失败时直接拼接原始输出

## 7. Plan Critique (`multi_agent/critique.py`)

- `critique_node()`：Plan Critique 自我纠错（+1 LLM 调用）
- 启用开关：`config.ENABLE_PLAN_CRITIQUE = True`
- 检测到 capability 不匹配时自动修正
- LLM 失败时降级使用原计划（`CRITIQUE_FAILED` 告警）

## 8. Alerts 系统 (`multi_agent/alerts.py`)

`PlanAlert` 数据类 + `ALERT_CODES` 表 + `log_degradation()` 持久化到 `logs/degradation.jsonl`：

```python
ALERT_CODES = {
    "PLAN_EMPTY":           ("warn",  "Planner 返回空计划"),
    "PLAN_JSON_INVALID":    ("warn",  "Planner 输出无法解析"),
    "PLAN_MISROUTE":        ("warn",  "Critique 检测到 capability 不匹配"),
    "SUPERVISOR_MAX_LOOP":  ("error", "Supervisor 达到最大循环次数"),
    "WORKER_TIMEOUT":       ("error", "Worker 执行超时"),
    "WORKER_RETRY_EXHAUST": ("error", "Worker 重试耗尽"),
    "RERANKER_UNAVAILABLE": ("warn",  "CrossEncoder 不可用"),
    "DEGRADATION_TRIGGER":  ("info",  "触发降级链"),
    ...
}
```

API 路由 `/observability/alerts` 暴露这些事件。

## 9. SSE 流式进度 (`multi_agent/graph.py:stream_events()`)

- LangGraph `stream()` 驱动，在 Planner/Supervisor/Worker/Reporter 各节点产出事件
- **去重粒度**：`emitted = set()` 按 `(step_id, status)` 去重（非仅 step_id），保证同一步骤的 running→success/failed 都能发出
- **计时**：事件 data 包含 `started_at`, `finished_at`, `elapsed` 三个字段
- `_yield_step_events()`：从 step_results 提取 executing 事件
- running 状态在 Supervisor 派发时设定，Worker 完成时更新为 success/failed

事件类型（详见 `web/src/lib/types.ts`）：

| 事件 | 触发节点 | data 字段 |
|---|---|---|
| `meta` | Planner | `node_labels` 映射 |
| `status` | Supervisor / Worker | `node`, `ts` |
| `log` | Worker | `level`, `node`, `step_id`, `message`, `payload` |
| `delta` | Reporter | `content`（句子块） |
| `done` | graph 收尾 | `elapsed`, `sources` |
| `error` | 任意 | `message` |

## 10. 工具函数 (`multi_agent/tools.py`)

Worker 的 LangChain 工具入口（动态导入避免循环依赖）：

```python
def _get_sql_agent() -> SQLAgent: ...
def _get_rag_pipeline() -> RAGPipeline: ...
def _get_report_generator() -> ReportGenerator: ...

sql_query_tool = Tool(name="sql_query", func=..., description="...")
search_knowledge_tool = Tool(name="search_knowledge", func=..., description="...")
generate_report_tool = Tool(name="generate_report", func=..., description="...")
```

**注意**：这里的惰性单例与 `api/deps.py` 的惰性单例**有重复**（无锁）— Worker 调用前可能已构造一份 RAGPipeline，embedding 模型二次加载。当前接受这个浪费，换取零侵入接入。

## 11. 关键约定

- `MemoryManager` 在 `MultiAgentSystem.ask()` / `stream_events()` 入口调用 `start_session`、出口调用 `end_turn`
- Worker 并行执行**无共享状态**（通过 `Send` API 各自拷贝 state）
- `AgentState.step_results` 用 `Annotated[dict, _merge_step_results]` 处理并发写入
- Supervisor SQL 空结果降级**有条件触发**：问题必须含知识库关键词
- Planner `_filter_plan()` 后置移除混合计划中冗余的 RAG 步骤
- Planner `_fallback_plan()` 在空计划时自动创建 RAG 兜底步骤
- SSE 流去重粒度为 `(step_id, status)`，非仅 `step_id`

## 12. 修改指南

- **加新 capability**：在 `tool_registry.py` 注册 capability + worker，然后在 `workers/` 加新文件
- **改 Planner prompt**：编辑 `planner.py:PLANNER_SYSTEM`
- **改 SSE 事件格式**：编辑 `graph.py:stream_events()` 和 `web/src/lib/types.ts` + `MessageBubble.tsx`
- **改降级链**：编辑 `degradation.py:DEGRADATION_CHAIN`
- **改告警码**：编辑 `alerts.py:ALERT_CODES`
