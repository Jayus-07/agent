# Workflow Phase 1 — 实施计划

> 范围：Phase 1 完整骨架 — Decorator + Registry + DAG Builder + Async Executor + Router + 1 个示例 workflow。
> 跑通 daily_report 后再扩展 inventory_alert / product_analysis。
> 关联文档：[workflow-engine.md](workflow-engine.md)（架构概览）。

## 决策锁定（2026-07-30）

### Workflow DSL

- **显式 `depends_on`**，不默认顺序依赖
- **Runtime 自动计算并行**（DAG 入度=0 节点 → `asyncio.gather`）
- **字段职责分离**：
  - `depends_on`（DAG 拓扑）vs `retry/timeout`（Runtime）vs `condition`（Control）

### Workflow Metadata

- **强类型 Schema**（不是 dict）
- `@workflow(name, description, objects, actions, examples, default_kbs)`
- `WorkflowMeta` dataclass
- Registry 启动扫描 → Router Index

### Task Router

- **规则 + LLM 兜底**（规则 confidence < 0.7 才调 LLM）
- **加权评分**：`0.3 * Object + 0.2 * Action + 0.5 * WorkflowMatch`
- **业务对象词典** 由 Workflow Registry 自动生成（不硬编码）
- Router Index 包含 examples embedding（启动时算一次）

### Capability 抽象

- `BaseCapability` → `BaseSkill` / `BaseAgentSkill` 双继承（不要 is_agent_skill 标识）
- Registry 统一管理但类型区分
- LLM 统一走 `infra/llm/proxy.py`

### Workflow Step 类型

- 3 大类：**capability / function / control**
- Capability 内部再分 data_skill / agent_skill，Workflow 不感知
- Control 包含 parallel / conditional

### Workflow 嵌套

- 第一版**禁止**（避免循环）
- 只允许 `Workflow → Capability → Skill`

## 目标（可验证）

| # | 目标 | 验证方式 |
|---|------|---------|
| 1 | daily_report 手动触发能跑完 | 调 trigger API → 看 ctx.outputs |
| 2 | DAG 自动并行 | 看 trace 时间线（fetch_sales + fetch_inventory 重叠） |
| 3 | Step 失败可降级（on_error=skip） | 故意 mock 失败 → 看是否跳过 |
| 4 | Trace 集成 | Trace 详情页能看到 workflow_run 的所有 span |
| 5 | Router 路由"生成日报" → workflow | 跑规则匹配测试 |
| 6 | Router 路由"分析销量" → agent | 跑规则匹配测试 |

## 实施顺序（用户指定）

```
1. Decorator (@workflow + @step)
        ↓
2. Workflow Registry (扫描 + Router Index)
        ↓
3. DAG Builder (拓扑分层)
        ↓
4. Async Executor (asyncio.gather + retry)
        ↓
5. Trace Integration (parent span + child spans)
        ↓
6. Task Router (规则 + LLM fallback)
        ↓
7. daily_report 示例 + 端到端验证
```

## 文件清单

### 新建（11 个）

```
backend/orchestration/
├── workflow/
│   ├── __init__.py              导出 @workflow / @step / WorkflowRegistry
│   ├── meta.py                  WorkflowMeta / StepConfig dataclass
│   ├── decorator.py             @workflow + @step 装饰器
│   ├── registry.py              WorkflowRegistry + Router Index 生成
│   ├── dag.py                   DAG 拓扑分层
│   ├── executor.py              Async Executor (asyncio.gather)
│   ├── context.py               WorkflowContext (state 对象)
│   ├── router.py                Task Router (规则 + LLM 兜底)
│   ├── persistence.py           workflow_runs 持久化
│   └── router_index.py          Router Index 数据结构
├── capability/
│   └── base.py                  BaseCapability + BaseSkill + BaseAgentSkill
└── workflows/
    └── daily_report.py          示例 workflow

backend/app/api/routes/
└── workflows.py                 API: list / trigger / runs
```

### 修改（3 个）

```
backend/orchestration/skills/
└── base.py                      重构：继承 BaseCapability

backend/data_collection/
└── scheduler.py                 升级：接入 APScheduler

backend/orchestration/
└── tool_registry.py             扩展：注册 Workflow（不只是 Skill）
```

## 核心 API 设计

### Decorator

```python
from orchestration.workflow import workflow, step

@workflow(
    name="daily_report",
    description="每日经营日报",
    objects=["daily_report", "sales", "operation"],
    actions=["generate", "send"],
    examples=[
        "生成今天的经营日报",
        "跑一下今天的销售日报",
        "把日报发给我",
    ],
    default_kbs=["analytics"],
)
class DailyReport:
    @step()  # 独立节点（无 depends_on）
    async def fetch_sales(self, ctx: WorkflowContext):
        return {"sales": ...}
    
    @step()  # 独立节点（自动与 fetch_sales 并行）
    async def fetch_inventory(self, ctx: WorkflowContext):
        return {"inventory": ...}
    
    @step(
        depends_on=["fetch_sales", "fetch_inventory"],
        timeout_sec=30,
        on_error="skip",  # abort / skip / agent_degrade
    )
    async def rag_query_template(self, ctx):
        return {"template": ...}
    
    @step(
        depends_on=["fetch_sales", "fetch_inventory", "rag_query_template"],
    )
    async def agent_analyze(self, ctx):
        # 调 Business Agent Skill（不是 Planner）
        return await inventory_analyzer_skill.run({...})
    
    @step(depends_on=["agent_analyze"])
    async def generate_report(self, ctx):
        return await report_skill.execute({...})
    
    @step(depends_on=["generate_report"], retry=2, timeout_sec=60)
    async def send_email(self, ctx):
        return await email_skill.execute({...})
```

### WorkflowContext

```python
@dataclass
class WorkflowContext:
    workflow_name: str
    run_id: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]        # key = step name
    trace_id: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str                     # running / success / failed / partial
    error: str | None
    skip_steps: set[str]
```

### WorkflowRegistry

```python
class WorkflowRegistry:
    def __init__(self):
        self._workflows: dict[str, type] = {}
        self._router_index: dict[str, RouterEntry] = {}
    
    def register(self, cls):
        """注册一个 Workflow 类（自动读 workflow_meta）"""
    
    def build_router_index(self):
        """扫描所有 workflow，生成 Router Index（含 examples embedding）"""
    
    def get(self, name: str) -> type | None:
        return self._workflows.get(name)
    
    def list(self) -> list[WorkflowMeta]:
        return [cls.workflow_meta for cls in self._workflows.values()]
```

### DAG

```python
@dataclass
class DAG:
    steps: dict[str, StepConfig]
    
    @property
    def layers(self) -> list[list[str]]:
        """拓扑分层：每层内的节点可并行"""
    
    def validate(self):
        """检测循环 + 缺失依赖"""
```

### Executor

```python
class WorkflowExecutor:
    async def run(self, cls: type, inputs: dict) -> WorkflowContext:
        """执行一个 workflow class，返回 WorkflowContext"""
        # 1. 创建 ctx + 根 trace span
        # 2. DAG 拓扑分层
        # 3. 每层 asyncio.gather
        # 4. 每个 step 子 span + retry + on_error
```

### Task Router

```python
@dataclass
class RouteResult:
    intent: str                          # daily_report / inventory_alert / ...
    candidate_mode: list[str]            # ["workflow"] / ["agent"] / ["agent", "workflow"]
    workflow_candidate: str | None
    confidence: float                    # 0~1

class TaskRouter:
    async def route(self, user_query: str) -> RouteResult:
        # 1. 规则层（业务对象 + 动作 + examples embedding 相似度）
        # 2. 加权评分 (0.3*Object + 0.2*Action + 0.5*WorkflowMatch)
        # 3. confidence < 0.7 → LLM 兜底
```

## API 端点

```python
# backend/app/api/routes/workflows.py

@router.get("/workflows")
async def list_workflows():
    """列出所有注册的 Workflow"""

@router.get("/workflows/runs")
async def list_runs(workflow_name: str = "", page: int = 1):
    """查询运行历史"""

@router.post("/workflows/{name}/trigger")
async def trigger_workflow(name: str, inputs: dict = Body({})):
    """手动触发"""

@router.get("/workflows/{name}/status")
async def workflow_status(name: str):
    """查询 workflow 配置 + 上次运行状态"""
```

## 验证清单

- [ ] daily_report 注册成功（启动日志确认）
- [ ] 手动 trigger 能跑完 6 个 Step
- [ ] fetch_sales + fetch_inventory 在 trace 时间线**重叠**（并行）
- [ ] 故意 mock fetch_sales 抛异常 → on_error="skip" 跳过
- [ ] 故意 mock send_email 抛异常 → Workflow 整体报 failed 但其他 step 不影响
- [ ] Trace 详情页能看到 1 个 workflow_run span + 6 个 step spans
- [ ] Router 输入"生成今天的日报" → confidence > 0.7，路由到 workflow
- [ ] Router 输入"分析销量为什么下降" → confidence < 0.7（无 workflow 匹配），调 LLM 兜底
- [ ] Workflow 注册表新增 workflow 后 Router Index 自动包含

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| DAG 死循环 | 拓扑分层时空层抛 `CycleDetectedError` |
| Workflow 重名 | `register()` 检查 name 唯一，重复抛错 |
| Step 依赖不存在的 step | 启动时静态校验 |
| Step timeout 导致 trace span 残留 | `asyncio.wait_for` + try/finally |
| LLM Router 调用频率 | 复用 `infra/llm/proxy.py` 频率限制 |
| BaseSkill 重构影响现有 6 个 Skill | 兼容层：BaseSkill 默认行为不变 |
| examples embedding 启动慢 | 启动时后台线程预热，不阻塞 |

## Commit 拆分

| # | Commit | 内容 |
|---|--------|------|
| 1 | `feat(workflow): WorkflowMeta + Decorator` | meta.py + decorator.py |
| 2 | `feat(workflow): WorkflowRegistry + Router Index` | registry.py + router_index.py |
| 3 | `feat(workflow): DAG 拓扑分层` | dag.py |
| 4 | `feat(workflow): Async Executor + Trace` | executor.py + context.py |
| 5 | `feat(workflow): Task Router 三层评分` | router.py |
| 6 | `feat(workflow): BaseCapability 重构` | capability/base.py + skills/base.py 兼容 |
| 7 | `feat(workflow): daily_report 示例 + APScheduler` | workflows/daily_report.py + scheduler.py |
| 8 | `feat(workflow): workflow_runs 持久化 + API` | persistence.py + routes/workflows.py |

## 范围外（明确不做）

- ❌ Workflow 嵌套 / SubWorkflow
- ❌ YAML 配置定义 workflow（Python 装饰器优先）
- ❌ 持久化上下文恢复（重启不续跑）
- ❌ 可视化 DAG 编辑器
- ❌ Workflow 版本管理 / 回滚
- ❌ 库存预警 Workflow（Phase 2）

## 预期产出

- **代码**：~700 行（含骨架 + daily_report 示例）
- **测试**：手动验证 7 项（见验证清单）
- **文档**：本文件 + 更新 workflow-engine.md 决策对齐 section
- **演示**：daily_report 端到端跑通 + 邮件成功发送

## 下一步

实施计划确认后，按 8 个 commit 顺序执行，每个 commit 完成后跑 `py_compile` + 手动验证该 commit 的目标。