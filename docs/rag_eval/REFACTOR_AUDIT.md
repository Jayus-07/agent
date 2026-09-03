# RAG Evaluation V2 — Refactor Audit

> Date: 2026-09-04
> Auditor: Senior RAG Evaluation Engineer
> Scope: All files under `backend/evaluation/`, `backend/rag/`, `docs/rag_eval/`, `.github/workflows/rag_eval.yml`, `data/baselines/`, `data/eval_runs/`

---

## 1. Current Evaluation Architecture

### 1.1 Module Structure

```
backend/evaluation/
├── __init__.py          (104 lines) — re-exports
├── models.py            (75 lines)  — TestCase, EvalResult, EvalReport, ModuleSummary
├── dataset.py           (120 lines) — load_dataset, validate_dataset
├── metrics.py           (223 lines) — recall_at_k, mrr, ndcg_at_k, etc.
├── judge.py             (161 lines) — LLM-as-judge (4 dimensions)
├── report.py            (782 lines) — markdown/json/html reports
├── storage.py           (210 lines) — eval run persistence
├── baseline.py          (234 lines) — baseline promote/diff/regression
└── runners/
    └── builtin.py       (867 lines) — 5 runners: planner, rag, sql, e2e, cs
```

### 1.2 Registered Runners

| Runner | Module | Needs Live | Status |
|--------|--------|-----------|--------|
| planner | `planner` | Yes | Active |
| rag | `rag` | No | Active (primary) |
| sql | `sql` | Yes | Active |
| e2e | `e2e` | Yes | Active |
| cs | `cs` | No | Active |

### 1.3 Data Models

- **TestCase**: `id, question, module, expected: dict, metadata: dict`
- **EvalResult**: `case_id, module, status (pass/fail/error/skip), expected, actual, metrics, duration_ms, error_msg`
- **ModuleSummary**: per-module aggregation
- **EvalReport**: `timestamp, module, mode, smoke, summaries, results, total_score`

---

## 2. Current Dataset Architecture

### 2.1 Active Dataset: `rag_test_kb.json` (v1.3)

- **59 total cases**: 42 positive RT + 12 negative + 5 department isolation
- **Schema**: `{id, question, module:"rag", kb_id:"rag_test_kb", expected:{...}, metadata:{...}}`
- **Expected fields**: `relevant_docs`, `relevant_snippets`, `min_relevant_chunks`, optional `match_type`, `should_reject`, `relevant_chunks` (always empty), `allowed_departments`, `forbidden_departments`

### 2.2 Case Distribution

| Category | Count | Notes |
|----------|------:|-------|
| Positive (fact_lookup) | 42 | Single-doc, mostly `relevant_docs` + `relevant_snippets` |
| Negative (should_reject) | 12 | 8 generic + 4 hard_negative_near_miss |
| Department isolation | 5 | 3 positive isolation + 2 leak guards |

### 2.3 Difficulty Distribution

| Difficulty | Count |
|-----------|------:|
| easy | 20 |
| medium | 26 |
| hard | 13 |

### 2.4 Critical Gaps

| Gap | Severity |
|-----|----------|
| `relevant_chunks` is **never populated** (only RT-035 has the key, value `[]`) | P0 |
| No multi-hop / multi-document cases | P0 |
| No table-specific cases | P0 |
| No long-document cases | P0 |
| No adversarial cases | P0 |
| No chunking/parsing evaluation cases | P1 |
| No MultiQuery evaluation | P1 |
| Query types limited to `fact_lookup` | P1 |
| `match_type="doc_id"` documented but unimplemented | P1 |

### 2.5 doc_id Protocol

- **Current**: `md5(basename)[:10]` — fragile to file renames
- **Protocol split**: `loader.py` uses `md5(basename)[:10]`; `indexer.py` uses namespaced `md5(kb|dept|subpath|basename)[:10]`
- **BM25 workaround**: dual-key matching (doc_id set + file basenames)
- **Known issue**: acknowledged in `indexing/doc_id.py` docstring; unification deferred to "F6 batch"

### 2.6 Sibling Datasets

- `rag_test_kb_v1.4.json` — exists but NOT active (not referenced by workflow/baseline)
- `cs.json`, `e2e.json`, `planner.json`, `sql.json` — other module datasets
- `rag.v1.deprecated.json`, `rag_v2.deprecated.json` — deprecated

---

## 3. Current RAG Pipeline

### 3.1 End-to-End Flow

```
Query
 ├─ QueryAnalyzer (zero-LLM, ~5ms) → ParsedQuery + metadata_filter
 ├─ KBRouter (keyword-weighted) → kb_id selection
 ├─ AnswerCache (first-turn only)
 └─ RAGChain.ask
     ├─ Retriever Stack (outer → inner):
     │   [HistoryAwareRetriever — optional]
     │   → ContextualCompressionRetriever(RerankCompressor)
     │   → MultiQueryRetriever (mode auto)
     │   → AdaptiveRetriever (cluster detection)
     │   → ChunkLevelRetriever (2-stage)
     │       Stage 1: doc-level (vector + keyword overlap)
     │       Stage 2: chunk-level (hybrid_retrieve)
     │           ├─ Vector Search (Chroma, k=8)
     │           ├─ BM25 Search (k=10)
     │           ├─ RRF Fusion (k=60)
     │           ├─ Neighbor/Adaptive expansion
     │           └─ Parent context attachment
     ├─ Rerank (DashScope qwen3-rerank OR local BGE CrossEncoder)
     ├─ Evidence Gates (Gate 1/1.5/2)
     ├─ LLM Generation (create_stuff_documents_chain)
     ├─ Gate 3 (META can_answer check)
     ├─ Citation Verification
     ├─ Claim Verification (numeric/date zero tolerance)
     └─ Faithfulness Check (NLI scoring)
```

### 3.2 Key Configuration Constants

| Stage | Key Constants | Defaults |
|-------|--------------|----------|
| Vector | `HYBRID_SEARCH_K`, `VEC_MIN_SCORE` | 8, 0.2 |
| BM25 | `BM25_SEARCH_K` | 10 |
| RRF | `rrf_k` | 60 |
| Adaptive | `ADAPTIVE_CLUSTER_THRESHOLD`, `ADAPTIVE_K_STEPS` | 0.3, [8,12,16] |
| MultiQuery | `MULTI_QUERY_MODE`, `COUNT`, `SIMILARITY` | auto, 3, 0.9 |
| Rerank | `RERANK_TOP_K`, `RERANK_SCORE_THRESHOLD` | 8, 0.3 |
| Evidence | `FAITHFULNESS_REJECT_SCORE`, `HIGH_RISK_REJECT_SCORE` | 0.5, 0.7 |
| Chunking | `LEAF_CHUNK_TOKENS`, `PARENT_CHUNK_TOKENS` | 500, 2000 |

### 3.3 Chunking Strategies

| Strategy | Trigger | Notes |
|----------|---------|-------|
| Structure | policy/compliance/security/etc. | Section-aware, parent-child |
| FinancialTable | financial docs | NL summary + row-level kv leaves |
| Step | SOP/training | Numbered steps |
| Legal | legal/contract | 第N条 boundaries |
| QA | FAQ docs | Q+A merged chunks |
| FixedSize | ad_policy | Legacy |
| Recursive | fallback | Generic recursive split |
| Semantic | gated OFF | Embedding-based boundaries |

### 3.4 Document Parsing

| Format | Parser | Lines | Notes |
|--------|--------|------:|-------|
| PDF | PyMuPDF | 251 | Layout-aware, table detection, NO OCR |
| DOCX | python-docx | 155 | Style-based headings, tables |
| Markdown | regex | 76 | `#` headings, lists |
| TXT | regex | 64 | Chinese numbering heuristics |
| CSV | csv module | 105 | Table nodes |
| XLSX | openpyxl | 57 | Per-sheet tables |

---

## 4. Current Metric Computation (Exact)

### 4.1 Retrieval Metrics

| Metric | Formula | Implementation |
|--------|---------|---------------|
| `recall_at_k` | `|expected ∩ actual[:k]| / |expected|` | Empty expected → 1.0 |
| `mrr` | `1/rank` of first relevant (1-indexed) | Empty expected → 1.0 |
| `ndcg_at_k` | `DCG/IDCG`, binary relevance | Empty expected → 1.0 |
| `top1_accuracy` | 1.0 if `actual_docs[0] ∈ expected` | Both empty → 1.0 |
| `chunk_recall` | snippet: `matched/total`; chunk: `|intersection|/|expected|`; fallback: doc overlap | Complex branching |
| `reject_accuracy` | 1.0 if `should_reject` AND confidence ∈ {none, low} | Per-case; omitted for positives |
| `dept_leak` | 0/1: `retrieved_depts ∩ forbidden ≠ ∅` OR `retrieved_depts - allowed ≠ ∅` | Per-case |

### 4.2 Aggregate Metrics

| Metric | Formula | Notes |
|--------|---------|-------|
| `pass_rate` | `passed / total` | |
| `aggregate_metrics` | arithmetic mean per metric | Rounds to 4 decimals |
| `reject_accuracy` (aggregate) | Requires key `"rejected"` ≥ 1.0 | **BUG**: runner emits `"reject_accuracy"`, not `"rejected"` → always 0.0 |
| `stability_variance` | `pstdev` of pairwise char-2-gram Jaccard | **Misnamed**: returns std dev, not variance |

### 4.3 Confidence Heuristic (No LLM)

| Condition | Confidence | Gate | Reason |
|-----------|-----------|------|--------|
| Empty details | none | retrieval | no_evidence |
| top1_score < 0.5 | none | retrieval | low_relevance |
| top1_score < 0.6 | low | retrieval | — |
| top1 - top2 < 0.15 | medium | — | — |
| else | high | — | — |
| Entity absent (reject cases) | low | entity_check | entity_absent |

### 4.4 Pass/Fail Logic

```
pass = chunk_hit AND NOT dept_leak
```

Confidence/reject metrics do NOT affect pass/fail.

### 4.5 Missing Metrics

| Metric | Status |
|--------|--------|
| Precision@K | Not implemented |
| Context Noise Rate | Not implemented |
| Faithfulness (standalone) | Only E2E LLM Judge |
| Citation (rule-based) | Only LLM Judge |
| Answer Correctness (typed) | Not implemented |
| Stage-level metrics | Not implemented |
| Ablation support | Not implemented |

---

## 5. Current CI/CD Behavior

### 5.1 Workflow: `.github/workflows/rag_eval.yml`

**Triggers**:
- PR: paths `backend/rag/**`, `backend/evaluation/**`, `backend/prompts/**`, `data/docs/rag_test_kb/**`, `data/baselines/**`
- Push: `master`
- Schedule: Mon 09:00 Beijing
- Manual: `workflow_dispatch`

**Steps**:
1. Checkout + Python 3.11 + deps
2. Cache HF models (bge-small-zh + bge-reranker-base)
3. Validate dataset: `validate_eval_dataset.py`
4. Init test DB
5. RAG eval (no LLM): `python -m evaluation --module rag`
6. E2E eval: `--live --judge` (push/dispatch only)
7. Regression check: `baseline.py` with threshold 0.05
8. Upload artifacts + PR comment

### 5.2 Regression Thresholds

- `pass_rate` drop > 5% → ERROR (exit 2)
- Per-metric drop > 5% → WARNING
- Per-metric drop > 10% → ERROR
- `critical_metrics` can override to immediate ERROR

### 5.3 Issues

- No per-metric differentiated thresholds (e.g., dept_leak > 0 should be instant fail)
- No stage-level evaluation in CI
- No ablation in CI
- Single dataset for all CI stages (no smoke/core/hard split)

---

## 6. Current Baseline Behavior

### 6.1 Active Baseline: `baseline_rag_1.3.json`

```json
{
  "module": "rag",
  "dataset_version": "1.3",
  "promoted_at": "2026-08-21T...",
  "pass_rate": 0.9444,
  "metrics": {
    "recall@5": 0.9444,
    "recall@10": 0.9444,
    "mrr": 0.8889,
    "ndcg@10": 0.9034,
    "top1_accuracy": 0.5741,
    "reject_accuracy": 1.0,
    "chunk_recall": 0.7222
  },
  "total": 54, "passed": 51, "failed": 3
}
```

### 6.2 Issues

- Baseline has `total: 54` (pre-DEPT cases) but dataset now has 59 cases
- Latest run shows `pass_rate: 0.8644` vs baseline `0.9444` → -8pp drop exceeds 5% threshold
- Stale baseline `baseline_rag_1.0.json` still exists (37 cases, no top1/reject metrics)
- `dept_leak` metric not in baseline (added after v1.3 baseline was promoted)

---

## 7. Current Ground Truth Structure

### 7.1 Schema

```json
{
  "id": "RT-XXX",
  "question": "...",
  "module": "rag",
  "kb_id": "rag_test_kb",
  "expected": {
    "relevant_docs": ["doc_id_1"],
    "relevant_snippets": ["snippet text..."],
    "min_relevant_chunks": 1,
    "match_type": "snippet",
    "should_reject": false,
    "relevant_chunks": [],
    "allowed_departments": [],
    "forbidden_departments": []
  },
  "metadata": {
    "difficulty": "easy|medium|hard",
    "domain": "...",
    "probe_type": "...",
    "doc_type": "...",
    "fix_note": "...",
    "isolation_test": true
  }
}
```

### 7.2 Issues

- `relevant_chunks` never populated → chunk_recall relies on fallback (doc overlap)
- No `expected_answer` field → no answer correctness evaluation
- No `must_contain` / `must_not_contain` → no typed answer checking
- No `query_type` taxonomy
- No `requires_multi_hop` / `requires_multi_document` flags
- No `source_documents` / `source_sections` traceability
- `doc_id` fragile to renames
- No `ground_truth_verified` flag

---

## 8. Current Evaluation Gaps

### 8.1 Dataset Gaps (P0)

1. **No multi-hop / multi-document cases** — 0 coverage
2. **No chunk-level ground truth** — `relevant_chunks` always empty
3. **No table evaluation** — 0 table-specific cases
4. **No long-document evaluation** — no "lost in the middle" testing
5. **No adversarial cases** — no same-form-different-meaning, numeric approximation, title pollution
6. **No parsing/chunking evaluation** — no format-specific test cases
7. **Single query type** — all positive cases are `fact_lookup`

### 8.2 Metric Gaps (P0)

1. **No Precision@K** — cannot measure context noise
2. **No stage-level evaluation** — cannot diagnose which pipeline stage fails
3. **No ablation evaluation** — cannot measure individual component contribution
4. **No standalone faithfulness** — only E2E LLM Judge (expensive, unreliable)
5. **No rule-based citation evaluation** — only LLM Judge
6. **No typed answer correctness** — no numeric/boolean/list comparison
7. **No failure taxonomy** — cannot categorize failure modes

### 8.3 Pipeline Gaps (P1)

1. **No MultiQuery evaluation** — 0 cases test multi-query behavior
2. **No Adaptive Retrieval ablation** — cannot measure adaptive contribution
3. **No Reranker before/after comparison** — Top-1 bottleneck unverified
4. **No dataset tiering** — no smoke/core/hard split for CI

### 8.4 Infrastructure Gaps (P1)

1. **`reject_accuracy` contract mismatch** — aggregate function always returns 0.0
2. **Judge failure = silent pass** — fallback total=3.0 meets pass threshold
3. **Judge `total` never recomputed** — LLM arithmetic trusted blindly
4. **Baseline stale** — total=54 vs actual 59 cases
5. **No dataset quality gate** — validation doesn't check `expected` structure

---

## 9. Code-Documentation Inconsistencies

| # | Location | Inconsistency |
|---|----------|--------------|
| 1 | `builtin.py` docstring | Says 4 runners; actually 5 |
| 2 | `builtin.py` docstring | References `multi_agent.*`; code uses `backend.agents` / `backend.orchestration` |
| 3 | `builtin.py:421` | Comment says `match_type: chunk_id | snippet | doc_id` but no `doc_id` branch |
| 4 | `metrics.py` | `reject_accuracy()` requires `"rejected"` key; runner emits `"reject_accuracy"` |
| 5 | `metrics.py` | `stability_variance` returns pstdev, not variance |
| 6 | `report.py:25,38` | Duplicate `reject_accuracy` key in `METRIC_LABELS` |
| 7 | `report.py` CSS | Invalid hex `#10b9810` (7 digits) — green color never renders |
| 8 | `report.py` | Dashboard hardwired to RAG module; empty for other modules |
| 9 | `storage.py` | Docstring says "dual write PostgreSQL + filesystem"; `persist_report` never calls DB |
| 10 | `dataset.py` | `validate_dataset` doesn't validate `expected` structure |
| 11 | `validate_eval_dataset.py` | Requires `kb_id` (ERROR) but loader treats it as optional |
| 12 | `v2-evaluation-design.md` | Describes "50 cases" / "8 negatives"; actual is 59/12 |
| 13 | `HANDOFF.md` | Documents "Top-1 58%"; latest run shows 57.63% |
| 14 | `models.py` | `EvalReport.module` docstring missing `"cs"` |
| 15 | `__init__.py` | `write_html_report` and `flag_regressions` not exported |
| 16 | `judge.py` | `JUDGE_SYSTEM_PROMPT` explicitly NOT sent to LLM (ChatOllama limitation) |
| 17 | `builtin.py:509` | Fallback assigns `chunk_hit` a set (not bool); works by truthiness |
| 18 | `config/rag.py` | `FAITHFULNESS_REJECT_SCORE` defined twice (risk-tiered then flat 0.5); latter wins |

---

## 10. Refactor Risks

### 10.1 High Risk

| Risk | Mitigation |
|------|-----------|
| Changing `doc_id` protocol breaks all ground truth | Use content-hash or stable identifier; migrate existing cases |
| New schema breaks backward compatibility with v1.3 baseline | Version-gate schema changes; maintain v1.3 loader |
| Stage evaluation requires pipeline instrumentation | Use existing trace spans; minimal code changes |
| Ablation changes pipeline behavior | Feature-flag each ablation mode; never modify production path |

### 10.2 Medium Risk

| Risk | Mitigation |
|------|-----------|
| New metrics change CI pass/fail behavior | Add metrics as informational first; promote to gate after calibration |
| Dataset split changes CI runtime | Profile each tier; keep smoke < 2 min |
| LLM-based faithfulness is expensive | Use rule-based checks first; LLM only for ambiguous cases |

### 10.3 Low Risk

| Risk | Mitigation |
|------|-----------|
| Report format changes | Add new sections; don't remove existing ones |
| New dataset files | Additive; don't modify existing `rag_test_kb.json` structure |

### 10.4 Constraints

- **Hardware**: GPU 1660 Ti (6GB VRAM), RAM 16GB — limits reranker model size
- **CI time**: Must keep PR eval < 5 min; full eval < 30 min
- **Production pipeline**: Cannot modify RAG pipeline for evaluation purposes
- **Existing tests**: Must not break 578 existing tests
- **Baseline compatibility**: Must maintain ability to compare against v1.3 baseline

---

## Appendix A: File Inventory

| File | Lines | Role |
|------|------:|------|
| `backend/evaluation/__init__.py` | 104 | Re-exports |
| `backend/evaluation/models.py` | 75 | Data models |
| `backend/evaluation/dataset.py` | 120 | Dataset loading |
| `backend/evaluation/metrics.py` | 223 | Metric functions |
| `backend/evaluation/judge.py` | 161 | LLM judge |
| `backend/evaluation/report.py` | 782 | Report generation |
| `backend/evaluation/storage.py` | 210 | Run persistence |
| `backend/evaluation/baseline.py` | 234 | Baseline management |
| `backend/evaluation/runners/builtin.py` | 867 | Runner implementations |
| `backend/scripts/validate_eval_dataset.py` | 210 | Dataset validation CLI |
| `backend/rag/pipeline.py` | 749 | RAGPipeline orchestrator |
| `backend/rag/chain.py` | 1068 | RAGChain (LCEL) |
| `backend/rag/reranker.py` | 455 | Reranker (DashScope + local BGE) |
| `backend/rag/base.py` | 136 | CustomRetriever (vector) |
| `backend/rag/retrieval/hybrid.py` | 400 | Hybrid retrieve + RRF |
| `backend/rag/retrieval/bm25_store.py` | 382 | BM25 persistence |
| `backend/rag/retrieval/retrievers.py` | 556 | ChunkLevel + Adaptive |
| `backend/rag/retrieval/multi_query.py` | 341 | MultiQuery |
| `backend/rag/retrieval/query_analyzer.py` | 453 | Zero-LLM query analysis |
| `backend/rag/retrieval/enhanced_hybrid_retrieval.py` | 306 | Complexity-adaptive |
| `backend/rag/preprocessing/chunking.py` | 939 | 8 chunk strategies |
| `backend/rag/indexing/indexer.py` | 1425 | Incremental indexer |
| `backend/rag/indexing/doc_id.py` | 96 | doc_id protocol |
| `backend/rag/evidence_gate/operations.py` | 458 | Gate 1/1.5/2 |
| `backend/rag/guardrails/scorer.py` | 306 | Faithfulness NLI |
| `backend/rag/citation.py` | 180 | Citation verify/format |

## Appendix B: Latest Evaluation Run (2026-08-27)

```
Total: 59 | Passed: 51 | Failed: 8 | Pass Rate: 86.44%

Metrics:
  recall@5:       0.8559
  recall@10:      0.8559
  mrr:            0.8390
  ndcg@10:        0.8391
  top1_accuracy:  0.5763
  reject_accuracy: 1.0000
  chunk_recall:   0.6271
  dept_leak:      0.0000
```
