# Failure Taxonomy

> Date: 2026-09-04

---

## Purpose

Every evaluation failure must be classified into exactly one category. This enables:
- Aggregate statistics: "60% of Top-1 failures are ranking_error"
- Targeted optimization: "fix chunking to reduce wrong_chunk failures"
- Regression tracking: "wrong_document failures increased after X change"

## Taxonomy

```
retrieval/
├── missing_document     — correct document not in top-K at all
├── wrong_document       — wrong document ranked #1
├── wrong_chunk          — right document but wrong chunk
├── ranking_error        — correct doc retrieved but ranked too low
├── low_recall           — too few relevant results in top-K
└── high_noise           — too many irrelevant results in top-K

generation/
├── hallucination        — answer contains unsupported claims
├── incomplete           — answer misses key information
├── incorrect            — answer contains wrong facts
└── unsupported_claim    — specific claim not backed by context

citation/
├── missing              — no citation provided
├── wrong_source         — citation points to wrong document
└── unsupported          — cited source doesn't support the claim

security/
├── refusal_failure      — should have rejected but answered
├── department_leak      — retrieved from forbidden department
└── adversarial_success  — fell for adversarial trap

pipeline/
├── timeout              — exceeded time limit
├── model_error          — LLM/embedding model error
└── infrastructure       — DB/index/connection error
```

## Classification Rules

### Automatic Classification (from metrics)

| Condition | Classification |
|-----------|---------------|
| `top1_doc ∉ relevant_docs` AND `relevant_docs ∩ top5_docs = ∅` | `retrieval/missing_document` |
| `top1_doc ∉ relevant_docs` AND `relevant_docs ∩ top5_docs ≠ ∅` | `retrieval/ranking_error` |
| `top1_doc ∈ relevant_docs` BUT `top1_chunk ∉ relevant_chunks` | `retrieval/wrong_chunk` |
| `should_reject=True` AND `confidence=high` | `security/refusal_failure` |
| `dept_leak=1` | `security/department_leak` |
| `must_contain` not satisfied | `generation/incomplete` |
| `must_not_contain` violated | `generation/incorrect` |
| Faithfulness score < threshold | `generation/hallucination` |

### Manual Classification (requires human review)

When automatic classification is ambiguous, flag for review with top candidate categories.

## Failure Report Format

```json
{
  "case_id": "RETR-BASIC-001",
  "status": "fail",
  "failure_category": "retrieval/ranking_error",
  "failure_details": {
    "expected_top1": "ops_库存管理制度.pdf",
    "actual_top1": "faq_常见问题FAQ.md",
    "expected_in_top5": true,
    "rerank_score_top1": 0.35,
    "stage": "S5_reranker"
  }
}
```
