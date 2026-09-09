# RAG Evaluation Current State

> 生成时间: 2026-09-04
> 基于代码库实际分析，非理论推测

---

## 1. 当前评测体系概览

本项目是一套 **跨境电商 RAG + Multi-Agent 平台**（LangGraph + MCP），评测体系完全自建，采用注册制（Registry）五模块架构：

| 模块 | Runner | 是否需要 LLM | 数据集 | 案例数 |
|------|--------|-------------|--------|--------|
| `rag` | `_run_rag` | 否（离线检索） | `rag_test_kb.json` v1.3 | 59 |
| `planner` | `_run_planner` | 是（离线用启发式） | `planner.json` v1.0 | 20 |
| `sql` | `_run_sql` | 是 | `sql.json` v1.0 | 20 |
| `e2e` | `_run_e2e` | 是（+Judge） | `e2e.json` v1.0 | 20 |
| `cs` | `_run_cs` | 否 | `cs.json` v1.0 | 20 |

**核心框架位置**: `backend/evaluation/`
**核心数据集位置**: `backend/evaluation/datasets/`
**基线存储**: `data/baselines/`
**运行记录**: `data/eval_runs/`
**CI 工作流**: `.github/workflows/rag_eval.yml`

---

## 2. 当前所有评测集

### 2.1 RAG 检索评测集 — `rag_test_kb.json` (v1.3, 活跃)

- **文件**: `backend/evaluation/datasets/rag_test_kb.json`
- **版本**: 1.3
- **案例数**: 59 条（RT-001~RT-054 正例+负例 + DEPT-001~DEPT-005 部门隔离）
- **状态**: 活跃，CI 使用

**构成**:
- RT-001~RT-042: 正例（42 条），覆盖 FAQ/售后/库存/采购/规格/合规/物流/质检/合同/技术手册等领域
- RT-043~RT-050: 通用负例（8 条），`should_reject=true`
- RT-051~RT-054: Hard negative 近似负例（4 条），`should_reject=true`
- DEPT-001~DEPT-005: 部门隔离测试（5 条），kb_id=`policy_general`

### 2.2 RAG 评测集 v1.4 — `rag_test_kb_v1.4.json` (实验性)

- **文件**: `backend/evaluation/datasets/rag_test_kb_v1.4.json`
- **版本**: 1.4
- **案例数**: 58 条（54 RT + RT-CHUNK-001~004 真实 chunk_id 案例）
- **状态**: 已生成但未 promote 为基线，DEPT 案例被移除

### 2.3 Planner 评测集 — `planner.json` (v1.0)

- **文件**: `backend/evaluation/datasets/planner.json`
- **案例数**: 20 条（P001~P020）
- **状态**: 活跃

### 2.4 SQL 评测集 — `sql.json` (v1.0)

- **文件**: `backend/evaluation/datasets/sql.json`
- **案例数**: 20 条（S001~S020，含 S014~S017 安全测试）
- **状态**: 活跃

### 2.5 E2E 评测集 — `e2e.json` (v1.0)

- **文件**: `backend/evaluation/datasets/e2e.json`
- **案例数**: 20 条（E001~E020）
- **状态**: 活跃

### 2.6 客服路由评测集 — `cs.json` (v1.0)

- **文件**: `backend/evaluation/datasets/cs.json`
- **案例数**: 20 条（CS001~CS020）
- **状态**: 活跃

### 2.7 已弃用数据集

| 文件 | 弃用原因 |
|------|----------|
| `rag.v1.deprecated.json` (30 条) | doc_id 协议 `sha256[:16]` 与实际 `md5[:10]` 不匹配，召回永远为 0 |
| `rag_v2.deprecated.json` (15 条) | KB 文档已不在库中；混用 doc_id 协议；字段名 `expect_reject` vs `should_reject` 不一致 |

---

## 3. Dataset Schema

### 3.1 RAG 评测集 Schema (v1.3)

```json
{
  "version": "1.3",
  "_comment": "...",
  "test_cases": [
    {
      "id": "RT-001",
      "question": "下单后可以修改收货地址吗？",
      "module": "rag",
      "kb_id": "rag_test_kb",
      "expected": {
        "relevant_docs": ["3e30e0df16"],
        "relevant_snippets": ["修改地址", "待发货"],
        "min_relevant_chunks": 1,
        "should_reject": false,
        "match_type": "snippet",
        "probe_type": "domain_recall",
        "allowed_departments": ["hr"],
        "forbidden_departments": ["finance"]
      },
      "metadata": {
        "difficulty": "easy",
        "domain": "order",
        "fix_note": "...",
        "probe_note": "..."
      }
    }
  ]
}
```

**字段说明**:
- `id`: 唯一标识
- `question`: 用户问题
- `module`: 固定 `"rag"`
- `kb_id`: 知识库 ID（`rag_test_kb` 或 `policy_general`）
- `expected.relevant_docs`: 期望召回的 doc_id 列表（`md5(basename)[:10]`）
- `expected.relevant_snippets`: 期望在召回内容中出现的关键词
- `expected.min_relevant_chunks`: 最少召回 chunk 数
- `expected.should_reject`: 是否应拒答（负例为 `true`）
- `expected.match_type`: 匹配方式（`"snippet"` 表示关键词匹配即可，不要求 doc_id 精确）
- `expected.probe_type`: 探测类型分类标签
- `expected.allowed_departments` / `forbidden_departments`: 部门隔离约束
- `metadata.difficulty`: easy / medium / hard
- `metadata.domain`: 业务领域
- `metadata.fix_note`: ground truth 修复审计记录

### 3.2 Planner 评测集 Schema

```json
{
  "id": "P001",
  "question": "技术部有多少人？",
  "module": "planner",
  "expected": {
    "capabilities": ["query_database"],
    "max_steps": 1,
    "should_not_contain": ["search_knowledge", "generate_report"]
  },
  "metadata": {"difficulty": "easy", "type": "single_sql"}
}
```

### 3.3 SQL 评测集 Schema

```json
{
  "id": "S001",
  "question": "技术部有多少人？",
  "expected_sql": "SELECT COUNT(*) as count FROM users u JOIN departments d ...",
  "expected_result": [{"count": 3}],
  "output_columns": ["count"],
  "allow_equivalent": true,
  "security_checks": ["no_sensitive_columns", "read_only"]
}
```

### 3.4 E2E 评测集 Schema

```json
{
  "id": "E001",
  "question": "冷藏肉类的保质期是多久？",
  "expected_routing": ["search_knowledge"],
  "rubric": {
    "completeness": "必须给出48小时的具体规定",
    "faithfulness": "数字48小时必须来自生鲜手册原文",
    "citation": "必须引用生鲜营运标准手册"
  },
  "metadata": {"difficulty": "easy", "type": "single_rag"}
}
```

### 3.5 CS 评测集 Schema

```json
{
  "id": "CS001",
  "question": "你们的退货政策是什么？",
  "module": "cs",
  "expected": {
    "cs_route": "knowledge_query",
    "intent": "k_faq",
    "output_guard": {"should_filter": false, "forbidden_patterns": []},
    "audit_required": false,
    "expected_behavior": "路由到知识问答，返回退货政策相关内容"
  },
  "metadata": {"difficulty": "easy", "type": "knowledge_qa"}
}
```

---

## 4. 真实数据样例

### 4.1 RAG 正例 — RT-001 (简单 FAQ)

```json
{
  "id": "RT-001",
  "question": "下单后可以修改收货地址吗？",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {
    "relevant_docs": ["3e30e0df16"],
    "relevant_snippets": ["修改地址", "待发货"],
    "min_relevant_chunks": 1
  },
  "metadata": {
    "difficulty": "easy",
    "domain": "order",
    "fix_note": "2026-08-21: snippet 对齐文档原文（01_FAQ '订单可以修改地址吗'/'地址写错了能改吗'，无'收货地址'措辞）"
  }
}
```

### 4.2 RAG 负例 — RT-051 (Hard Negative)

```json
{
  "id": "RT-051",
  "question": "笔记本电脑的保修期是多久？",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {
    "relevant_docs": [],
    "relevant_snippets": [],
    "min_relevant_chunks": 0,
    "should_reject": true
  },
  "metadata": {
    "difficulty": "hard",
    "domain": "negative",
    "probe_type": "hard_negative_near_miss",
    "probe_note": "近似负样本：KB 含保修内容（01_FAQ 电子产品保修/02_售后FAQ）但仅限电水壶与泛称电子产品，无笔记本电脑具体政策；期望系统不被『保修』关键词带偏而硬答"
  }
}
```

### 4.3 RAG 对抗样例 — RT-040 (同形异义词)

```json
{
  "id": "RT-040",
  "question": "Apple Watch 包装盒有什么标签要求？",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {
    "relevant_docs": ["7fae54d9ff"],
    "relevant_snippets": ["序列号", "IP6X"],
    "min_relevant_chunks": 1
  },
  "metadata": {
    "difficulty": "medium",
    "domain": "product_spec",
    "probe_type": "word_disambiguation",
    "probe_note": "25_同形异义词.md 第一篇(电子产品 Apple)，期望召回电子产品章节而非水果章节"
  }
}
```

### 4.4 RAG 部门隔离 — DEPT-003

```json
{
  "id": "DEPT-003",
  "question": "公司报销标准是多少？发票有什么要求？",
  "module": "rag",
  "expected": {
    "relevant_docs": [],
    "allowed_departments": ["hr"],
    "forbidden_departments": ["finance"],
    "min_relevant_chunks": 0
  },
  "metadata": {
    "kb_id": "policy_general",
    "department": "hr",
    "difficulty": "hard",
    "domain": "hr",
    "isolation_test": true,
    "fix_note": "跨部门泄漏守卫：hr 视角问 finance 话题，检索不得泄漏 finance 文档"
  }
}
```

### 4.5 E2E 多跳混合 — E003

```json
{
  "id": "E003",
  "question": "叶菜类蔬菜当天没卖完怎么处理？预计产品部有多少员工？",
  "expected_routing": ["search_knowledge", "query_database"],
  "rubric": {
    "completeness": "必须同时包含叶菜处理流程（22:00前报损）和产品部人数",
    "faithfulness": "叶菜规定必须来自生鲜手册，人数必须来自数据库",
    "citation": "叶菜部分需引用生鲜手册"
  },
  "metadata": {"difficulty": "medium", "type": "mixed_sql_rag"}
}
```

---

## 5. Evaluation 执行链路

### 5.1 RAG 离线检索评测链路（核心链路）

```
backend/evaluation/datasets/rag_test_kb.json
    ↓ dataset.py:load_dataset()
backend/evaluation/cli.py:main()
    ↓ runner.py:run_module("rag")
backend/evaluation/runners/builtin.py:_run_rag()
    ↓
    ├── ChromaDB Vector Search (bge-small-zh embedding)
    ├── BM25 Store (bm25_store.py)
    ├── RRF 融合 (rrf_k=60, hybrid.py)
    ├── 同义词扩展 (rank_bonus 1.0/0.7)
    ├── ChunkLevelRetriever (retrievers.py: 两阶段 doc_db→chunk)
    ├── AdaptiveRetriever (聚类→父文档扩展)
    ├── CrossEncoder Rerank (bge-reranker-base / DashScope)
    └── RerankCompressor (ContextualCompressionRetriever)
    ↓
    ↓ 指标计算:
    ├── recall_at_k (recall@5, recall@10)
    ├── mrr
    ├── ndcg_at_k (ndcg@10)
    ├── chunk_recall_at_k
    ├── top1_accuracy
    ├── reject_accuracy (负例)
    ├── dept_leak (部门隔离)
    └── 置信度启发式 (EvidenceGate 离线近似)
    ↓
backend/evaluation/report.py → MD + HTML + JSON 报告
backend/evaluation/storage.py → data/eval_runs/{run_id}/
```

**关键文件**:
- `backend/evaluation/runners/builtin.py:_run_rag()` — RAG runner 实现
- `backend/rag/retrieval/hybrid.py` — Vector + BM25 + RRF
- `backend/rag/retrieval/retrievers.py` — ChunkLevelRetriever, AdaptiveRetriever
- `backend/rag/reranker.py` — RerankCompressor
- `backend/evaluation/metrics.py` — 指标纯函数

### 5.2 E2E 评测链路

```
backend/evaluation/datasets/e2e.json
    ↓
backend/evaluation/runners/builtin.py:_run_e2e()
    ↓
    └── MultiAgentSystem.ask() (完整 Agent 链路)
        ├── 路由推断 (_infer_routing_from_answer)
        └── LLM-as-Judge (judge.py)
            ├── completeness × 0.35
            ├── faithfulness × 0.30
            ├── conciseness × 0.15
            └── citation_quality × 0.20
    ↓
    pass = routing_ok AND judge_total >= 3.0
```

### 5.3 SQL 评测链路

```
backend/evaluation/datasets/sql.json
    ↓
backend/evaluation/runners/builtin.py:_run_sql()
    ↓
    └── SQLAgent.ask()
        ├── syntax_valid
        ├── result_set_match (tolerance 1e-6)
        └── security_pass
```

### 5.4 CS 评测链路

```
backend/evaluation/datasets/cs.json
    ↓
backend/evaluation/runners/builtin.py:_run_cs()
    ↓
    └── CSRouter.route()
        ├── routing_accuracy
        ├── intent_accuracy
        └── audit_coverage
```

### 5.5 Planner 评测链路

```
backend/evaluation/datasets/planner.json
    ↓
backend/evaluation/runners/builtin.py:_run_planner()
    ↓
    └── planner_node(state) (live) / evaluate_planner_offline (离线)
        ├── jaccard_similarity (capabilities)
        ├── redundancy check
        └── edges 结构检查
```

---

## 6. 当前指标

### 6.1 离线评测指标 (`backend/evaluation/metrics.py`)

| 指标 | 存在 | 计算位置 | 用于 |
|------|------|----------|------|
| Recall@K (recall@5, recall@10) | ✅ | `metrics.py:recall_at_k()` | RAG Retrieval |
| MRR | ✅ | `metrics.py:mrr()` | RAG Retrieval |
| NDCG@K (ndcg@10) | ✅ | `metrics.py:ndcg_at_k()` | RAG Retrieval |
| Chunk Recall@K | ✅ | `metrics.py:chunk_recall_at_k()` | RAG Retrieval |
| Top-1 Accuracy | ✅ | `runners/builtin.py` | RAG Retrieval (核心真实指标) |
| Reject Accuracy | ✅ | `metrics.py:reject_accuracy()` | RAG 拒答安全 |
| Dept Leak | ✅ | `runners/builtin.py` | RAG 部门隔离 |
| Jaccard Similarity | ✅ | `metrics.py:jaccard_similarity()` | Planner |
| Exact Match | ✅ | `metrics.py:exact_match()` | 通用 |
| Result Set Match | ✅ | `metrics.py:result_set_match()` | SQL |
| P95 Latency | ✅ | `metrics.py:p95_latency()` | 性能 |
| Stability Variance | ✅ | `metrics.py:stability_variance()` | 稳定性 |
| Precision@K | ❌ | 不存在 | — |
| Faithfulness (离线) | ❌ | 不存在 | — |
| Citation (离线) | ❌ | 不存在 | — |

### 6.2 LLM-as-Judge 指标 (`backend/evaluation/judge.py`)

| 指标 | 权重 | 分值 | 用于 |
|------|------|------|------|
| Completeness | 0.35 | 1-5 | E2E |
| Faithfulness | 0.30 | 1-5 | E2E |
| Conciseness | 0.15 | 1-5 | E2E |
| Citation Quality | 0.20 | 1-5 | E2E |
| Total (加权) | — | 1.0-5.0 | E2E pass/fail |

### 6.3 运行时指标 (`backend/rag/metrics.py`)

| 指标 | 说明 |
|------|------|
| avg/p50/p95/p99_total_ms | 端到端延迟 |
| avg_retrieval_ms | 检索延迟 |
| avg_rerank_ms | 重排延迟 |
| avg_llm_ms | LLM 生成延迟 |
| avg_recalled / avg_final | 召回/最终文档数 |
| filter_usage_rate | 过滤器使用率 |

### 6.4 不存在的指标

- **Precision@K**: 不存在。`multi_path_retrieval.py` 中有 `sparse_precision=0.0` 占位符但从未计算。
- **离线 Faithfulness**: 不存在。仅在 E2E Judge 和 `guardrails/scorer.py`（运行时）中存在。
- **离线 Citation**: 不存在。仅在 E2E Judge 中存在。
- **离线 Answer Correctness**: 不存在。

---

## 7. 最近一次评测结果

**运行时间**: 2026-08-27T22:57:44
**数据集**: rag_test_kb.json v1.3 (59 条，含 DEPT)
**模式**: 离线
**Git SHA**: c275410, branch: master

### 核心指标

| 指标 | 数值 | 状态 |
|------|------|------|
| 总数 | 59 | — |
| 通过 | 51 | — |
| 失败 | 8 | — |
| 错误 | 0 | — |
| **通过率** | **86.4%** | ⚠️ |
| **Top-1 准确率** | **57.6%** | ❌ |
| **拒答准确率** | **100.0%** | ✅ |
| MRR | 83.9% | ✅ |
| NDCG@10 | 83.9% | ✅ |
| Recall@5 | 85.6% | ⚠️ |
| Recall@10 | 85.6% | — |
| Chunk Recall | 62.7% | — |
| Dept Leak | 0.0% | ✅ |

### 基线对比

| 指标 | Baseline v1.3 (54例) | 最新 Run (59例) | 变化 |
|------|---------------------|----------------|------|
| pass_rate | 94.4% | 86.4% | -8.0% ↓ |
| top1_accuracy | 57.4% | 57.6% | +0.2% |
| reject_accuracy | 100% | 100% | = |
| mrr | 88.9% | 83.9% | -5.0% ↓ |
| ndcg@10 | 90.3% | 83.9% | -6.4% ↓ |
| recall@5 | 94.4% | 85.6% | -8.8% ↓ |
| chunk_recall | 72.2% | 62.7% | -9.5% ↓ |

> 注：最新 run 包含额外 5 条 DEPT 案例，拉低了整体均值。

---

## 8. 失败 Case 分析

**全部 8 个失败案例均为 "Top-1 错"** — 即 recall 通过（期望 doc 在 Top-5/10 中），但排名第一的不是期望文档。

| Case ID | Question | 期望 doc | 实际 Top-1 doc | 失败原因 |
|---------|----------|----------|----------------|----------|
| RT-003 | 平台支持哪些支付方式？ | `3e30e0df16` (FAQ) | `894418906b` (采购流程) | 同主题多文档混淆 |
| RT-004 | 签收后发现商品破损怎么办？ | `788552468f` (退货政策) | `f938bc1033` (退货流程) | 标题关键词干扰 |
| RT-006 | 如何申请商品保修？ | `13b619e25a` (售后FAQ) | `f938bc1033` (退货政策) | 同主题文档竞争 |
| RT-012 | 到货验货的流程是什么？ | `894418906b` (采购流程) | `b1ec771661` (质检SOP) | 流程类文档语义接近 |
| RT-015 | 跨境商品需要满足哪些合规认证？ | `7e28946475` (商品规格) | `fd2910f986` (合规认证) | 合规主题多文档 |
| RT-027 | 跨境电商退货的违约金是多少？ | `276a3f4841` (技术手册) | `04c6e90faa` (其他) | 跨领域问题混淆 |
| RT-029 | 供应商交期延迟怎么处理？ | `894418906b` (采购流程) | `99a80089e4` (库存管理) | 供应链主题混淆 |
| RT-035 | 盘点差异率超过多少需要追责？ | `99a80089e4` (库存管理) | `14aa8a1c7a` (采购合同) | 制度类文档语义接近 |

**根因分析**（来自 `docs/rag_eval/HANDOFF.md` 和 `v2-evaluation-design.md`）:
- BGE-reranker-base 模型无法有效区分同主题文档
- 召回扩展有效（+2%），但调权重/阈值/bonus 均失败（7 个失败实验）
- 瓶颈在 Reranker 模型能力上限，预估天花板 ~60-65% Top-1
- 生产门禁要求 Top-1 >= 85%，当前差距显著

---

## 9. RAG 能力覆盖矩阵

| RAG 能力 | 当前是否有评测 | 使用哪个 Dataset | 覆盖多少 Case | 说明 |
|----------|--------------|-----------------|-------------|------|
| Vector Retrieval | ✅ 间接 | rag_test_kb.json | 42 正例 | 作为 Hybrid 的一部分被测试，无法单独分离 |
| BM25 | ✅ 间接 | rag_test_kb.json | 42 正例 | 同上，作为 Hybrid 的一部分 |
| Hybrid (Vector+BM25+RRF) | ✅ | rag_test_kb.json | 42 正例 | 整体链路测试 |
| RRF | ✅ 间接 | rag_test_kb.json | 42 正例 | 融合在 Hybrid 中 |
| Reranker | ✅ 间接 | rag_test_kb.json | 42 正例 | Top-1 指标反映 reranker 效果 |
| MultiQuery | ❌ | — | 0 | 评测链路不经过 MultiQuery（离线 runner 无 LLM） |
| Adaptive Retrieval | ✅ 间接 | rag_test_kb.json | 42 正例 | Span 中有 Adaptive 决策记录 |
| Chunking | ⚠️ 极弱 | rag_test_kb_v1.4.json | 4 (RT-CHUNK) | v1.4 仅 4 条 chunk 级案例，未 merge |
| PDF | ✅ 间接 | rag_test_kb.json | ~15 | KB 含 5 个 PDF 文档，但无专门 PDF 质量评测 |
| DOCX | ✅ 间接 | rag_test_kb.json | ~3 | KB 含 3 个 DOCX 文档 |
| Markdown | ✅ 间接 | rag_test_kb.json | ~5 | KB 含 5 个 MD 文档 |
| TXT | ❌ | — | 0 | KB 中无 TXT 文档 |
| 表格 | ❌ | — | 0 | 无专门表格检索评测（KB 文档含表格但无对应评测案例） |
| 图片 | ❌ | — | 0 | 无图片评测 |
| Citation | ⚠️ 仅 E2E | e2e.json | 20 | 通过 LLM Judge 的 citation_quality 维度 |
| Faithfulness | ⚠️ 仅 E2E | e2e.json | 20 | 通过 LLM Judge 的 faithfulness 维度 |
| Negative Query / Refusal | ✅ | rag_test_kb.json | 12 (RT-043~054) | 8 通用负例 + 4 hard negative |
| 部门隔离 | ✅ | rag_test_kb.json | 5 (DEPT) | allowed/forbidden departments |
| 同形异义词消歧 | ✅ | rag_test_kb.json | 2 (RT-040, RT-041) | Apple 电子 vs 水果 |
| 数字近似敏感 | ✅ | rag_test_kb.json | 1 (RT-042) | 8 / 8.00 / ¥8 |
| 标题干扰 | ✅ | rag_test_kb.json | 1 (RT-039) | 标题含关键词但内容无关 |
| 极短文档 | ✅ | rag_test_kb.json | 1 (RT-038) | <50 字文档 |
| 跨文档多跳 | ❌ | — | 0 | 无需要综合多个文档回答的案例 |
| 长文档 | ❌ | — | 0 | 无专门的长文档评测 |

---

## 10. 当前评测集存在的问题

### 10.1 数据问题

1. **数据量少**: RAG 核心仅 42 正例 + 12 负例 + 5 部门隔离 = 59 条。v2 设计文档建议 50+ 条，当前勉强达标但统计意义弱。

2. **无跨文档多跳案例**: 所有正例均指向单一 `relevant_docs`，无需要综合 2+ 文档回答的问题。

3. **无 expected_answer**: `expected_answer` 字段在 schema 中预留但所有案例均为空，无法评测生成质量。

4. **relevant_chunks 大部分为空**: 仅 v1.4 的 4 条 RT-CHUNK 案例有真实 chunk_id，v1.3 的 `relevant_chunks` 字段全为 `[]`。

5. **Ground Truth 可信度**: v1.3 经过 3 轮修复（v1.0→v1.1→v1.2→v1.3），每条 `fix_note` 记录了对齐文档原文的过程。但 doc_id 协议 `md5(basename)[:10]` 意味着改文件名即破坏所有 ground truth。

6. **无 source document 关联**: 评测集不记录 source document 路径，仅记录 doc_id hash，不可逆向追溯。

### 10.2 覆盖问题

1. **只覆盖单文档问答**: 42 个正例全部指向单一文档，无多文档综合问题。

2. **无复杂推理问题**: 所有正例为事实检索型（"XX是什么"、"XX流程怎样"），无需要推理、比较、总结的问题。

3. **表格评测缺失**: KB 文档含表格（商品规格、库存状态等），但无专门表格检索评测案例。

4. **长文档缺失**: 仅 RT-038 涉及极短文档（<50字），无长文档（>10 页）评测。

5. **无相似干扰文档系统测试**: 仅 `写作规范反例/` 子目录的 5 个对抗文档被 4 条案例覆盖（RT-038~042），覆盖面窄。

### 10.3 RAG 组件评测问题

**无法单独证明各组件效果**:

| 组件 | 能否证明 | 原因 |
|------|---------|------|
| BM25 有效果 | ❌ | 无 BM25-only vs Hybrid 的对比评测 |
| Vector 有效果 | ❌ | 无 Vector-only vs Hybrid 的对比评测 |
| Hybrid 有效果 | ❌ | 无 Hybrid vs 单通道的 A/B 评测 |
| RRF 有效果 | ❌ | 无 RRF vs 其他融合策略的对比 |
| Reranker 有效果 | ⚠️ 间接 | Top-1 57.6% 反映 reranker 效果但无 rerank 前后对比 |
| MultiQuery 有效果 | ❌ | 离线评测链路完全不经过 MultiQuery |
| Adaptive Retrieval 有效果 | ❌ | Span 中有决策记录但无对比评测 |

**根本原因**: 评测 runner 跑的是完整 pipeline，没有分阶段独立评测。`docs/rag_eval/pipeline_eval.md` 设计了分阶段评测（ChunkHybridEvaluator / RerankEvaluator / LLMGenerateEvaluator），但 **该模块 `backend/rag_eval/` 从未实现**。

### 10.4 评测链路问题

1. **离线 runner 不经过 MultiQuery / LLM Generate**: RAG runner 是纯检索评测，不涉及 LLM 生成，无法评测端到端质量。

2. **离线 runner 的 EvidenceGate 是启发式近似**: 真实的 EvidenceGate 仅在 `chain.py` 端到端链路生效，离线 runner 用 rerank_score 阈值 + 实体检查近似。

3. **E2E 评测需要真实 LLM**: E2E runner `needs_live=True`，CI 中 PR 不跑 Judge（缺 ANTHROPIC_API_KEY），仅 master/dispatch 跑。

---

## 11. CI/CD 集成情况

### 11.1 GitHub Actions 工作流

**文件**: `.github/workflows/rag_eval.yml`

| 问题 | 回答 |
|------|------|
| 1. 哪些 RAG Evaluation 进入 CI？ | 离线 RAG 检索评测 + E2E 评测 |
| 2. PR 运行哪些？ | 数据集校验闸门 → 离线 RAG 评测 → E2E（无 Judge） |
| 3. master push 运行哪些？ | 离线 RAG 评测 → E2E（含 Judge，需 ANTHROPIC_API_KEY） |
| 4. 是否存在 Regression Test？ | ✅ `python -m evaluation.baseline` 对比基线 |
| 5. 是否存在 Threshold？ | ✅ `--threshold 0.05`（pass_rate 跌 >5% 为 error，指标跌 >10% 为 error） |
| 6. 是否保存 Evaluation Report？ | ✅ artifact 上传 30 天 + `data/eval_runs/` 持久化 |
| 7. 是否存在 Baseline？ | ✅ `data/baselines/baseline_rag_1.3.json` |
| 8. 指标下降 CI 会不会失败？ | ✅ 会。pass_rate 跌幅 >5% 或关键指标跌幅 >10% 时 CI 失败 |
| 9. CI 是否调用真实 LLM？ | RAG 评测不调用（DEEPSEEK_API_KEY 置空）；E2E 调用（master/dispatch 用 ANTHROPIC_API_KEY） |
| 10. CI 是否下载模型？ | 不下载。`HF_HUB_OFFLINE=1`，使用缓存的 bge-small-zh + bge-reranker-base |
| 11. 运行时间/成本问题？ | 30 分钟超时；RAG 评测首案例 ~44s（DashScope rerank 冷启动），后续 ~0.5s/case |

### 11.2 其他 CI 工作流

- `unit-tests.yml`: 全量 pytest，55% 覆盖率门禁，每次 push/PR 运行
- `tool_quality.yml`: 工具注册质量检查，10 个必需工具验证

### 11.3 数据集校验闸门

**文件**: `backend/scripts/validate_eval_dataset.py`

CI 第一步执行，校验项：
- ID 唯一性
- 问题无重复
- doc_id 协议匹配真实 KB 文件（ERROR 级）
- snippet 关键词存在于 KB 文档（ERROR/WARN 级）
- 负例一致性（should_reject=true 时 relevant_docs 必须为空）

---

## 12. Baseline / Regression 情况

### 12.1 已有基线

| 基线文件 | 数据集版本 | Promote 时间 | pass_rate | top1_accuracy |
|----------|-----------|-------------|-----------|---------------|
| `baseline_rag_1.0.json` | v1.0 | 2026-08-15 | 100% (37/37) | — (无此指标) |
| `baseline_rag_1.3.json` | v1.3 | 2026-08-21 | 94.4% (51/54) | 57.4% |

### 12.2 回归检测机制

- CI 通过 `python -m evaluation.baseline <run_id> --threshold 0.05` 执行
- 按 dataset version 自动匹配基线文件
- pass_rate 跌幅 > 5% → exit 2（CI 失败）
- 关键指标跌幅 > 5% → warning；> 10% → exit 2（CI 失败）
- `promote()` 写入新基线并打 git tag `eval-baseline-{module}-{version}-{date}`

### 12.3 运行历史

`data/eval_runs/` 保存 3 次运行记录（均 2026-08-27），含完整 per-case JSON 和 meta.json（git_sha, branch, dataset_version, prompt_versions, env）。

`data/eval_history.db` 为旧框架遗留 SQLite（1 行 golden_v1 记录），**无任何代码引用**。

---

## 13. 已使用的开源 Benchmark

### 搜索结果

| Benchmark | 是否使用 | 说明 |
|-----------|---------|------|
| CRUD-RAG | ❌ 未发现 | 项目中无任何引用 |
| BEIR | ❌ 未发现 | 项目中无任何引用 |
| RAGTruth | ❌ 未发现 | 项目中无任何引用 |
| RAGBench | ❌ 未发现 | 项目中无任何引用 |
| RAGAS | ❌ 未使用 | 仅在文档中被引用为设计参考（`docs/rag_eval/v2-evaluation-design.md` Appendix C），代码中无依赖 |
| DeepEval | ❌ 未发现 | 项目中无任何引用 |
| TruLens | ❌ 未发现 | 项目中无任何引用 |

**结论**: 当前项目 **未使用任何开源评测集**。所有评测数据均为自建，基于虚构的跨境电商知识库（`rag_test_kb`）。

文档中引用的设计影响：LangChain Evaluation、LangGraph CRAG、RAGFlow EvidenceGate（仅概念对齐，非数据/代码集成）。

---

## 14. 当前评测体系总结

### 评测体系成熟度

| 维度 | 评分 | 说明 |
|------|------|------|
| 框架完整度 | ★★★★☆ | 注册制 5 模块、CLI、CI、基线、报告、持久化全具备 |
| 数据集质量 | ★★★☆☆ | v1.3 经 3 轮修复，ground truth 对齐文档原文；但量小、覆盖面窄 |
| 指标体系 | ★★☆☆☆ | 检索指标齐全，生成/faithfulness/citation 仅 E2E Judge 覆盖 |
| 组件隔离评测 | ★☆☆☆☆ | 无分阶段评测，无法证明各组件独立效果 |
| 开源基准对齐 | ☆☆☆☆☆ | 完全自建，无外部 benchmark |
| CI 集成 | ★★★★☆ | 数据集校验→离线评测→回归检查→PR 评论，链路完整 |
| 可复现性 | ★★★★☆ | 离线模式确定性（LLM key 置空、HF 离线、模型缓存） |

### 关键数字

- **总评测案例**: 139 条（5 模块：59+20+20+20+20）
- **RAG 核心**: 59 条（42 正例 + 12 负例 + 5 部门隔离）
- **最新 pass_rate**: 86.4%（51/59）
- **最新 Top-1**: 57.6%（生产门禁 85%，差距 27.4%）
- **拒答准确率**: 100%（12/12）
- **已知失败实验**: 7 个（调权重/阈值/bonus 均无效）
- **已知成功实验**: 1 个（同义词扩展 +2%）

---

## 15. 待进一步讨论的问题

1. **Top-1 准确率 57.6% vs 生产门禁 85%**: 项目自身文档已明确这是最大瓶颈，归因于 BGE-reranker-base 模型能力上限。是否需要升级 Reranker 模型？

2. **v1.4 数据集是否 promote**: v1.4 移除了 DEPT 案例、新增了 4 条 chunk 级案例，但尚未 merge 为活跃数据集。是否应该合并？

3. **分阶段评测是否实现**: `pipeline_eval.md` 设计了但从未实现。是否需要实现 ChunkHybrid / Rerank / LLMGenerate 分阶段评测？

4. **MultiQuery / 端到端生成评测缺失**: 离线 runner 不经过 MultiQuery 和 LLM Generate，这些组件在评测中完全盲区。

5. **开源 Benchmark 是否引入**: 当前完全自建数据，统计意义和泛化能力均受限。

---

## 当前评测集一句话总结

> **一套基于虚构跨境电商知识库的自建 RAG 检索评测集（59 条），覆盖单文档事实检索 + 拒答 + 部门隔离，有完整的 CI/基线/报告基础设施，但 Top-1 准确率仅 57.6%（远低于 85% 生产门禁），无分阶段组件评测、无多跳/跨文档/表格/生成质量评测、无外部 benchmark 对齐。**

### 当前最大的 5 个问题（按严重程度排序）

1. **Top-1 准确率 57.6% 远低于生产门禁 85%**: 这是项目自身已识别的核心瓶颈，Reranker 模型能力上限受限，7 个调优实验均失败。

2. **无分阶段组件评测**: 无法单独证明 BM25 / Vector / Hybrid / RRF / Reranker / MultiQuery 各组件的独立效果。`pipeline_eval.md` 设计了但未实现。

3. **数据集覆盖面窄**: 仅覆盖单文档事实检索，无多跳/跨文档/表格/长文档/复杂推理/生成质量评测。42 个正例统计意义弱。

4. **生成质量评测几乎空白**: Faithfulness / Citation / Answer Correctness 仅在 E2E Judge 中覆盖（20 条，需真实 LLM），离线评测完全不涉及生成层。

5. **完全自建数据，无外部 Benchmark 对齐**: 无法与业界标准比较，数据集泛化能力未知。

### 当前最值得保留的 5 个东西

1. **注册制评测框架** (`backend/evaluation/`): 模块化、可移植、零项目依赖的核心设计，新增模块只需实现 RunnerFunc 并注册。

2. **CI 回归检测链**: 数据集校验闸门 → 离线评测 → 基线对比 → PR 评论，完整且有效。

3. **v1.3 数据集的 ground truth 审计**: 每条案例的 `fix_note` 记录了完整的修复历史，确保 ground truth 与 KB 文档原文一致。

4. **负例 + 拒答评测**: 12 条负例（含 4 条 hard negative near-miss）+ 100% 拒答准确率，是安全层的重要验证。

5. **持久化运行记录** (`data/eval_runs/`): 含 git_sha、dataset_version、prompt_versions、per-case JSON，支持事后追溯和对比。

### 下一步最应该重构的 5 个地方

1. **实现分阶段评测**: 按 `pipeline_eval.md` 设计实现 ChunkHybrid / Rerank / LLMGenerate 独立评测，证明各组件效果。

2. **扩充数据集至 100+ 条**: 增加多跳/跨文档/表格/长文档/复杂推理案例，提升统计意义和覆盖面。

3. **提升 Top-1 准确率**: 评估 Reranker 模型升级（如 bge-reranker-v2-m3 或 DashScope reranker），或引入 LLM-based reranking。

4. **引入外部 Benchmark**: 至少引入一个标准 benchmark（如 CRUD-RAG 或 BEIR 子集）作为泛化能力参照。

5. **统一数据集版本**: 决定 v1.3 vs v1.4 的去留，合并 DEPT 案例和 RT-CHUNK 案例，建立版本管理流程。
