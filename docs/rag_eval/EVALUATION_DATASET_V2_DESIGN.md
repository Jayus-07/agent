# RAG Evaluation Dataset V2 Design

> Date: 2026-09-04
> Version: 2.0

---

## 1. Design Goals

1. **Diagnostic power over case count** — every case must test a specific capability
2. **Stage-level fault localization** — identify which pipeline stage caused failure
3. **Ablation-ready** — measure each component's contribution
4. **Stable ground truth** — content-hash based document identifiers
5. **Multi-dimensional coverage** — retrieval, generation, faithfulness, safety

## 2. Schema V2

### 2.1 Case Schema

```json
{
  "id": "string — unique case identifier (e.g., RETR-BASIC-001)",
  "question": "string — user question",
  "module": "rag",
  "kb_id": "string — target knowledge base ID",

  "expected": {
    "relevant_docs": ["list of doc basenames that contain the answer"],
    "relevant_snippets": ["list of exact text snippets that should be retrieved"],
    "relevant_chunks": ["list of chunk_ids containing the answer (optional)"],
    "min_relevant_chunks": 1,

    "expected_answer": "string — reference answer (optional, for generation eval)",
    "must_contain": ["list of strings that must appear in the answer"],
    "must_not_contain": ["list of strings that must NOT appear in the answer"],

    "should_reject": false,
    "match_type": "doc|snippet|chunk|multi_document",

    "allowed_departments": [],
    "forbidden_departments": [],

    "answer_type": "fact|numeric|boolean|list|procedure|comparison|none"
  },

  "metadata": {
    "difficulty": "easy|medium|hard|adversarial",
    "domain": "inventory|finance|legal|hr|product|security|tech|logistics|returns|faq|supplier|mixed",
    "query_type": "fact_lookup|definition|procedure|comparison|aggregation|reasoning|multi_hop|conditional|exception|negative",
    "requires_multi_hop": false,
    "requires_multi_document": false,
    "source_documents": ["list of source filenames"],
    "source_sections": ["list of section titles"],
    "tier": "smoke|core|hard|regression",
    "probe_type": "basic|hard_negative|adversarial|table|long_doc|department|parsing",
    "created_at": "2026-09-04",
    "version": "2.0",
    "ground_truth_verified": true
  }
}
```

### 2.2 Schema Changes from V1.3

| Change | Reason |
|--------|--------|
| Added `expected_answer` | Enable answer correctness evaluation |
| Added `must_contain` / `must_not_contain` | Typed answer checking |
| Added `answer_type` | Type-specific correctness rules |
| Added `query_type` taxonomy | Multi-dimensional coverage |
| Added `requires_multi_hop` / `requires_multi_document` | Multi-hop tracking |
| Added `tier` | Dataset tiering for CI |
| Added `probe_type` | Failure categorization |
| Added `ground_truth_verified` | Quality gate |
| Changed `match_type` values | `doc|snippet|chunk|multi_document` (clearer) |
| `relevant_docs` uses **basename** not doc_id | Stable against doc_id protocol changes |

### 2.3 Document Identifier Strategy

**Decision**: Use **file basename** as the stable identifier in ground truth.

Rationale:
- `doc_id = md5(basename)[:10]` is fragile to renames
- Content hash changes when content changes (also fragile)
- Basename is human-readable and traceable
- Validation script maps basename → doc_id at runtime

Ground truth chain:
```
Case.expected.relevant_docs (basename)
    → validate_eval_dataset.py maps to doc_id
    → Runtime uses doc_id for matching
```

## 3. Dataset Directory Structure

```
backend/evaluation/datasets/
├── rag/
│   ├── retrieval_basic.json          — single-doc fact lookup (easy/medium)
│   ├── retrieval_multi_doc.json      — multi-document / multi-hop
│   ├── retrieval_hard_negative.json  — hard negatives & near-miss
│   ├── retrieval_adversarial.json    — adversarial (homonym, numeric, title pollution)
│   ├── retrieval_table.json          — table-structured data
│   ├── retrieval_chunk.json          — chunk-level ground truth
│   ├── retrieval_department.json     — department isolation
│   ├── generation.json               — answer correctness + faithfulness
│   └── regression.json               — auto-accumulated regression cases
├── rag_test_kb.json                  — V2 combined (all of the above merged)
├── cs.json
├── e2e.json
├── planner.json
└── sql.json
```

### 3.1 Tier Assignment

| Tier | Purpose | CI Trigger | Expected Cases |
|------|---------|-----------|---------------:|
| smoke | PR quick check | Every PR | 15-20 |
| core | Full retrieval | PR + push | 80-100 |
| hard | Adversarial + edge | Push + nightly | 30-50 |
| regression | Historical failures | All triggers | Variable |

## 4. Query Type Taxonomy

| Type | Description | Example |
|------|-------------|---------|
| `fact_lookup` | Single fact from one document | "退货期限是几天？" |
| `definition` | Term/concept definition | "什么是呆滞品？" |
| `procedure` | Step-by-step process | "采购流程是什么？" |
| `comparison` | Compare two or more things | "A级和B级供应商有什么区别？" |
| `aggregation` | Aggregate info from multiple sections | "库存盘点有哪些要求？" |
| `reasoning` | Draw conclusion from rules | "供应商连续两季度D级会怎样？" |
| `multi_hop` | Chain info across documents | "库存低于多少需要补货，补货需要走什么流程？" |
| `conditional` | Answer depends on condition | "如果报销金额超过5000元，需要什么审批？" |
| `exception` | Normal + exception rules | "正常退货流程是什么？什么情况下不可退？" |
| `negative` | Should be rejected | "如何投资股票？" (out of domain) |

## 5. Probe Type Taxonomy

| Probe | What it tests |
|-------|--------------|
| `basic` | Standard single-doc retrieval |
| `hard_negative` | Similar keywords but wrong context |
| `adversarial` | Homonyms, numeric tricks, title pollution |
| `table` | Structured data in tables |
| `long_doc` | Lost-in-the-middle, deep sections |
| `department` | Cross-department isolation |
| `parsing` | Document format handling |

## 6. Version Policy

| Version | Meaning |
|---------|---------|
| 2.0.0 | Schema change (breaking) |
| 2.1.0 | New cases added (backward compatible) |
| 2.0.1 | Ground truth correction |

### Baseline Promotion Rules

- Baseline can only be promoted after **full evaluation** passes
- Schema change requires new baseline version
- Ground truth correction requires re-validation of affected cases
- Case addition does NOT require baseline re-promotion (additive)
