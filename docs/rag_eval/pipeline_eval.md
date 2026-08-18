# Pipeline Eval — 代码速查

> 给开发者看的。3 个 Stage 评测器代码（V1.5 范围）。

---

## 1. TraceReader 接口

读 `trace_collector` span 的工具，不改 RAG 链路。

| 方法 | 返回 | 数据来源 |
|---|---|---|
| `get_metrics(span_name)` | dict | `trace.spans[i].metrics` |
| `get_chunk_hybrid()` | `{stage1_docs, stage2_chunks}` | span: `rag.chunk_hybrid` |
| `get_rerank()` | `{top_docs, top1_score}` | span: `rag.rerank` |
| `get_llm_generate()` | `{answer, citations}` | `trace.output` |
| `get_gate_metadata()` | `{layer, final_decision, self_correction_attempted}` | `trace.metadata.evidence_gate` |

> 完整代码：约 50 行，未列出（避免冗余）。

---

## 2. ③ ChunkHybridEvaluator

评测 Stage 1 文档召回 + Stage 2 chunk 召回。

```python
# backend/rag_eval/pipeline_eval/stage_chunk_hybrid.py
from backend.rag_eval.trace_reader import TraceReader


def evaluate_chunk_hybrid(trace: dict, ground_truth: dict) -> dict:
    reader = TraceReader(trace)
    data = reader.get_chunk_hybrid()

    expected_docs = set(ground_truth.get("relevant_docs", []))
    expected_chunks = set(ground_truth.get("relevant_chunks", []))

    doc_recall = (
        len(set(data["stage1_docs"]) & expected_docs) / max(len(expected_docs), 1)
        if expected_docs else 0.0
    )
    chunk_recall = (
        len(set(data["stage2_chunks"]) & expected_chunks) / max(len(expected_chunks), 1)
        if expected_chunks else 0.0
    )

    return {
        "hybrid_doc_recall": round(doc_recall, 4),
        "hybrid_chunk_recall": round(chunk_recall, 4),
    }
```

---

## 3. ⑤ RerankEvaluator

评测重排序首位命中率。

```python
# backend/rag_eval/pipeline_eval/stage_rerank.py
from backend.rag_eval.trace_reader import TraceReader


def evaluate_rerank(trace: dict, ground_truth: dict) -> dict:
    reader = TraceReader(trace)
    data = reader.get_rerank()

    expected_top_doc = (ground_truth.get("relevant_docs") or [None])[0]
    top1_hit = 1.0 if (expected_top_doc and expected_top_doc in data["top_docs"][:1]) else 0.0

    return {
        "rerank_top1_hit": top1_hit,
        "rerank_top1_score": round(data["top1_score"], 4),
    }
```

---

## 4. ⑥ LLMGenerateEvaluator

评测 Citation 引用准确性。

```python
# backend/rag_eval/pipeline_eval/stage_llm_generate.py
from backend.rag_eval.trace_reader import TraceReader


def evaluate_llm_generate(trace: dict, ground_truth: dict) -> dict:
    reader = TraceReader(trace)
    data = reader.get_llm_generate()

    citations = data["citations"]
    valid = sum(1 for c in citations if c.get("chunk_id") and c.get("doc_id"))
    accuracy = valid / max(len(citations), 1) if citations else 0.0

    return {
        "llm_citation_count": len(citations),
        "llm_citation_accuracy": round(accuracy, 4),
    }
```

---

## 5. Pipeline Runner

```python
# backend/rag_eval/pipeline_eval/runner.py
from backend.rag_eval.pipeline_eval.stage_chunk_hybrid import evaluate_chunk_hybrid
from backend.rag_eval.pipeline_eval.stage_rerank import evaluate_rerank
from backend.rag_eval.pipeline_eval.stage_llm_generate import evaluate_llm_generate


def evaluate_pipeline(trace: dict, ground_truth: dict) -> dict:
    return {
        "chunk_hybrid": evaluate_chunk_hybrid(trace, ground_truth),
        "rerank": evaluate_rerank(trace, ground_truth),
        "llm_generate": evaluate_llm_generate(trace, ground_truth),
    }
```

---

## 6. 修改指南

- **加新指标**：在对应 stage 函数里加 dict 字段
- **加新 stage**：新建 `stage_*.py`，在 `runner.py` 注册
- **改阈值**：在 `backend/rag_eval/config.py` 加常量
