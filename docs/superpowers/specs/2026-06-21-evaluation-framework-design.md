# Evaluation Framework — 设计规格书

> 状态: 设计完成，待用户审阅
> 日期: 2026-06-21
> 目标: 建立可度量、可追踪、可展示的自动化评估体系

## 1. 背景与动机

当前项目有 196 个单元/集成测试，但缺少端到端的质量评估。改 prompt、调参数、换模型后，无法量化"变好了还是变坏了"。评估体系解决三个问题：

1. **盲调** — 每次优化靠感觉，没有基线对比
2. **面试展示** — 需要数字证明系统设计有效性
3. **回归防线** — 新改动引入退化时自动告警

## 2. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 评估框架形态 | 独立 `evaluation/` 模块 | 一等公民，可独立运行，面试可展示 |
| 测试集来源 | 手写高精度数据集 | 标注质量优先于数量，企业场景要求可靠 |
| 覆盖范围 | Planner + RAG + SQL + E2E 全覆盖 | 全面展示系统能力，避免"只测局部"的印象 |
| 评分方式 | 硬指标全自动 + LLM-Judge 周期性 + 人工抽检 | 兼顾效率与可靠性 |
| 报告格式 | 控制台摘要 + Markdown 详细报告 + 时间戳归档 | 快速查看 + 深度分析 + 趋势追踪 |

## 3. 模块结构

```
evaluation/
├── __init__.py              # 评估体系入口，导出 run_all(), run_module()
├── cli.py                   # 统一命令行: python -m evaluation [module] [--flags]
├── dataset.py               # 测试集加载器，从 datasets/ 读 JSON，校验 schema
├── runner.py                # 通用执行器：读测试集 → 跑子系统 → 收集结果
├── metrics.py               # 指标计算库（recall@k, MRR, exact_match, etc.）
├── judge.py                 # LLM-as-Judge 评分器（端到端答案质量）
├── report.py                # 报告生成：控制台摘要 + Markdown 详细报告
├── models.py                # Pydantic 模型（TestCase, EvalResult, EvalReport）
├── datasets/                # 手写测试集
│   ├── planner.json         # ~20 条，问题 → 预期 capability DAG
│   ├── rag.json             # ~30 条，问题 → 预期相关 chunk/doc
│   ├── sql.json             # ~20 条，问题 → 预期 SQL + 结果集
│   └── e2e.json             # ~20 条，问题 → 评估 rubric
└── results/                 # 归档目录（git ignore）
    └── 2026-06-21-1430/
        ├── summary.md       # 本次跑分汇总
        ├── planner.json     # 细粒度结果
        ├── rag.json
        ├── sql.json
        └── e2e.json
```

### 3.1 各文件职责

**`models.py`** — Pydantic 数据模型

```python
class TestCase(BaseModel):
    """单条测试用例的通用表示"""
    id: str
    question: str
    module: Literal["planner", "rag", "sql", "e2e"]
    expected: dict  # 模块特定的预期输出
    metadata: dict = {}  # kb_id, tags, difficulty, etc.

class EvalResult(BaseModel):
    """单条用例的评估结果"""
    case_id: str
    status: Literal["pass", "fail", "error", "skip"]
    expected: dict
    actual: dict
    metrics: dict  # 该用例的各项指标值
    duration_ms: int
    error_msg: str | None = None

class EvalReport(BaseModel):
    """一次完整评估的报告"""
    timestamp: datetime
    module: str
    summary: dict  # 汇总指标
    results: list[EvalResult]
    score: float  # 综合分 0-100
```

**`dataset.py`** — 测试集加载
- `load_dataset(module: str) -> list[TestCase]`: 从 `datasets/{module}.json` 加载并校验
- `validate_dataset(cases: list[TestCase]) -> bool`: 检查 ID 唯一性、必填字段、预期格式合法性
- `get_split(module, split="all")`: 返回"全量"/"快速冒烟"/"深度评估"子集

**`runner.py`** — 通用执行引擎
- `run_planner(cases)`: 调用 `multi_agent/planner.py` 的 `_extract_json()` + `_normalize_plan()`，不经过 LLM（用 mock 或直接调函数）或实际调 LLM 取决于 `--live` 标志
- `run_rag(cases)`: 调用 `retrieval/pipeline.py` 的检索部分（不含 LLM 生成），收集 top-K chunk
- `run_sql(cases)`: 调用 `sql_agent/sql_generator.py` 生成 SQL → sqlglot 校验 → 执行 → 比对结果
- `run_e2e(cases)`: 调 `MultiAgentSystem.ask()` 完整链路，收集 Works 调用记录 + 最终答案

**`metrics.py`** — 指标计算（纯函数，无副作用）

| 函数 | 公式 |
|------|------|
| `recall_at_k(actual, expected, k)` | `|actual[:k] ∩ expected| / |expected|` |
| `mrr(actual, expected)` | `1/|Q| * Σ 1/rank_i` |
| `ndcg_at_k(actual, expected, k)` | DCG/IDCG |
| `jaccard_similarity(set_a, set_b)` | `|A ∩ B| / |A ∪ B|` |
| `exact_match(actual, expected)` | `actual == expected` |
| `semantic_sql_match(actual_result, expected_result)` | 结果集比较（tolerance 1e-6） |

**`judge.py`** — LLM-as-Judge
- 仅在 `--judge` 标志时启用，用于 E2E 评估
- 结构化 prompt，4 维评分（完整性/忠实性/简洁性/引用质量），各 1-5
- 输出包含评分理由，便于人工抽检
- 小模型裁判可靠性警告：自动标注 `judge_confidence: low|medium|high`

**`report.py`** — 报告生成
- `print_summary(report)`: 控制台表格输出
- `write_report(report, path)`: Markdown 完整报告
- `compare_reports(report_a, report_b)`: 两次跑分的差异对比

**`cli.py`** — 命令行入口
```
python -m evaluation                      # 全量评估
python -m evaluation rag                  # 单模块
python -m evaluation --smoke              # 快速冒烟（每模块取 5 条）
python -m evaluation --judge              # 启用 LLM-as-Judge
python -m evaluation --live               # 实际跑 LLM（默认离线模式 mock）
python -m evaluation --compare latest     # 与上次跑分对比
```

## 4. 测试数据集

### 4.1 Planner 测试集 (`planner.json`) — 20 条

```json
{
  "module": "planner",
  "version": "1.0",
  "test_cases": [
    {
      "id": "P001",
      "question": "技术部有多少人？",
      "expected": {
        "capabilities": ["query_database"],
        "max_steps": 1,
        "should_not_contain": ["search_knowledge", "generate_report"]
      }
    }
  ]
}
```

指标：
- `capability_accuracy` (Jaccard) — 目标 > 85%
- `redundancy_rate` — 目标 < 10%
- `structure_accuracy` (拓扑序比较) — 目标 > 75%

### 4.2 RAG 测试集 (`rag.json`) — 30 条

```json
{
  "module": "rag",
  "version": "1.0",
  "test_cases": [
    {
      "id": "R001",
      "question": "冷藏肉类的保质期是多久？",
      "kb_id": "policy",
      "relevant_docs": ["policy/优品超市 - 《生鲜营运标准手册》V4.0（节选）.txt"],
      "relevant_snippets": ["冷藏肉类到货后48小时内必须上架"],
      "min_relevant_chunks": 1
    }
  ]
}
```

指标：
- `recall@5` — 目标 > 70%
- `recall@10` — 目标 > 85%
- `MRR` — 目标 > 0.60
- `NDCG@10` — 目标 > 0.65
- `citation_false_positive_rate` — 目标 < 15%

### 4.3 SQL 测试集 (`sql.json`) — 20 条

```json
{
  "module": "sql",
  "version": "1.0",
  "test_cases": [
    {
      "id": "S001",
      "question": "技术部有多少人？",
      "expected_sql": "SELECT COUNT(*) FROM users u JOIN departments d ON u.dept_id = d.id WHERE d.name = '技术部'",
      "expected_result": [{"count": 3}],
      "allow_equivalent": true,
      "security_checks": ["no_sensitive_columns", "read_only"]
    }
  ]
}
```

指标：
- `syntax_validity` — 目标 > 90%
- `result_equivalence` — 目标 > 80%
- `security_pass_rate` — 目标 100%（硬要求）
- `semantic_equivalence` — 目标 > 85%

### 4.4 端到端测试集 (`e2e.json`) — 20 条

```json
{
  "module": "e2e",
  "version": "1.0",
  "test_cases": [
    {
      "id": "E001",
      "question": "冷藏肉类的保质期是多久？预计技术部参与的项目总预算是多少？",
      "expected_routing": ["search_knowledge", "query_database"],
      "rubric": {
        "completeness": "必须同时给出保质期规定和预算数字",
        "faithfulness": "保质期数字必须来自生鲜手册原文，预算必须来自数据库",
        "citation": "至少引用 1 个文档出处，预算数字有明确计算过程"
      }
    }
  ]
}
```

指标：
- `routing_accuracy` — 目标 > 85%
- `llm_judge_score` (1-5) — 目标 > 3.5
- `citation_accuracy` — 目标 > 80%
- `human_review_pass_rate` — 人工抽检通过率

### 4.5 测试集分布

| 知识域 | Planner | RAG | SQL | E2E | 合计 |
|--------|---------|-----|-----|-----|------|
| 制度/生鲜手册 | 2 | 6 | - | 2 | 10 |
| 制度/保险条款 | 2 | 5 | - | 2 | 9 |
| 技术/IT 手册 | 2 | 5 | - | 2 | 9 |
| 技术/CNC 手册 | 2 | 4 | - | 2 | 8 |
| 财务/HR/销售 | 2 | 6 | - | 2 | 10 |
| 数据库查询 | 6 | - | 16 | 6 | 28 |
| 混合场景 | 4 | 4 | 4 | 4 | 16 |
| **合计** | **~20** | **~30** | **~20** | **~20** | **~90** |

## 5. 指标与评分

### 5.1 硬指标（全自动，每次跑）

| 模块 | 指标 | 目标基线 |
|------|------|---------|
| Planner | Capability Jaccard | > 85% |
| | 冗余检测率 | < 10% |
| | 结构正确率（edges） | > 75% |
| RAG | Recall@5 | > 70% |
| | Recall@10 | > 85% |
| | MRR | > 0.60 |
| | NDCG@10 | > 0.65 |
| | Citation 误杀率 | < 15% |
| SQL | 语法正确率 | > 90% |
| | 结果等价率 | > 80% |
| | 安全通过率 | 100% (硬要求) |
| | 语义等价率 | > 85% |
| E2E | 路由正确率 | > 85% |
| | 引用准确率 | > 80% |

### 5.2 软指标（LLM-as-Judge，`--judge` 标志启用）

端到端答案结构化评分：

```
评估维度（各 1-5 分）:
1. 完整性 — 是否回答了问题的所有部分？
2. 忠实性 — 数字和事实是否能追溯到数据源？
3. 简洁性 — 有没有冗余或无关内容？
4. 引用质量 — 引用标注是否准确、充分？

综合分 = 完整性×0.35 + 忠实性×0.30 + 简洁性×0.15 + 引用质量×0.20
```

小模型裁判可靠性警告：自动标注 `judge_confidence: low|medium|high`。

### 5.3 综合评分

```
系统总分 = Planner×0.15 + RAG×0.30 + SQL×0.25 + E2E×0.30
```

权重设计：RAG 和 E2E 最高——RAG 是核心管线，E2E 最贴近用户体验。

### 5.4 趋势追踪

每次跑生成一条报告行：

```
2026-06-21  Planner:88%  RAG:72%  SQL:91%  E2E:4.2  Total:82%
2026-06-28  Planner:90%  RAG:75%  SQL:92%  E2E:4.4  Total:84%  ↑+2%
```

## 6. CLI 接口设计

```
usage: python -m evaluation [MODULE] [OPTIONS]

Modules:
  all           全量评估（默认）
  planner       Planner 规划评估
  rag           RAG 检索评估
  sql           SQL 生成评估
  e2e           端到端评估

Options:
  --smoke       快速冒烟（每模块取 5 条），默认 false
  --live        启用真实 LLM 调用（推荐用于跑分基线），默认 false
  --judge       启用 LLM-as-Judge 评分 E2E 答案（隐含 --live）
  --compare ID  与指定历史跑分对比，ID 可以是 "latest"
  --output DIR  输出目录，默认 evaluation/results/<timestamp>/
  --verbose     输出每条用例的详细结果

Examples:
  python -m evaluation --smoke                  # 离线快速冒烟，验证框架
  python -m evaluation --live                   # 全量真实评估（跑基线）
  python -m evaluation rag --live               # RAG 单模块真实评估
  python -m evaluation --live --judge           # 全量 + LLM评分
  python -m evaluation --live --compare latest  # 真实评估 + 上次对比
```

## 7. 与现有代码的集成

### 7.1 不修改现有代码

评估框架作为**外挂模块**，通过公开 API 调用现有子系统：

| 评估目标 | 调用方式 |
|----------|---------|
| Planner | `MultiAgentSystem` 的 Planner 节点（含 LLM 调用） |
| RAG 检索 | `retrieval/chain.py` 的检索部分（向量+BM25+Reranker，不含 LLM 生成） |
| SQL 生成 | `SQLAgent.generate_sql()` → sqlglot 校验 → 执行 → 结果比对 |
| E2E | `MultiAgentSystem.ask()` 完整链路 |

### 7.2 两种运行模式

**Live 模式（`--live`，推荐用于真实评估）**

完整调用 LLM，获取真实性能数据。Planner、SQL Generator、Reporter 均通过 Ollama 推理。这是跑基线、测优化效果的模式。

**Offline 模式（默认，`--no-live`，用于快速验证框架）**

各模块行为：
- **RAG**：正常跑，检索管线不依赖 LLM，offline/live 结果一致
- **SQL**：跑语法校验 + 安全校验部分（不需要 LLM 的环节）；语义正确性评估跳过
- **Planner**：跳过（依赖 LLM 拆解任务）
- **E2E**：跳过（依赖完整 Agent 链路）

Offline 模式用于：首次搭建时验证评估框架自身逻辑、测试集 schema 校验、指标计算正确性。**真实跑分必须用 `--live`。**

### 7.3 SQL 语义等价的判定标准

```python
def is_semantically_equivalent(actual_sql, expected_sql, db_session):
    """
    判定两条 SQL 是否语义等价：
    1. 两条 SQL 在 demo 数据库中的执行结果一致（列名 + 行数 + 值 tolerance 1e-6）
    2. 安全校验路径一致（敏感列未泄露、只读限制未绕过）
    满足二者之一即判定等价
    """
```

### 7.3 依赖关系

```
evaluation/
    ├── 依赖 multi_agent.planner (Planner 评估)
    ├── 依赖 retrieval.pipeline (RAG 评估)
    ├── 依赖 sql_agent.sql_generator (SQL 评估)
    ├── 依赖 multi_agent.graph (E2E 评估)
    └── 依赖 llm.llm_factory (--live 模式)
```

## 8. 现有可复用资产

| 来源 | 可复用内容 |
|------|-----------|
| `tests/test_planner.py` | 大量问题示例用于 Planner 测试集 |
| `sql_agent/demo_sql_agent.py` | 8 个 SQL 测试用例可转化 |
| `multi_agent/demo.py` | 4 个 Multi-Agent 场景 |
| `retrieval/evaluation/benchmark_dataset.json` | 骨架格式参考 |
| `retrieval/evaluation/retrieval_eval.py` | Recall/MRR/NDCG 计算逻辑可参考 |
| `tests/` (17 文件) | 各类内联查询示例 |

## 9. 非目标（YAGNI）

以下不在本次范围内：
- ❌ 持续集成自动触发（GitHub Actions 等）
- ❌ 评估数据集的训练/测试分离
- ❌ 多模型对比评估矩阵
- ❌ 前端可视化 Dashboard
- ❌ 性能/延迟基准测试

## 10. 成功标准

1. **可运行**: `python -m evaluation` 跑完 4 个模块，输出报告
2. **有基线**: 首次跑分数字成为 baseline，写入 `results/baseline.md`
3. **可对比**: `--compare latest` 能计算每个指标的 Δ
4. **有数据**: 90 条测试用例标注完成，覆盖所有知识域
5. **面试可展示**: 报告清晰展示系统各模块的量化能力
