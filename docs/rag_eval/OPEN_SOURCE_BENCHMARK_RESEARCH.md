# Open-Source RAG Evaluation Benchmark Research

> Date: 2026-09-04
> Purpose: Evaluate which open-source benchmarks and frameworks are suitable for this enterprise RAG project

---

## 1. Retrieval Benchmarks

### 1.1 BEIR (Benchmarking Information Retrieval)

| Dimension | Finding |
|-----------|---------|
| **Problem** | Heterogeneous zero-shot evaluation of IR models across 18 datasets and 9 task types |
| **Dataset Organization** | Per-dataset: `corpus.jsonl`, `queries.jsonl`, `qrels/`, `test.tsv` |
| **Ground Truth** | TREC-style qrels: query-id → doc-id → graded relevance (human annotated) |
| **Query Types** | Dataset-level task types (QA, fact-checking, citation prediction, etc.) |
| **Retrieval Eval** | nDCG@10 (primary), MAP, MRR, Recall@k, Precision@k |
| **Generation/Faithfulness** | None — retrieval only |
| **License** | Apache-2.0 (framework); datasets vary |

**Suitability**: **Reference only**. BEIR is English-centric and domain-generic. Our project is Chinese enterprise domain. However, BEIR's evaluation protocol (qrels format, nDCG@10 primary metric) is the industry standard we should align with.

**Recommendation**: Reference its evaluation protocol. Do NOT import BEIR datasets.

---

### 1.2 MTEB / MMTEB (Massive Text Embedding Benchmark)

| Dimension | Finding |
|-----------|---------|
| **Problem** | Unified benchmark for text embedding models across 9 task families |
| **Scale** | MMTEB: 500+ tasks, 250+ languages; C-MTEB covers Chinese |
| **Ground Truth** | BEIR-style corpus/queries/qrels for retrieval tasks |
| **Primary Metric** | nDCG@10 (replaced Spearman for retrieval) |
| **Chinese Coverage** | C-MTEB has Chinese retrieval/reranking/STS datasets |
| **License** | MIT-style; datasets vary |

**Suitability**: **Reference for embedding/reranker selection**. C-MTEB retrieval and reranking leaderboards are useful for model selection. Do NOT import as test dataset.

**Recommendation**: Reference for model selection. Use C-MTEB reranking leaderboard data for reranker benchmark comparison.

---

### 1.3 MIRACL (Multilingual IR Across a Continuum of Languages)

| Dimension | Finding |
|-----------|---------|
| **Problem** | Multilingual ad-hoc retrieval across 18 languages |
| **Dataset** | Per-language Wikipedia passage corpora + native-speaker annotated queries/qrels |
| **Key Feature** | Explicit human-annotated negative passages (not auto-derived) |
| **Chinese** | Yes, Chinese is one of the 16 released languages |
| **Primary Metric** | nDCG@10, Recall@100 |
| **License** | Apache-2.0 |

**Suitability**: **Limited**. MIRACL's strength is multilingual retrieval with annotated negatives. Our project is Chinese-only enterprise domain. The annotated negative passage methodology is worth learning from.

**Recommendation**: Reference its negative annotation methodology. Do NOT import datasets.

---

## 2. RAG Evaluation Datasets / Benchmarks

### 2.1 RAGTruth

| Dimension | Finding |
|-----------|---------|
| **Problem** | Word-level hallucination detection in RAG outputs |
| **Dataset** | ~18k responses from 6 LLMs; 450 manually annotated instances (1,428 spans) |
| **Ground Truth** | Word-level hallucination spans: evident/subtle conflict, evident/subtle baseless |
| **Task Types** | QA (MS MARCO), Data-to-Text (Yelp), Summarization (CNN/DM) |
| **Key Finding** | Fine-tuned Llama-2-13B beats GPT-4 prompting for hallucination detection |
| **License** | Research use |

**Suitability**: **Reference for faithfulness evaluation design**. RAGTruth's claim-level annotation methodology directly informs our faithfulness evaluation design. The 4-category hallucination taxonomy (evident/subtle × conflict/baseless) is adoptable.

**Recommendation**: Reference its hallucination taxonomy and claim decomposition approach for our faithfulness evaluator.

---

### 2.2 RAGBench

| Dimension | Finding |
|-----------|---------|
| **Problem** | Explainable large-scale RAG benchmark with dimensional labels |
| **Dataset** | ~100k examples across 5 domains (bio-medical, general, legal, customer support, finance) |
| **Ground Truth** | GPT-4 labels on 3 dimensions: Adherence, Relevance, Utilization |
| **Key Finding** | Fine-tuned DeBERTa matches/beats GPT-4 as judge at far lower cost |
| **Key Finding** | RAG models often produce relevant answers that fail to fully utilize context |
| **License** | Gated HF dataset |

**Suitability**: **Reference for multi-dimensional evaluation**. RAGBench's 3-dimension model (Adherence/Relevance/Utilization) is a good reference. The customer support and finance domains partially overlap with our enterprise scenario. The finding that "relevant answers fail to utilize context" is directly relevant to our Top-1 problem.

**Recommendation**: Reference its dimensional evaluation approach. Consider its "Utilization" dimension for our generation evaluation.

---

### 2.3 CRUD-RAG

| Dimension | Finding |
|-----------|---------|
| **Problem** | First comprehensive Chinese RAG benchmark |
| **Dataset** | 80k Chinese news documents as KB; CRUD taxonomy tasks |
| **CRUD Taxonomy** | Create (continuation), Read (QA), Update (hallucination modification), Delete (summarization) |
| **Ground Truth** | Reference answers + hallucination labels |
| **Key Finding** | Larger chunks help creation/summarization; dense retrieval + reranking best for QA |
| **License** | Apache-2.0 |

**Suitability**: **Partially suitable**. CRUD-RAG is Chinese, which matches our project. However, it uses news documents, not enterprise knowledge bases. The CRUD taxonomy is interesting but overkill for our needs. The "Update" (hallucination modification) task is relevant to our faithfulness evaluation.

**Recommendation**: Reference its Chinese evaluation methodology. Do NOT import directly (news domain mismatch). Consider adopting its hallucination probing approach.

---

### 2.4 RGB (Retrieval-Augmented Generation Benchmark)

| Dimension | Finding |
|-----------|---------|
| **Problem** | Tests whether LLMs possess abilities RAG assumes |
| **Dataset** | Bilingual (EN+ZH) Wikipedia-based; ~600 base + ~200 per ability |
| **4 Abilities** | (1) Noise robustness, (2) Negative rejection, (3) Information integration, (4) Counterfactual robustness |
| **Key Finding** | LLMs struggle most with negative rejection and multi-document integration, especially in Chinese |
| **Method** | Pre-assembled contexts with controlled noise ratios (0→0.8) |
| **License** | CC BY-NC-SA 4.0 |

**Suitability**: **Highly relevant methodology**. RGB's 4-ability framework maps well to our needs:
- Noise robustness → our context noise evaluation
- Negative rejection → our reject accuracy (already strong)
- Information integration → our multi-hop/multi-document gap
- Counterfactual robustness → our adversarial evaluation

**Recommendation**: **Adopt its ability taxonomy** for our evaluation matrix design. Reference its noise injection methodology for adversarial dataset construction.

---

### 2.5 MultiHop-RAG

| Dimension | Finding |
|-----------|---------|
| **Problem** | Dedicated multi-hop retrieval + reasoning benchmark |
| **Dataset** | 609 news articles; 2,556 multi-hop queries with evidence document pairs |
| **4 Categories** | Inference (31.9%), Comparison (33.5%), Temporal (22.8%), Null/Unanswerable (11.8%) |
| **Ground Truth** | Answer + evidence document sets (pairs/quads) |
| **Metrics** | MAP@K, MRR@K, Hit@K for retrieval; answer accuracy for generation |
| **Key Finding** | Existing RAG systems perform poorly when evidence spans documents |
| **License** | ODC-BY |

**Suitability**: **Highly relevant for multi-hop design**. MultiHop-RAG directly addresses our biggest dataset gap. Its 4-category taxonomy (inference/comparison/temporal/null) is directly adoptable. The evidence document pair annotation is exactly what we need for multi-document ground truth.

**Recommendation**: **Adopt its multi-hop taxonomy and evidence annotation approach** for our multi-document dataset design.

---

## 3. Evaluation Frameworks

### 3.1 RAGAS

| Dimension | Finding |
|-----------|---------|
| **Problem** | Reference-based and reference-free component-wise RAG evaluation |
| **Retrieval Metrics** | context_precision, context_recall, context_entities_recall, noise_sensitivity |
| **Generation Metrics** | answer relevancy, answer correctness, factual_correctness, summarization |
| **Faithfulness** | Claim decomposition: LLM splits answer → statements → verify each against context |
| **Citation** | **Removed** from current version (existed in early 0.0.x) |
| **Test Generation** | KG-based TestsetGenerator with single_hop/multi_hop synthesizers |
| **License** | Apache-2.0 |

**Suitability**: **Partially suitable**. RAGAS's claim-decomposition faithfulness approach is the gold standard. Its `noise_sensitivity` metric (measuring irrelevant context in retrieved results) directly addresses our context noise gap. However, RAGAS requires LLM calls for every metric — expensive for CI.

**Recommendation**: **Adopt its claim decomposition approach** for faithfulness evaluation. **Adopt `noise_sensitivity` concept** for our Precision@K / context noise metric. Do NOT integrate RAGAS as a dependency (too heavy for CI).

---

### 3.2 DeepEval

| Dimension | Finding |
|-----------|---------|
| **Problem** | "Unit testing for LLM apps" — pytest-style assertions |
| **RAG Metrics** | Answer Relevancy, Faithfulness, Contextual Precision/Recall/Relevancy |
| **Faithfulness** | LLM extracts claims → each verified against context → supported ratio |
| **Other** | G-Eval (custom criteria), DAG (deterministic judge), Hallucination |
| **Test Generation** | Synthesizer with evolutions (reasoning, concretizing) |
| **License** | MIT |

**Suitability**: **Reference for test design**. DeepEval's pytest-style approach aligns with our existing evaluation framework. Its claim-based faithfulness is similar to RAGAS. The G-Eval custom criteria approach is useful for our typed answer correctness.

**Recommendation**: Reference its test organization. Consider its G-Eval approach for custom answer correctness criteria.

---

### 3.3 TruLens

| Dimension | Finding |
|-----------|---------|
| **Problem** | Instrumented tracing + feedback-function evaluation |
| **Core** | RAG Triad: context relevance, groundedness, answer relevance |
| **Groundedness** | LLM judges OR local HuggingFace NLI models |
| **License** | MIT |

**Suitability**: **Reference for tracing**. TruLens's OpenTelemetry-native approach is interesting but our project already has its own tracing (observability tracer). The RAG Triad (context relevance / groundedness / answer relevance) is a clean mental model.

**Recommendation**: Reference its RAG Triad for report organization. Use local NLI model option for faithfulness (matches our existing `guardrails/nli_llm.py`).

---

## 4. Comparison Table

| Benchmark | Type | Pros | Cons | Suitable? | Action |
|-----------|------|------|------|-----------|--------|
| BEIR | Retrieval | Industry standard protocol; 18 datasets | English-centric; domain-generic | Reference | Adopt nDCG@10 protocol |
| MTEB/C-MTEB | Embedding | 500+ tasks; Chinese coverage | Embedding-focused; not RAG | Reference | Use for reranker model selection |
| MIRACL | Retrieval | Annotated negatives; multilingual | Wikipedia domain; not enterprise | Reference | Adopt negative annotation method |
| RAGTruth | Hallucination | Word-level spans; 4-category taxonomy | News/QA domain; LLM-dependent | Reference | Adopt hallucination taxonomy |
| RAGBench | RAG | 3-dimension labels; 100k examples | Gated dataset; GPT-4 labels | Reference | Adopt dimensional evaluation |
| CRUD-RAG | RAG (Chinese) | Chinese; CRUD taxonomy | News domain; 80k docs | Partial | Adopt hallucination probing |
| RGB | RAG | 4-ability framework; bilingual | Pre-assembled contexts; NC license | **Adopt** | **Adopt ability taxonomy** |
| MultiHop-RAG | Multi-hop | 2,556 multi-hop queries; evidence pairs | News domain; ODC-BY | **Adopt** | **Adopt multi-hop taxonomy** |
| RAGAS | Framework | Claim decomposition; noise_sensitivity | Heavy LLM dependency; no citation | **Adopt** | **Adopt claim decomposition + noise metric** |
| DeepEval | Framework | pytest-style; G-Eval custom | LLM-dependent | Reference | Adopt G-Eval approach |
| TruLens | Framework | RAG Triad; local NLI option | Tracing-focused | Reference | Adopt Triad for report structure |

---

## 5. Chinese / Multilingual Reranker Research

### 5.1 Current Reranker Landscape

| Model | Size | Chinese | VRAM (fp16) | Speed | License | Notes |
|-------|------|---------|-------------|-------|---------|-------|
| **bge-reranker-base** (current) | 278M | Strong | ~1-2 GB | Fast | MIT | Oldest gen; superseded by v2-m3 |
| **bge-reranker-v2-m3** | 568M | Strong + 100 langs | ~2-4 GB | Fast | Apache-2.0 | Default choice in Chinese RAG stacks |
| **bge-reranker-v2-gemma** | 2.5B | Good | ~6+ GB | Slow | Apache-2.0 | LLM reranker; needs vLLM |
| **Qwen3-Reranker-0.6B** | 600M | Excellent | ~2 GB | Fast | Apache-2.0 | **Best size/quality; 32k context** |
| **Qwen3-Reranker-4B** | 4B | Excellent | ~9 GB | Moderate | Apache-2.0 | Too large for 1660 Ti |
| **gte-multilingual-reranker-base** | 306M | Strong | ~1-2 GB | Very fast | Apache-2.0 | 8k context; best long-doc encoder |
| **bce-reranker-base_v1** | 279M | Strong zh-en | ~1-2 GB | Fast | Apache-2.0 | Calibrated absolute scores |
| **jina-reranker-v2** | 278M | Good | ~1-2 GB | Fast | **CC-BY-NC** | Non-commercial only |

### 5.2 Hardware Constraints

- **GPU**: GTX 1660 Ti (6 GB VRAM)
- **RAM**: 16 GB
- **Implication**: Max model size ~2-4 GB VRAM → bge-reranker-v2-m3 or Qwen3-Reranker-0.6B

### 5.3 Recommended Upgrade Path

| Priority | Model | Reason |
|----------|-------|--------|
| 1 | **Qwen3-Reranker-0.6B** | Best MTEB-R score (65.8) in size class; 32k context; instruction-aware; Apache-2.0; ~2 GB VRAM |
| 2 | **bge-reranker-v2-m3** | Mature tooling; widely deployed; ~2-4 GB VRAM; Apache-2.0 |
| 3 | **gte-multilingual-reranker-base** | If long documents (>512 tokens) are common; 8k context; very fast |

### 5.4 Validation Requirement

**Do NOT upgrade reranker without ablation evidence.** Current Top-1 = 57.6%. Must first run:
1. Before-rerank vs After-rerank comparison (is reranker helping or hurting?)
2. If hurting: identify why (threshold too aggressive? model quality? score calibration?)
3. If helping but insufficient: test candidate models on our dataset (not just MTEB leaderboard)

---

## 6. Key Takeaways for This Project

### 6.1 What to Adopt

| From | What | How |
|------|------|-----|
| RGB | 4-ability taxonomy | Map to our evaluation matrix dimensions |
| MultiHop-RAG | Multi-hop taxonomy + evidence annotation | Design multi-document ground truth |
| RAGAS | Claim decomposition faithfulness | Implement standalone faithfulness evaluator |
| RAGAS | noise_sensitivity concept | Implement as Precision@K / context noise rate |
| RAGTruth | Hallucination taxonomy | 4-category classification for failure analysis |
| BEIR/MTEB | nDCG@10 as primary retrieval metric | Already have; keep |
| MIRACL | Annotated negative methodology | Design hard negative cases |

### 6.2 What NOT to Import

| What | Why Not |
|------|---------|
| BEIR datasets | English; domain mismatch |
| MIRACL datasets | Wikipedia; not enterprise |
| CRUD-RAG datasets | News domain; 80k docs too large |
| MultiHop-RAG datasets | News domain; English-heavy |
| RAGAS as dependency | Too heavy for CI; LLM cost |
| DeepEval as dependency | LLM cost; different test model |

### 6.3 Evaluation Architecture Inspiration

From studying these benchmarks, the optimal architecture for our project is:

```
External Benchmarks (reference only)
    ↓ Model selection guidance
    ↓ Evaluation protocol design

Project Domain Dataset (core)
    ↓ Enterprise-specific retrieval + generation
    ↓ Multi-hop, table, long-doc coverage

Adversarial Dataset (safety)
    ↓ Negative queries, near-miss, cross-domain
    ↓ Production safety validation

Regression Dataset (continuous)
    ↓ Auto-accumulated from production failures
    ↓ Permanent regression prevention
```
