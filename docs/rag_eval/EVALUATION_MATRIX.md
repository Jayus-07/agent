# Evaluation Matrix

> Date: 2026-09-04

---

## Coverage Matrix: Before vs After

| Capability | Before (v1.3) | After (v2.0) | Delta |
|-----------|-------------:|------------:|------:|
| Basic retrieval (fact_lookup) | 42 | 30 | -12 (re-categorized) |
| Definition | 0 | 8 | +8 |
| Procedure | 0 | 6 | +6 |
| Comparison | 0 | 5 | +5 |
| Aggregation | 0 | 4 | +4 |
| Reasoning | 0 | 4 | +4 |
| Multi-hop | 0 | 8 | +8 |
| Conditional | 0 | 4 | +4 |
| Exception | 0 | 3 | +3 |
| Negative (generic) | 8 | 8 | 0 |
| Negative (hard) | 4 | 10 | +6 |
| Adversarial (homonym) | 0 | 4 | +4 |
| Adversarial (numeric) | 0 | 4 | +4 |
| Adversarial (title pollution) | 0 | 3 | +3 |
| Table | 0 | 6 | +6 |
| Chunk-level | 0 | 8 | +8 |
| Department isolation | 5 | 8 | +3 |
| **Total positive** | **42** | **~80** | **+38** |
| **Total negative** | **12** | **~29** | **+17** |
| **Total cases** | **59** | **~125** | **+66** |

## Evaluation Dimension Matrix

| Dimension | Metric | Stage | Ablation |
|-----------|--------|-------|----------|
| **Retrieval** | | | |
| Document recall | Recall@5, Recall@10 | Stage 1-5 | Yes |
| Document ranking | Top-1, MRR, NDCG@10 | Stage 1-5 | Yes |
| Document precision | Precision@5, Precision@10 | Stage 1-5 | Yes |
| Chunk recall | Chunk Recall@K | Stage 2-5 | Yes |
| Context noise | Noise Rate | Stage 5-6 | Yes |
| **Generation** | | | |
| Answer correctness | must_contain / must_not_contain | Stage 7 | No |
| Faithfulness | Claim support ratio | Stage 7 | No |
| Citation | Source validity | Stage 7 | No |
| **Safety** | | | |
| Reject accuracy | Reject rate on negatives | E2E | No |
| Department leak | Leak rate | E2E | No |

## Stage Evaluation Design

| Stage | What | Input | Output Metrics |
|-------|------|-------|---------------|
| S1: Vector | Chroma similarity search | query | Recall@K, MRR, NDCG |
| S2: BM25 | BM25 sparse retrieval | query | Recall@K, MRR, NDCG |
| S3: Hybrid/RRF | Vector + BM25 + RRF | query | Recall@K, MRR, NDCG, Precision@K |
| S4: Adaptive | Cluster expansion | S3 results | Recall delta, noise delta |
| S5: Reranker | Cross-encoder rerank | S3/S4 results | Top-1 delta, MRR delta, NDCG delta |
| S6: Final context | After compression | S5 results | Precision@K, noise rate |
| S7: LLM | Generation | S6 context | Answer correctness, faithfulness |

## Ablation Modes

| Mode | Components | Purpose |
|------|-----------|---------|
| `vector_only` | Vector search only | Baseline dense retrieval |
| `bm25_only` | BM25 only | Baseline sparse retrieval |
| `hybrid` | Vector + BM25 + RRF | Measure RRF fusion value |
| `hybrid_rerank` | + Reranker | Measure reranker value |
| `hybrid_adaptive` | + Adaptive | Measure adaptive value |
| `full` | All components | Production pipeline |
