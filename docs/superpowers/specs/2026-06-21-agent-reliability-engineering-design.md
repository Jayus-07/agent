# Agent 可靠性工程 — Planner/Supervisor 深度优化设计

> **日期**: 2026-06-21
> **目标**: 小模型下的 Multi-Agent 可靠性工程，让工作流从 "能跑" 变成 "跑得聪明"
> **原则**: 先轻量加固（Phase A），再 Plan Critique 循环（Phase B），逐步演进
> **延迟约束**: 最多 +1 次 LLM 调用（仅 Plan Critique 节点）

---

## 1. 架构变更

### 当前流程

```
START → Planner → Supervisor → Workers → Supervisor → ... → Reporter → END
```

### 新流程

```
START → Planner → Plan Critique → Supervisor → Workers → Supervisor → ... → Reporter → END
                                           ↑___________loop______________|
```

**新增节点：Plan Critique**，位于 Planner 之后、Supervisor 之前。

### 数据流

```
Planner 产出 plan
       ↓
Plan Critique 节点:
  - 输入: (plan, question, capabilities_schema)
  - LLM 审查: capability 匹配、依赖合理性、步骤完整性、冗余检测
  - 输出: 修正后的 plan（或原 plan）
  - 失败: 直接用原 plan，不阻塞
       ↓
Supervisor 调度 Worker（现有逻辑 + 循环上限 + 结构化降级）
       ↓
Reporter 汇总（BM25 兜底 + 结构化 success 判断）
```

---

## 2. Plan Critique 节点

### 2.1 设计目标

让 LLM 以"审查员"视角审视自己的计划，发现并修正路由错误，但不重新设计整个计划。

### 2.2 触发条件

- `ENABLE_PLAN_CRITIQUE = True`（config 可配，默认开）
- 计划步骤数 > 1（单步计划跳过，节省延迟）
- 计划非空

### 2.3 Prompt 设计

```
PLAN_CRITIQUE_SYSTEM = """
你是一个计划审查员（Plan Reviewer）。你的任务是审查另一个 AI 生成的
任务分解计划，找出其中的错误并修正。

## 可用能力
{capabilities_schema}

## 审查规则
1. **capability 匹配**：每个步骤的 capability 是否真正匹配问题意图？
   - query_database → 需要具体数据/统计/排名/数量的问题
   - search_knowledge → 需要制度/规范/经验/方法/定义的问题
   - generate_report → 需要生成格式化报告的问题
2. **依赖合理性**：edges 中的依赖关系是否合理？
3. **步骤完整性**：是否遗漏了必要的步骤？
4. **步骤冗余**：是否有对回答问题无帮助的冗余步骤？

## 输出原则
- **最小修改**：只修正明显有问题的部分，不重新设计整个计划
- **信任原计划**：如果原计划基本合理，直接返回原 JSON，不要画蛇添足

## 输出格式
返回 JSON，格式与原计划完全相同：
{{"nodes": {{...}}, "edges": {{...}}}}
如果原计划无需修改，返回原始 JSON 即可。
"""
```

### 2.4 容错层级

```
Layer 1: Critique LLM 成功 → 使用修正后的 plan
Layer 2: Critique LLM 超时/异常 → 使用原 plan（记录告警 CRITIQUE_FAILED）
Layer 3: Critique 返回畸形 JSON → 经 _normalize_plan 兜底，
         至少保证 capability 在白名单内
```

### 2.5 监控埋点

- `_plan_critiqued: bool` — 是否经过了 Critique
- `_plan_changed: bool` — Critique 是否实际修改了计划（nodes 或 edges 有差异）
- 日志：原计划 node count vs 修正后 node count

---

## 3. Worker 超时保护 + 重试优化

### 3.1 当前问题

`workers/base.py` 声明了 `DEFAULT_TIMEOUT = 60` 但从未使用。Worker 可能永久阻塞。重试之间无退避，连续重试加剧系统压力。

### 3.2 超时保护

`asyncio.wait_for()` 包裹 Worker 调用，将同步 Tool 调用放到 `asyncio.to_thread()` 中：

```python
output = await asyncio.wait_for(
    asyncio.to_thread(tool_fn.invoke, params),
    timeout=DEFAULT_TIMEOUT  # 60s
)
```

### 3.3 指数退避

```python
RETRY_BACKOFF_BASE = 1.5  # 秒

for attempt in range(max_retries + 1):
    try:
        # ... 执行 ...
    except (TimeoutError, Exception):
        if attempt < max_retries and _is_retryable(str(e)):
            delay = RETRY_BACKOFF_BASE ** (attempt + 1)  # 1.5s, 2.25s
            await asyncio.sleep(delay)
```

### 3.4 错误分类：可重试 vs 不可重试

不可重试的错误（参数错误/表不存在/语法错误）跳过重试：

```python
UNRETRYABLE_PATTERNS = [
    "no such table", "column not found", "syntax error",
    "invalid parameter", "权限不足",
]
```

---

## 4. Supervisor 循环上限

### 4.1 当前问题

Supervisor 无最大循环次数限制。如果 Worker 反复失败或从不返回 running，Supervisor 可能无限循环。

### 4.2 设计

```python
MAX_SUPERVISOR_LOOPS = 10  # 最多 10 轮调度

def supervisor_node(state):
    loop_count = state.get("_supervisor_loop_count", 0)
    if loop_count >= MAX_SUPERVISOR_LOOPS:
        # 强制终止：将所有 pending/running 标记为 failed
        # 记录告警 SUPERVISOR_MAX_LOOP
        return {"_all_steps_done": True, ...}
    # ...
    return {"_supervisor_loop_count": loop_count + 1, ...}
```

### 4.3 State 新增字段

`AgentState` TypedDict 中新增 `_supervisor_loop_count: int`。

---

## 5. SQL 降级链重构

### 5.1 当前问题

`_SQL_EMPTY_PATTERNS` 用字符串匹配检测空结果，SQL Agent 改变 Markdown 模板即失效。

### 5.2 结构化检测

`StepResult` 新增字段：

```python
class StepResult(TypedDict):
    # ...原有...
    output: str
    row_count: int | None     # SQL 查询返回行数
    is_empty: bool | None     # 是否为空结果
    error_type: str | None    # 错误类型分类
```

Supervisor 降级检测改为检查 `result.is_empty` 或 `result.row_count == 0`。

### 5.3 通用降级链

从 SQL→RAG 一级降级扩展为可配置的降级链：

```python
# degradation.py — 新文件
DEGRADATION_CHAIN = {
    "query_database":   ["search_knowledge"],      # SQL 空 → RAG
    "search_knowledge": ["query_database"],         # RAG 无结果 → SQL
    "generate_report":  ["search_knowledge"],       # 报告缺数据 → RAG
}
```

每个步骤最多降级 1 次，防止无限降级循环。

---

## 6. Reporter 防护增强

### 6.1 Context Filter：BM25 兜底

**当前**：CrossEncoder 不可用时**静默跳过**过滤，完全不相关的 RAG 结果喂给 Reporter。

**优化**：CrossEncoder 失败时降级为 BM25 关键词匹配过滤，而非完全跳过。

### 6.2 阈值可配置化

```python
CONTEXT_RELEVANCE_THRESHOLD = config.RERANKER_THRESHOLD  # 默认 0.35
```

### 6.3 all_success 检测结构化

**当前**：用字符串长度和特定错误字符串判断成功/失败。

**优化**：直接检查 `result.status`、`result.is_empty`、`result.error_type` 字段。

---

## 7. JSON 修复管道

### 7.1 当前问题

`_extract_json` 只做简单的 markdown 代码块剥离和 `{}` 边界查找。小模型输出的畸形 JSON（尾逗号、单引号、中文引号、缺失引号的 key）无法处理。

### 7.2 4 层修复管道

```
Layer 0: 直接 json.loads() — 最快路径
Layer 1: 截取最外层 {} 再解析
Layer 2: 修复常见小模型错误（尾逗号、中文引号、单引号、控制字符、无引号 key）
Layer 3: 暴力提取（正则找 JSON 片段）
```

每层尝试解析，成功即返回。全失败则触发 `PLAN_JSON_INVALID` 告警 + `_fallback_plan()`。

---

## 8. 可观测性

### 8.1 告警代码表

| 代码 | 级别 | 含义 |
|---|---|---|
| `PLAN_EMPTY` | warn | Planner 返回空计划，降级为 RAG 兜底 |
| `PLAN_JSON_INVALID` | warn | Planner 输出无法解析为 JSON |
| `PLAN_CAP_INVALID` | warn | 计划包含无效 capability，已跳过 |
| `PLAN_MISROUTE` | warn | Critique 检测到 capability 不匹配，已修正 |
| `CRITIQUE_FAILED` | warn | Plan Critique 调用失败，使用原计划 |
| `SUPERVISOR_MAX_LOOP` | error | Supervisor 达到最大循环次数 |
| `WORKER_TIMEOUT` | error | Worker 执行超时 |
| `WORKER_RETRY_EXHAUST` | error | Worker 重试耗尽，最终失败 |
| `RERANKER_UNAVAILABLE` | warn | CrossEncoder 不可用，降级为 BM25 |
| `DEGRADATION_TRIGGER` | info | 触发降级链 |

### 8.2 告警传递

- 写入 `AgentState.alerts: list[dict]`
- SSE 流中作为 `log` 事件发出（`level: warn|error`）
- 降级日志写入 `logs/degradation.jsonl`（非阻塞追加）

### 8.3 前端展示

前端通过 SSE `log` 事件的 `level` 字段区分展示样式：

- `info`: 正常进度，StatusBar 中滚动
- `warn`: 黄色警告标识，ThinkingPanel 中展示
- `error`: 红色错误标识，ThinkingPanel 中展示

---

## 9. 文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `multi_agent/planner.py` | 修改 | `_extract_json` 升级为 4 层修复管道；增加告警产出 |
| `multi_agent/critique.py` | **新增** | Plan Critique 节点实现 |
| `multi_agent/graph.py` | 修改 | 图中插入 Critique 节点；`AgentState` 新增字段 |
| `multi_agent/state.py` | 修改 | `StepResult` 新增 `row_count`, `is_empty`, `error_type`；`AgentState` 新增 `alerts`, `_supervisor_loop_count`, `_plan_critiqued`, `_plan_changed` |
| `multi_agent/supervisor.py` | 修改 | 循环上限；结构化降级检测；通用降级链 |
| `multi_agent/workers/base.py` | 修改 | asyncio 超时保护；指数退避；错误分类 |
| `multi_agent/reporter.py` | 修改 | BM25 兜底；阈值可配置；结构化 success 判断 |
| `multi_agent/degradation.py` | **新增** | 通用降级链注册 + 降级执行逻辑 |
| `multi_agent/alerts.py` | **新增** | 告警代码表 + PlanAlert 数据类 + 日志写入 |
| `config.py` | 修改 | 新增 `ENABLE_PLAN_CRITIQUE`, `RERANKER_THRESHOLD` 配置项 |
| `web/src/lib/constants.ts` | 修改 | 告警级别的图标/颜色映射 |

---

## 10. LLM 调用增量

| 阶段 | 旧调用数 | 新调用数 | 增量 |
|---|---|---|---|
| Planner | 1 | 1 | 0 |
| Plan Critique | — | 1 | **+1** |
| Supervisor | 0 | 0 | 0 |
| Workers | N | N | 0 |
| Reporter | 0~1 | 0~1 | 0 |
| **总计** | 1~2 + N | 2~3 + N | **+1** |

---

## 11. 测试策略

### 单元测试

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/test_json_repair.py` | 4 层修复管道：正常 JSON / 尾逗号 / 中文引号 / 单引号 / 无引号 key / 嵌套对象 / 空输入 |
| `tests/test_critique.py` | Critique prompt 生成 / 异常退出 / 空计划跳过 / 单步跳过 / config 开关 |
| `tests/test_worker_retry.py` | 超时触发 / 指数退避 / 不可重试错误 / 重试耗尽 |
| `tests/test_supervisor_loop.py` | 循环上限触发 / 强制终止 / 降级链 |
| `tests/test_degradation.py` | 降级链注册 / 重复降级防护 / 各 capability 降级目标 |
| `tests/test_alerts.py` | 告警代码完整性 / JSONL 写入 |

### 集成测试

| 场景 | 验证点 |
|---|---|
| Planner 输出错误 capability | Critique 修正 → Supervisor 正确调度 |
| SQL Worker 返回空结果 | 结构化 is_empty → 触发降级链 → RAG Worker 执行 |
| Worker 执行超时 | asyncio.TimeoutError → 重试 → 最终失败告警 |
| Supervisor 达到 10 轮上限 | 强制终止 → Reporter 降级汇总 |
| CrossEncoder 不可用 | BM25 兜底 → Reporter 正常输出 |

---

## 12. 演进路线

```
Phase A (本次) ──────────────────────────────────────────
  轻量加固：Critique + 超时 + 循环保护 + 降级链 + JSON修复

Phase B (后续) ──────────────────────────────────────────
  - Critique 升级为 ReAct Micro-Loop：每一步执行后 LLM 重评估
  - Supervisor 支持 OR 依赖（任一成功即可）和 partial success
  - 降级链支持自定义策略（用户可配置自己的降级规则）

Phase C (远期) ──────────────────────────────────────────
  - 跨会话的计划缓存与复用
  - Planner 小样本 fine-tune（收集纠正案例）
  - 在线评估：自动对比计划质量，统计 Critique 修改率
```
