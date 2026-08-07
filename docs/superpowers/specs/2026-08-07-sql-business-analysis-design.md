# SQL 查询能力架构重构 — 设计文档

> 日期：2026-08-07
> 状态：已确认

## 背景

当前 `SQLSkill` 内部耦合了 SQL 查询和手动 Trace 管理，缺少独立的业务分析层。Planner 无法将 SQL 查询和业务分析编排为两个独立 Capability。

## 目标架构

```
用户问题 → Router → Planner → Supervisor
                              │
                    Capability DAG:
                      task_1: sql.query ──────┐
                      task_2: business.analyze ◄── (depends_on task_1)
                              │
                    Supervisor 调度:
                      第1轮: SQLSkill → SQLResult
                      第2轮: BusinessAnalysisSkill → BusinessInsight
```

### 核心原则

- SQL 是 Data Capability（数据能力）
- BusinessAnalyzer 是 Reasoning Capability（推理能力）
- 两者通过 SQLResult 数据协议解耦
- Planner 负责编排 → Supervisor 负责调度 → Runtime 统一 Trace

## 新增模块

### 1. `backend/skills/sql/models.py` — 数据协议

```python
class SQLResult(BaseModel):
    sql: str
    tables: list[str]
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time: float
```

### 2. `backend/skills/business_analysis/` — 业务分析 Skill

- `skill.py` — BusinessAnalysisSkill（capability: `business.analyze`）
- `analyzer.py` — BusinessAnalyzer 核心（RAG + LLM 分析）
- `models.py` — BusinessInsight Pydantic 模型
- `prompts/business_analysis.md` — LLM 分析 Prompt
- `__init__.py`

```python
class BusinessInsight(BaseModel):
    summary: str
    risks: list[str]
    suggestions: list[str]
    confidence: float
    related_knowledge: list[str]
```

### 3. `backend/observability/trace_middleware.py` — 统一 Trace

在 LangGraph runtime 层统一拦截所有节点执行，不再由各 Skill 手动管理 Span。

## 变更清单

| 操作 | 文件 | 说明 |
|---|---|---|
| ✨新建 | `backend/skills/sql/models.py` | Pydantic SQLResult |
| ✨新建 | `backend/skills/sql/__init__.py` | 包初始化 |
| 🔧修改 | `backend/skills/sql/skill.py` | 精简：移除手动 Trace，返回 Pydantic SQLResult |
| ✨新建 | `backend/skills/business_analysis/skill.py` | BusinessAnalysisSkill |
| ✨新建 | `backend/skills/business_analysis/analyzer.py` | 业务分析核心 |
| ✨新建 | `backend/skills/business_analysis/models.py` | BusinessInsight |
| ✨新建 | `backend/skills/business_analysis/prompts/business_analysis.md` | Prompt 模板 |
| ✨新建 | `backend/skills/business_analysis/__init__.py` | 包初始化 |
| 🔧修改 | `backend/skills/registry.py` | 注册新 Skill |
| 🔧修改 | `backend/orchestration/supervisor/scheduler.py` | Send 注入 previous_outputs |
| ✨新建 | `backend/observability/trace_middleware.py` | 统一 Trace 中间件 |
| ✨新建 | `backend/tests/sql/test_business_analysis.py` | 业务分析测试 |
| 🔧修改 | `backend/tests/sql/test_sql_skill_structured.py` | 适配精简后 SQLSkill |

## 测试场景

1. **库存不足**：sql.query + business.analyze → SQLResult → BusinessInsight(risks + suggestions)
2. **库存日报**：sql.query → business.analyze → report.generate 三步骤 DAG
