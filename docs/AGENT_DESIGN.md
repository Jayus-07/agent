# AGENT_DESIGN — Multi-Agent 编排 + Workflow 引擎

> 多 Agent 编排与 Workflow 引擎的设计文档。配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md) / [RAG_DESIGN.md](RAG_DESIGN.md)

---

## 1. 概览

### 1.1 5 节点 + 9 Capability 一图

```
                      ┌──────────────┐
                      │   START      │
                      └──────┬───────┘
                             ▼
                      ┌──────────────┐
                      │   Planner    │  任务拆解 → Capability DAG
                      │   (LLM)      │  4 层 JSON 修复 + 5min LRU 缓存
                      └──────┬───────┘
                             ▼
                      ┌──────────────┐
                      │   Critique   │  规则引擎 (0ms) + anomaly LLM
                      │   (规则/LLM) │  移除冗余 RAG / 注入 business.analyze
                      └──────┬───────┘
                             ▼
                  ┌──────────────────────────┐
                  │        Supervisor        │  调度
                  │   (依赖检查 + 就绪派发)   │  Send[] 并行
                  │   Send[] → 多个 Skill    │  上限 10 轮
                  └──────────┬───────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │ sql.query│         │rag.search│         │report.  │
   │ SQLSkill │         │ RAGSkill │         │generate │
   └─────────┘         └─────────┘         └─────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             ▼
                      ┌──────────────┐
                      │  Supervisor  │    全部完成？
                      │  (all_done)  │
                      └──────┬───────┘
                             ▼
                      ┌──────────────┐
                      │   Reporter   │  汇总 + 引用 + LLM 一句话总结
                      └──────┬───────┘
                             ▼
                      ┌──────────────┐
                      │     END      │
                      └──────────────┘
```

### 1.2 9 个 Capability 矩阵

| Capability | Skill | 输入 | 输出 |
|---|---|---|---|
| `sql.query` | SQLSkill | 自然语言问题 | `SQLResult`（Pydantic：sql / tables / columns / rows / row_count） |
| `rag.search` | RAGSkill | 自然语言问题 | Markdown 回答 + 来源 |
| `business.analyze` | BusinessAnalysisSkill | SQL 结果（`previous_outputs`） | `BusinessInsight`（summary / risks / suggestions / confidence） |
| `report.generate` | ReportSkill | report_type + filters | Markdown 报告路径 |
| `email.send` | EmailSkill | recipients + subject + body | send 状态 |
| `data.export` | DataExportSkill | data + format | 文件路径 |
| `web.search` | WebSearchSkill | 搜索词 | top N URL |
| `web.crawl` | WebCrawlSkill | URL | 文本内容 |
| `data.collect` | DataCollectionSkill | 采集类型 | CollectResult |

**注册原则**：新增 Skill 只需 3 步（写类 / 导入 / 自动发现），无需修改框架代码。

---

## 2. 拓扑

### 2.1 LangGraph `StateGraph` 节点

```
START → planner → critique → supervisor ⇄ skills → reporter → END
```

| 节点 | 类型 | 调用 |
|---|---|---|
| `planner` | graph_node | LLM 生成 DAG（带 4 层 JSON 修复 + 后置规则过滤） |
| `critique` | graph_node | 规则引擎（0ms）+ anomaly LLM 兜底 |
| `supervisor` | graph_node | 依赖检查 + 就绪派发 + 卡死检测 |
| `<skill_name>` | function_node（动态） | Skill 注册时自动加入图 |
| `reporter` | graph_node | 结构化渲染 + 引用 + LLM 总结 |

### 2.2 Send[] 并行派发

`supervisor_node` 返回 `list[Send]`，LangGraph 自动并行执行：

```python
# backend/orchestration/supervisor/scheduler.py
def route_after_supervisor(state):
    ready = compute_ready_steps(state)  # 依赖检查
    if not ready:
        return "reporter" if all_done(state) else END
    return [Send(skill_node, {"step_id": sid, ...}) for sid in ready]
```

**关键设计**：

- 每轮 Supervisor 找"就绪步骤"（依赖完成的步骤）
- 多个就绪 → Send[] 并行
- 单步完成 → 回到 Supervisor（形成 loop）
- 全部完成 → reporter
- **Supervisor 上限 10 轮**（`MAX_SUPERVISOR_LOOPS`）

### 2.3 状态字段（TODOs）

`_supervisor_loop_count` 仅在 supervisor_node 递增，Send[] 并行派发后**所有并行 Skill 都返回才增加一次**。SSE 流式路径下从 `step_results` 估算（`system._count_rounds_from_results`）。

---

## 3. 节点详解

### 3.1 Planner（任务拆解）

**职责**：根据用户问题生成 `Capability DAG`。

```json
{
  "nodes": {
    "1": {"step_id": "1", "capability": "sql.query", "description": "查询最近 30 天销售下降商品", "params": {"question": "哪些商品销量下降 20%？"}},
    "2": {"step_id": "2", "capability": "business.analyze", "description": "分析下降原因", "params": {}}
  },
  "edges": {"2": ["1"]}
}
```

**关键能力**：

- 4 层 JSON 修复管道（兜 LLM 输出格式错误）
- 5 分钟 LRU 缓存（同样问题不重复规划）
- 后置规则过滤（兜底 capability 白名单）
- 仅产出 DAG，不调用任何 Tool / Skill / DB

**关键限制**：

- Planner 失败兜底为单步 `rag.search` 计划（**缺乏 fallback 智能**）

### 3.2 Critique（计划审查）

**职责**：审查 Planner 产出，可能改写。

| 阶段 | 实现 | 耗时 |
|---|---|---|
| 规则引擎 | 检测 dependency 缺失 / 冗余步骤 / 必加步骤 | 0ms |
| LLM 兜底 | 仅在规则 anomaly 时调 LLM（如节点 > 3 个 + 复杂） | 1-2s |

**规则示例**：

- 仅 1 个 `rag.search` 步骤 → 跳过 Critique（`node_count > 1` 才运行）
- `sql.query` 后无 `business.analyze` → 自动注入
- 多个 `rag.search` 无依赖 → 提示合并

### 3.3 Supervisor（调度决策）

**职责**：按依赖关系调度 + Send[] 并行派发。

**核心算法**：

```python
def supervisor_node(state):
    # 1. 更新 step_results 状态（pending → running）
    # 2. 找就绪步骤（依赖完成的所有 step）
    # 3. 卡死检测（无可执行 + 未完成）
    # 4. 累加 _supervisor_loop_count
    # 5. 返回 Send[] 或 reporter / END
```

**关键设计**：

- 依赖检查：参考 `edges[step_id]`（前置依赖 list）
- 就绪派发：所有 `dep_id` 在 `step_results` 中 `status == success` 才就绪
- 自动降级：见 [§6 降级链](#6-降级链)
- 上限 10 轮（`MAX_SUPERVISOR_LOOPS`）

### 3.4 Skills（动态节点）

**职责**：每个 Skill 是一个 LangGraph `function_node`，由 `tool_registry` 注册时自动加入图。

**自动发现机制**（ADR-0001）：

```python
# backend/orchestration/tool_registry.py
class ToolRegistry:
    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        # import 触发 BaseSkill 自动注册（__init_subclass__）

    def get_skill_node_names(self) -> list[str]:
        """从已注册 Skill 派生 node 名字（单一事实来源）"""
        return [f"{s.name}_node" for s in self._skills.values()]
```

### 3.5 Reporter（汇总）

**职责**：把 `step_results` 汇总成最终 Markdown 回答。

- 结构化渲染（含表格 / 列表）
- 引用来源合并（所有 Skill 的 sources）
- LLM 一句话总结（可选）
- 上下文过滤（按相关性）
- 参考文献提取

---

## 4. Capability 体系

### 4.1 注册原则

```
backend/skills/<name>/skill.py     ← 创建 Skill 类（继承 BaseSkill）
backend/skills/registry.py        ← import 触发自动注册
```

无需修改框架代码。Planner 从 `ToolRegistry.get_available_capabilities()` 自动派生 capability 列表。

### 4.2 9 个 Skill 详解

#### ① sql.query → SQLSkill

- 入口：`backend/skills/sql/skill.py`
- 包装：`SQLAgent.ask_struct(question, current_user_id)` → `SQLResult`
- 6 层安全：详见 [PRD.md §4.3](PRD.md)
- 错误：`SQLStatus`（8 种）→ 失败时 Supervisor 决定降级

#### ② rag.search → RAGSkill

- 入口：`backend/skills/rag/skill.py`
- 包装：`pipeline.ask(question, session_id, kb_id)` → Markdown + 来源
- 6 段流水线：详见 [RAG_DESIGN.md §3](RAG_DESIGN.md)

#### ③ business.analyze → BusinessAnalysisSkill

- 入口：`backend/skills/business_analysis/skill.py`
- 输入：前置 `sql.query` 的 `previous_outputs`（dataframe）
- 输出：`BusinessInsight(summary / risks / suggestions / confidence)`
- 自动从 `step_results` 找最近 SQL 结果

#### ④ report.generate → ReportSkill

- 入口：`backend/skills/report/skill.py`
- 6 种内置：`daily_sales / product_performance / inventory_health / ad_performance / order_fulfillment / customer_analysis`
- 详见 [PRD.md §4.5](PRD.md)

#### ⑤ email.send → EmailSkill

- 入口：`backend/skills/email/skill.py`
- SMTP 真发（生产）/ mock 收件人（演示）

#### ⑥ data.export → DataExportSkill

- 入口：`backend/skills/data_export/skill.py`
- CSV / Excel / JSON 输出

#### ⑦ web.search → WebSearchSkill

- 入口：`backend/skills/web_search/skill.py`
- 第三方搜索 API（可切换）

#### ⑧ web.crawl → WebCrawlSkill

- 入口：`backend/skills/web_crawl/skill.py`
- HTTP + Playwright（待补）

#### ⑨ data.collect → DataCollectionSkill

- 入口：`backend/skills/data_collection/skill.py`
- 5 阶段 Pipeline（Fetcher / Parser / Cleaner / Analyzer / Writer）

### 4.3 数据协议

| 数据契约 | 文件 | 用途 |
|---|---|---|
| `SQLResult`（Pydantic） | `backend/skills/sql/models.py` | Skill 层数据交换 |
| `BusinessInsight` | `backend/skills/business_analysis/models.py` | 业务分析输出 |
| `StepResult` | `backend/orchestration/state.py` | 单步执行结果 |
| `BaseCapability` | `backend/orchestration/tool_registry.py` | Capability 抽象 |

**步骤间数据传递**：`Supervisor` 在 `Send` 中注入 `previous_outputs`（前置步骤的 `output` 自动传给后置步骤）。

---

## 5. AgentState 状态机

### 5.1 字段定义

```python
class AgentState(TypedDict, total=False):
    question: str                               # 用户原始问题
    kb_id: str                                  # 知识库 ID
    plan: dict                                  # Planner 产出 DAG
    step_results: dict[str, StepResult]         # 合并的步骤结果（_merge_step_results reducer）
    current_step_id: str                        # 当前正在执行的步骤
    messages: list[BaseMessage]                 # ReAct 对话历史（add_messages reducer）
    final_answer: str                           # Reporter 产物
    alerts: list[PlanAlert]                     # SSE 流展示
    _supervisor_loop_count: int                 # 调度轮次
    _plan_critiqued: bool                       # 是否经过 Critique
    _plan_changed: bool                         # Critique 是否改写
    _degraded_steps: set[str]                   # 降级步骤集合（operator.or_ reducer）
```

### 5.2 StepResult 字段

```python
class StepResult(TypedDict, total=False):
    step_id: str
    capability: str                             # Planner 分配的 capability
    description: str
    status: Literal["pending", "running", "success", "failed", "skipped"]
    output: Any                                 # Worker 返回值（dict / str）
    error: str
    retries: int
    started_at: float
    finished_at: float
    row_count: int                              # SQL 返回行数
    is_empty: bool                              # SQL / RAG 空数据
    error_type: str                             # timeout / parse / auth / network / unknown
```

### 5.3 流转路径

```
1. 初始（events.make_initial_state）
   空 plan + 空 step_results + 计数器归零
2. planner_node
   写入 plan
3. critique_node
   可能改写 plan，写 _plan_critiqued / _plan_changed
4. supervisor_node
   更新 step_results 状态、找就绪步骤、累加 _supervisor_loop_count
5. Send[] 派发
   多个 Skill 节点并行执行，各 Skill 写自己的 step_result
6. 循环
   Skill 完成后回到 supervisor，直到 all_done
7. reporter_node
   写 final_answer
8. 降级
   execute_degradation 在 all_done 但有 is_empty 时插入 fallback step
```

### 5.4 自定义 reducer

| 字段 | Reducer | 备注 |
|---|---|---|
| `step_results` | `_merge_step_results` | 并行结果 union |
| `_degraded_steps` | `operator.or_` | set 合并（**必须用新 set**，禁止原地 .add()） |
| `messages` | `add_messages`（LangGraph 内置） | ReAct 对话历史 |

---

## 6. 降级链

**触发条件**：单步 `is_empty=True` 或 `status=failed`（非 timeout）

| 失败步骤 | 降级到 |
|---|---|
| `sql.query` 空 | `rag.search` |
| `rag.search` 空 | `sql.query` |
| `report.generate` 缺数据 | `rag.search` |

**实现**：[backend/orchestration/supervisor/degradation.py](backend/orchestration/supervisor/degradation.py)

```python
def execute_degradation(state):
    for step_id, sr in state["step_results"].items():
        if sr.get("is_empty") or sr.get("status") == "failed":
            fallback = DEGRADATION_MAP.get(sr["capability"])
            if fallback:
                # 插入新 step，依赖原 step
                insert_new_step(state, step_id, fallback)
                state["_degraded_steps"].add(step_id)  # 注意：新 set
```

**当前限制**：

- 仅 3 条映射（其他 capability 失败无降级）
- 每步最多降级 1 次（防止死循环）
- 降级标记写入 `_degraded_steps`，用于 Trace 记录

---

## 7. SSE 事件协议（SSE v2）

`POST /chat/stream` 事件类型：

| event | 含义 | 前端处理 |
|---|---|---|
| `meta` | 握手（node_labels 映射表） | 写入 `store.nodeLabels` |
| `status` | 宏观阶段切换（planner / supervisor / sql_worker / ...） | 写入 `store.currentStatus`，StatusBar 消费 |
| `log` | 详细时间线（含 payload 入参/出参） | 环形追加（200 上限） |
| `delta` | 流式内容块（句子级切分） | `store.deltaText += content` |
| `done` | 结束信号（elapsed + sources） | `replaceLastAssistant` + `persistSession` |
| `error` | 错误/中止 | 立即替换最后一条 assistant |

**编码格式**：

```
event: <type>
data: <json>

```

完整格式（`backend/app/api/routes/chat.py`）：

```python
def _sse_encode(event: dict) -> str:
    evt_type = event["event"]
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {evt_type}\ndata: {payload}\n\n"
```

**关键设计**：

- **Backpressure**（P0-1）：队列满 → 记 metric + 触发 `stop_event` + 入队 sentinel 干净收尾
- **阻塞拉取**（P1-14）：`q.get(timeout=0.5)` 而非 100Hz 轮询
- **真实指标**（P0-2）：记录 ok / error / aborted 计数
- **中止双通道**：前端 abort fetch + `POST /chat/abort` 触发 `stop_event`

---

## 8. Workflow 引擎

### 8.1 声明式 DSL

```python
@workflow(
    name="daily_report",
    objects=["sales", "inventory", "promotion"],
    actions=["fetch", "analyze", "email"],
    examples=["生成今天的日报"],
    default_kbs=["analytics"],
    category="report",
)
class DailyReportWorkflow:
    @step(depends_on=[], timeout_sec=30)
    def fetch_sales(self, ctx): ...

    @step(depends_on=[], timeout_sec=30)
    def fetch_inventory(self, ctx): ...

    @step(depends_on=["fetch_sales", "fetch_inventory"], retry=2)
    def rag_query_template(self, ctx): ...

    @step(depends_on=["rag_query_template"], timeout_sec=60)
    def agent_analyze(self, ctx):
        # 调用 BusinessAnalyzer
        ...

    @step(depends_on=["agent_analyze"])
    def generate_report(self, ctx): ...

    @step(depends_on=["generate_report"], on_error="skip")
    def send_email(self, ctx): ...
```

### 8.2 引擎

| 组件 | 职责 | 文件 |
|---|---|---|
| `@workflow` 装饰器 | 注册 workflow + 业务元数据 | `backend/orchestration/workflow/decorator.py` |
| `@step` 装饰器 | 声明依赖 / 重试 / 超时 / 错误处理 | 同上 |
| `DAG.layers` | Kahn 拓扑分层 | `backend/orchestration/workflow/dag.py` |
| `WorkflowExecutor` | 按层 `asyncio.gather` 并行执行 | `backend/orchestration/workflow/executor.py` |
| `WorkflowContext` | 步骤间数据传递 | `backend/orchestration/workflow/context.py` |
| `WorkflowRegistry` | 启动时扫描 + 单一事实来源 | `backend/orchestration/workflow/registry.py` |
| `TaskRouter` | 3 维加权（0.3 obj + 0.2 action + 0.5 workflow_match） | `backend/orchestration/workflow/router.py` |
| `WorkflowScheduler` | APScheduler 包装 | `backend/orchestration/workflow/scheduler.py` |

### 8.3 已注册的 Workflow

| Workflow | 步骤数 | 触发 |
|---|---|---|
| `daily_report` | 7 步（5 层） | APScheduler 9:00 / 手动 trigger |
| `inventory_alert` | 8 步（6 层） | APScheduler 每日扫描 / 阈值命中 |

### 8.4 Task Router

```python
def route(self, question: str) -> tuple[str, float]:
    # 0.3·object + 0.2·action + 0.5·workflow_match
    score = 0.3*obj_score + 0.2*act_score + 0.5*wf_score
    if score >= 0.3:
        return best_workflow, score
    # 中段 → LLM 兜底（Phase 5 TODO）
    return self._llm_fallback(question), 0.0
```

### 8.5 关键 API

| 路径 | 用途 |
|---|---|
| `GET /api/workflows` | 所有注册 workflow + 调度元数据 |
| `POST /api/workflows/{name}/trigger` | 手动触发（async 立即返回 run_id） |
| `GET /api/workflows/runs` | 运行历史（分页） |
| `GET /api/workflows/runs/{run_id}` | 单次 run 详情（含 outputs） |
| `GET /api/schedules` | 定时任务列表 |
| `GET /api/schedules/{workflow_name}` | 单个 schedule 详情 |
| `PATCH /api/schedules/{workflow_name}` | 修改 hour / minute |

---

## 9. 限制与已知 TODO

### 9.1 调度

- **Supervisor 循环上限 10 轮**：复杂 DAG 可能被打断，未实现动态重规划
- **Send[] 并行无并发限流**：若 plan 派发 10+ skill 同时执行，无上限控制
- **Workflow LLM 兜底**：`_llm_fallback` 是 stub，未真实调 LLM

### 9.2 Planner

- **失败兜底单一**：仅回退到 `rag.search`，不知用户原意是 SQL 还是 RAG
- **仅 1 步时跳过 Critique**：单步计划完全跳过审查

### 9.3 降级

- **仅 3 条降级映射**：其他 capability 失败无降级路径
- **每步最多降级 1 次**：防死循环但牺牲完成率

### 9.4 Workflow

- **无版本管理 / 灰度发布**
- **无可视化 UI**：只有 `DAG.layers` 可调试输出
- **无输入参数 schema 校验**（自由 dict）
- **无运行时人工审批 / 暂停**
- **`data_collection/scheduler.py` 的 `start/stop` 仍 `NotImplementedError`**

### 9.5 状态

- **`_degraded_steps` 必须新 set**（用 `|` 运算符），禁止原地 `.add()`（重构时易踩坑）
- **`_supervisor_loop_count` 估算有启发式**（SSE 流式路径）
- **`messages` reducer 通道未启用**（ReAct 风格未实现）

---

## 10. 关键文件索引

### 10.1 编排

| 文件 | 职责 |
|---|---|
| `backend/orchestration/graph/builder.py` | StateGraph 构建 + Skill 自动发现 |
| `backend/orchestration/graph/system.py` | MultiAgentSystem.ask / stream_events |
| `backend/orchestration/graph/events.py` | SSE 事件分派 + make_initial_state |
| `backend/orchestration/state.py` | AgentState + StepResult 定义 |
| `backend/orchestration/tool_registry.py` | Capability 注册表（自动派生） |
| `backend/orchestration/supervisor/scheduler.py` | Supervisor + Send[] |
| `backend/orchestration/supervisor/degradation.py` | 降级链 |
| `backend/orchestration/workflow/dag.py` | DAG 拓扑分层 |
| `backend/orchestration/workflow/decorator.py` | @workflow / @step |
| `backend/orchestration/workflow/executor.py` | Async Executor |
| `backend/orchestration/workflow/registry.py` | WorkflowRegistry |
| `backend/orchestration/workflow/router.py` | TaskRouter |
| `backend/orchestration/workflow/scheduler.py` | APScheduler 包装 |

### 10.2 节点

| 文件 | 职责 |
|---|---|
| `backend/agents/planner/planner.py` | planner_node + 4 层 JSON 修复 |
| `backend/agents/planner/critique.py` | critique_node + 规则引擎 |
| `backend/agents/reporter/reporter.py` | reporter_node + 结构化渲染 |

### 10.3 Skill 池

| 目录 | 文件 |
|---|---|
| `backend/skills/sql/` | `skill.py` |
| `backend/skills/rag/` | `skill.py` |
| `backend/skills/business_analysis/` | `skill.py` |
| `backend/skills/report/` | `skill.py` |
| `backend/skills/email/` | `skill.py` |
| `backend/skills/data_export/` | `skill.py` |
| `backend/skills/web_search/` | `skill.py` |
| `backend/skills/web_crawl/` | `skill.py` |
| `backend/skills/data_collection/` | `skill.py` |
| `backend/skills/registry.py` | 集中注册 |

### 10.4 已废弃的 facade

- `backend/tools/rag.py`、`backend/tools/sql.py` 等 → **向后兼容 re-export**，实际逻辑已迁至 `backend/skills/*`

---

## 验证

最后验证：2026-08-10 · 与代码一致（5 节点 + 9 Capability + 10 轮 Supervisor + 3 条降级链）。
