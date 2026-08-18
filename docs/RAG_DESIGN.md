# RAG_DESIGN — RAG 全链路设计

> 文档入库 → 索引 → 检索 → 重排序 → 校验 → LLM 生成 完整设计。
> 配套阅读：[PRD.md](PRD.md) / [ARCHITECTURE.md](ARCHITECTURE.md) / [AGENT_DESIGN.md](AGENT_DESIGN.md)

---

## 1. 概览

### 1.1 双链路架构

```
        Offline (入库)                              Online (检索)
        ══════════════                              ══════════════

        Documents (PDF/DOCX/MD/TXT)             User Query
              │                                       │
        ┌─────┴─────┐                          ┌────┴─────┐
        │ Loader    │                          │ Query    │
        │ Cleaner   │                          │ Analyzer │
        │ Dedup     │                          │ (intent) │
        │ Chunk     │                          └────┬─────┘
        └─────┬─────┘                               │
              │                                ┌────┴─────┐
        ┌─────┴─────┐                          │ History  │
        │ Metadata │                           │ Aware    │
        │ Pipeline │                           └────┬─────┘
        └─────┬─────┘                                │
              │                                 ┌────┴─────┐
        ┌─────┴─────┐                           │ Multi    │
        │ Embedding │                           │ Query    │
        └─────┬─────┘                           └────┬─────┘
              │                                      │
        ┌─────┴─────┐                           ┌────┴─────┐
        │ ChromaDB  │                           │ ChunkLevel│
        │ (doc+chunk│                           │ Hybrid   │
        │  2 库)   │                           │ (Vec+BM25)│
        └───────────┘                           └────┬─────┘
                                                      │
                                                ┌─────┴─────┐
                                                │ Adaptive  │
                                                │ Expansion │
                                                └─────┬─────┘
                                                      │
                                                ┌─────┴─────┐
                                                │ Rerank    │
                                                │ (CrossEnc)│
                                                └─────┬─────┘
                                                      │
                                                ┌─────┴─────┐
                                                │ LLM       │
                                                │ Generate  │
                                                └───────────┘
```

### 1.2 关键能力一览

| 能力 | 实现 |
|---|---|
| 多知识库 | `kb_id` 隔离（policy / tech / finance / hr / default） |
| 文件类型 | PDF / DOCX / Markdown / TXT |
| 元数据 | doc_type / business_domain / summary / chunk_keywords |
| 引用 | `[1][2]` 内联标注 + 末尾参考文献 |
| 拒答 | Evidence Gate 三层（Retrieval / Rerank / Generation） |
| 自纠 | Self-Correction（LLM 拒答后 query 改写重试） |
| 忠实度 | Faithfulness NLI（拆 claim → 比对 → 剔除） |

---

## 2. 索引链路（9 阶段）

### 2.1 两条入口

| 路径 | 触发 | 入口 |
|---|---|---|
| **A. 全量重建** | `RAGPipeline._init()` | `pipeline.py:_prepare_vector_store()` |
| **B. 增量索引** | 启动 sync / API upload | `indexer.py:IncrementalIndexer.sync()` / `reindex_file()` |

### 2.2 `_index_file()` 9 阶段埋点

```
_index_file()  ← 每个文件一棵 trace 树
  ├─ ① index_load        → 加载，获取文件大小
  ├─ ② index_parse       → PyPDFLoader / Docx2txtLoader / TextLoader
  ├─ ③ index_clean       → DocumentCleaner (11 种清洗)
  ├─ ④ index_dedup       → SHA256 比对，重复则 skip
  ├─ ⑤ index_chunk       → ChunkStrategyRouter + ChunkFilter
  ├─ ⑥ index_metadata    → LLM+规则: 分类/摘要/关键词/实体
  ├─ ⑦ index_embed       → HuggingFaceEmbeddings 逐 chunk 向量化
  ├─ ⑧ index_vector_db   → ChromaKB.add_documents() 写入 chunk 向量库
  └─ ⑨ registry          → DocumentRegistry.register() SQLite 持久化
```

### 2.3 11 种清洗（③）

`DocumentCleaner` 控制字符 / 全角半角 / HTML / PDF 页眉页脚 / 控制符 / Surrogates / 空白合并 / 中文标点统一 / URL / Email / OCR / 独立页码。

### 2.4 分块策略（⑤）

| 文档类型 | 分块策略 | 块大小 |
|---|---|---|
| policy / 合规 / 法律 | ManualPolicy（按章节） | 2000 字 |
| project / 项目报告 | ProjectReport（按章节） | 1500 字 |
| general / 通用 | General（滑动窗口） | 1000 字 (overlap 100) |

默认（无规则命中）：500 字 + 50 overlap。

### 2.5 Metadata Pipeline（⑥）

```
输入：full_text + base_meta
  ↓
Step 1: classify_with_confidence()    → (doc_type, confidence)
Step 2: extract_rule_keywords()       → rule_keywords
Step 3: analyze_complexity()          → complexity dict
Step 4: LLM Decision Router           → llm_decision
Step 5: extract_doc_keywords_llm()    → llm_keywords（条件）
Step 6: 注入 chunk metadata（分层）   → chunk_keywords
Step 7: Span 输出                     → rule_metadata + llm_metadata
```

**LLM Decision Router**（减少 LLM 调用的关键）：

| doc_type | 策略 | 触发条件 |
|---|---|---|
| policy / compliance / legal | `llm_force` | **强制 LLM**（不管分数） |
| faq / product_spec / listing / sop | `rule_first` | score ≥ 50 才调 LLM |
| general 等 | `dual_merge` | score ≥ 50 才调 LLM |

**评分公式**：

```
llm_score = 0
  + 40  如果 doc_type ∈ {policy, compliance, legal}
  + 30  如果 risk_keyword_hits ≥ 3
  + 15  如果 risk_keyword_hits ≥ 1
  + 20  如果 confidence < 0.7
  + 15  如果 10000 < token ≤ 50000
  + 15  如果 structure_score ≥ 20
  不加分 如果 token > 50000（ultra_long 标记）
```

### 2.6 关键词规则管理

| 文件 | 用途 |
|---|---|
| `data/keyword_rules.db` | SQLite 关键词规则库（keyword / doc_type / category / weight） |
| 60s TTL 缓存 | 热加载 |
| `/knowledge/keywords` | 前端按文档类型分组管理 |
| 150 条种子 | `config/rag.py:DEFAULT_KEYWORDS` + `_SEED_DOC_TYPE_MAP` |

---

## 3. 检索链路（6 段流水线）

### 3.1 6 段流水线一图

```
Query
  ↓
┌─────────────────────────┐
│ ① HistoryAware          │  对话历史改写指代/省略
│    CONTEXTUALIZE_PROMPT   │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ ② MultiQuery             │  关键词检测复杂度 → LLM 改写
│    auto / always / off    │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ ③ ChunkLevel Hybrid      │  向量 + BM25 + RRF 60
│    Stage 1: Doc 筛选      │
│    Stage 2: Chunk 检索    │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ ④ Adaptive Expansion     │  Cluster 检测 → 拉全文
│    threshold=0.3          │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ ⑤ Rerank                 │  CrossEncoder + sigmoid 归一化
│    threshold=0.3          │
└────────┬────────────────┘
         ↓
┌─────────────────────────┐
│ ⑥ LLM Generate           │  Citation 强制 + META 注释
│    + Faithfulness 校验    │
└─────────────────────────┘
```

### 3.2 ① HistoryAware

历史感知查询重写：

```python
CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是跨境电商知识库的查询重写助手。
    根据对话历史，将用户问题重新表述为独立的检索查询。
    规则：
    1. 如果用户使用代词（他/她/这个/那个/它），替换为对话历史中的具体实体
    2. 如果问题独立完整，直接返回原问题
    3. 保留所有专有名词 / 技术术语 / 业务词汇
    """),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])
```

### 3.3 ② MultiQuery

**判断逻辑**（`need_multi_query`）：

```python
def need_multi_query(query) -> (bool, reason):
    mode = _mq_mode  # off / always / auto
    if mode == "off": return False, "off"
    if mode in ("always", "on"): return True, mode
    return _is_complex(query)  # 关键词启发 + 长度判断
```

**复杂度启发**（auto 模式）：

- 关键词命中：`分析 / 对比 / 总结 / 流程 / 如何 / 为什么 / 影响`...
- 简单前缀：`什么是 / 多少 / 几点 / 谁 / 哪个`
- 长度：< 5 字 → 简单；> 15 字 → 复杂

**LLM 改写流程**：

```
LLM 改写 → Parse → Normalize（去编号） → Dedup（Jaccard 0.9） → Limit（默认 3）
```

**多路并行检索**：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
with ThreadPoolExecutor(max_workers=min(3, len(queries))) as ex:
    for future in as_completed({ex.submit(retrieve, q): q for q in queries}):
        # 按 chunk_id 去重合并
```

### 3.4 ③ ChunkLevel Hybrid（核心）

#### Stage 1 — Doc 级筛选

```python
# 1. 优先 request metadata_filter（contextvars 注入）
if request_metadata_filter:
    if "person_names" in filter:
        matched_ids = self.person_index[person_names[0]]
        doc_ids = matched_ids
    else:
        doc_ids = []
# 2. 否则人名 / 关键词启发
elif person_names:
    matched_ids = self.person_index[person_name]
elif doc_results := doc_db.similarity_search(query, k=5):
    doc_ids = _filter_docs_by_keywords(query, doc_results)
```

人名倒排索引 (`person_index`) 单独维护：`{person_name: [doc_ids]}`。

#### Stage 2 — Hybrid Retrieval

```python
def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=5, doc_ids=None, rrf_k=60):
    # 并行执行
    with ThreadPoolExecutor(max_workers=2) as ex:
        vf = ex.submit(vector_retriever.retrieve, query, k, doc_ids, metadata_filter)
        bf = ex.submit(bm25_retriever.invoke, query)

    # RRF 融合
    rank_map = {}
    for rank, doc in enumerate(vector_docs, 1):
        cid = doc.metadata.get("chunk_id")
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)
    for rank, doc in enumerate(bm25_docs, 1):
        rank_map[cid] = rank_map.get(cid, 0) + 1 / (rrf_k + rank)

    # 按 RRF 分数排序
    sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)
    return [doc_dict[cid] for cid, _ in sorted_cids[:k]]
```

**关键设计**：

- **并行优于串行** —— 向量和 BM25 互不依赖，2 线程双跑
- **RRF 60** —— 平滑参数，避免单一检索器的极端排名影响
- **Evidence Gate 注入** —— 在 `merged[0].metadata` 写入 Gate 1 决策

### 3.5 ④ Adaptive Expansion

```python
class AdaptiveRetriever(BaseRetriever):
    def _get_relevant_documents(self, query):
        chunks = self.base_retriever.invoke(query)
        doc_counter = Counter(c.metadata.get("doc_id") for c in chunks)

        # 找占比 ≥ threshold 的文档
        clustered = [d for d, c in doc_counter.items() if c / total_chunks >= 0.3]

        if clustered and len(clustered) <= 2:
            # 集中 → 拉全文扩展
            full_docs = self.doc_db.get(where={"doc_id": {"$in": clustered}})
            return full_docs + chunks  # 全文在前
        return chunks  # 分散 → 跳过扩展
```

**关键设计**：

- **集中度阈值 0.3**（`ADAPTIVE_CLUSTER_THRESHOLD`）
- **触发上限 2 个文档**（`ADAPTIVE_MAX_CLUSTER_DOCS`）
- 避免上下文污染（分散时不拉全文）

### 3.6 ⑤ Rerank（CrossEncoder）

```python
from sentence_transformers import CrossEncoder

reranker = CrossEncoder(RERANKER_MODEL_PATH)  # BGE-reranker-base

def rerank(query, docs, top_k=3, threshold=0.3):
    pairs = [(query, doc.page_content) for doc in docs]
    scores = safe_call_with_timeout(reranker.predict, timeout=RERANK_TIMEOUT, sentences=pairs)

    # ⚠️ 关键：sigmoid 归一化（logit [-10, +10] → 0-1）
    scored = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
    scored = [(doc, _sigmoid(score)) for doc, score in scored
              if _sigmoid(score) > RERANK_SCORE_THRESHOLD]
    return scored[:top_k]
```

**关键设计**：

- **sigmoid 归一化** —— `logit` 输出范围 [-10, +10]，直接比 0.3 会过滤掉绝大多数
- **超时控制** —— `safe_call_with_timeout(RERANK_TIMEOUT)`
- **失败回退** —— 返回原始文档 + 0.5 默认分
- **阈值 0.3**（sigmoid 后）—— 经验值 0.2（宽松）~ 0.5（严格）

### 3.7 ⑥ LLM Generate（带引用）

```python
QA_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是跨境电商知识库助手。你**只能**根据下方资料回答问题，**严禁**使用资料之外的知识。

    回答格式：
    1. 正文用 Markdown，分点或分段均可
    2. 每个事实/数据必须标注来源编号 [1][2][3]
       正确：「根据公司规定，报销需在每月5日前提交 [1]」
       错误：「一般来说报销需要5天处理」← 没有引用
    3. 资料中查不到信息时：在正文里写「资料未提及」或「当前知识库暂无相关内容」
    4. 在正文最后，另起一行，输出 HTML 注释包裹的 JSON（**必须放在末尾**）:
       can_answer=true:  <!--META{{"can_answer": true, "citations": [1, 2], "confidence": 0.85}}-->
       can_answer=false: <!--META{{"can_answer": false, "reason": "no_evidence", "confidence": 0.1}}-->
    5. 严禁编造；资料中的数字/日期/名称必须与原文一致
    资料：{context}"""),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])
```

**文档格式**（DOCUMENT_PROMPT）：

```
[文档{index}] 来源: {source_file}
类型: {doc_type}
领域: {business_domain}
摘要: {summary}

{page_content}
```

---

## 4. 知识库存储

### 4.1 抽象层

```python
class KnowledgeStore(ABC):
    @classmethod
    def from_documents(cls, documents, embedding, persist_directory) -> "KnowledgeStore": ...
    @classmethod
    def from_texts(cls, texts, embedding, metadatas, persist_directory) -> "KnowledgeStore": ...
    def similarity_search(self, query, k=5, filter=None) -> list[Document]: ...
    def similarity_search_with_score(self, query, k=5, filter=None) -> list[tuple[Document, float]]: ...
    def add_documents(self, documents) -> list[str]: ...
    def delete(self, ids=None, where=None) -> int: ...
```

**当前实现**：`ChromaKnowledgeStore`（封装 `langchain_chroma.Chroma`）

**预留实现**：`PgVectorKnowledgeStore`（pgvector，后续 PR）

### 4.2 两个 ChromaDB

| 库 | 路径 | 写入 | 用途 |
|---|---|---|---|
| **chunk 级** | `CHROMA_PATH` | `indexer.py:_index_file()` 步骤 8 | 语义检索主体 |
| **doc 级** | `DOC_DB_PATH` | 索引时整篇文档 | Stage 1 召回 + Adaptive 扩展 |

### 4.3 切换路径

业务层只依赖 `KnowledgeStore` 抽象，后续切换到 `PgVectorKnowledgeStore` 仅需：

```
1. 实现 PgVectorKnowledgeStore
2. factory 切换
3. 业务代码无改动
```

---

## 5. Evidence Gate 三层拒答

### 5.1 三层网关

```
                  ┌─────────────────────────┐
                  │   Gate 1: Retrieval      │  召回质量
                  │   VEC_MIN_SCORE=0.2      │  + doc_type 覆盖
                  └────────┬────────────────┘
                           ↓
                  ┌─────────────────────────┐
                  │   Gate 2: Rerank         │  重排序质量
                  │   top1≥0.35 avg≥0.25     │  高风险 0.55
                  └────────┬────────────────┘
                           ↓
                  ┌─────────────────────────┐
                  │   Gate 3: Generation     │  LLM 自报
                  │   META can_answer=false  │
                  └────────┬────────────────┘
                           ↓
                  ┌─────────────────────────┐
                  │   Self-Correction        │  改写 query 重试
                  │   max_retries=1          │
                  └─────────────────────────┘
```

### 5.2 Gate 1 — Retrieval

```python
evidence_gate_retrieval(
    merged_docs,
    query_analysis=qa_result,
    vec_min_score=VEC_MIN_SCORE,                 # 0.2
    require_doc_type_coverage=DOC_TYPE_COVERAGE_REQUIRED,  # True
)
```

**通过条件**：

- 向量分数 ≥ 0.2
- 召回 doc_type 覆盖 QueryAnalyzer 推导的 doc_types

### 5.3 Gate 2 — Rerank

```python
evidence_gate_rerank(
    context_docs,
    intent=self.gate.intent,
    risk_level=self.gate.risk_level,
    min_top1=RERANK_MIN_TOP1,          # 0.35
    min_avg=RERANK_MIN_AVG,            # 0.25
    min_gap=RERANK_MIN_GAP,            # 0.05
    high_risk_min_top1=RERANK_HIGH_RISK_MIN_TOP1,  # 0.55
)
```

**通过条件**（任一不满足即拒）：

- top1 ≥ 0.35（高风险问题 0.55）
- avg ≥ 0.25
- top1 - top2 ≥ 0.05

### 5.4 Gate 3 — Generation（LLM 自报）

LLM 在回答末尾输出 META 注释：

```json
<!--META{"can_answer": false, "reason": "no_evidence", "confidence": 0.1}-->
```

边界 parse 出来后，chain 强制走拒答。

**reason 取值**：

- `no_evidence` — 无相关内容
- `low_relevance` — 相关性不够
- `insufficient` — 证据不足
- `out_of_scope` — 超出业务范围

### 5.5 Self-Correction

Gate 3 拒答后，若 `SELF_CORRECTION_ENABLED=True` 且 `retry_count < 1`：

```python
def _try_self_correct(original_decision, question, ...):
    new_query = self.corrector.try_rewrite(question, reason)
    if new_query is None:
        return None  # 改写失败 → 兜底拒答
    # 用新 query 重跑 pipeline
    result = self._execute(new_query, history)
    return answer if not is_still_rejected else None
```

**限制**：最多 1 次重试（`SELF_CORRECTION_MAX_RETRIES=1`）。

### 5.6 拒答响应

```python
def _reject(self, decision, layer, trace, t_total, self_correction_attempted=False):
    msg, info = build_rejection_response(decision, layer, self_correction_attempted)
    trace.metadata["rejection"] = info.to_dict()
    return msg
```

---

## 6. Faithfulness 校验

> **演进历史**：
> - 早期版本：用 mDeBERTa-v3-base-mnli-xnli 做逐 claim NLI 比对
> - 2026-08-11：切换到 **Qwen LLM-as-Judge** 整体评估（替代 mDeBERTa）
> - 2026-08-12：**移除 mDeBERTa 路径**，LLM-as-Judge 成为唯一引擎
> - 2026-08-10：sanitize_answer 改为仅标记 `[??]*`（废弃自动 rewrite/cite）

### 6.1 检测流程（LLM-as-Judge）

```python
def check_faithfulness(answer: str, context_docs: list) -> FaithfulnessResult:
    # 1. 拆 claim（规则 + 启发，仍是 claim_extractor.extract）
    claims = extract_claims(answer)
    if not claims:
        return FaithfulnessResult(enabled=True)

    # 2. 风险过滤（filter_claims：跳过无意义的 claim）
    high_risk, skip_claims = filter_claims(claims)

    # 3. LLM-as-Judge 整体评估（核心）
    #    1 次 LLM 调用，5-10s 完成，输出 score + unsupported_claims + reason
    verdict = evaluate_with_llm(answer, context_docs)

    # 4. Fallback 保护：LLM 推理失败 → 视为全部支持（不阻塞）
    if verdict.fallback:
        logger.warning(f"[Faithfulness] LLM 推理失败: {verdict.fallback_reason}")
        # 计入 nli_timeout_total 指标
        return FaithfulnessResult(
            score=verdict.score, total_claims=len(claims),
            high_risk_claims=len(high_risk), claims=[],
            enabled=True,
        )

    # 5. 50% 阈值保护（防误判）：
    #    当 unsupported 比例超过 50%，跳过 sanitize，保留原答案
    unsupported_ratio = len(verdict.unsupported_claims) / max(len(high_risk), 1)
    skip_rewrite = unsupported_ratio > FAITHFULNESS_SKIP_THRESHOLD  # 0.5

    # 6. 三级漏斗处理（已简化为仅标记）
    if not skip_rewrite and verdict.unsupported_claims:
        cleaned = sanitize_answer(answer, ...)  # 仅追加 [??]*[存疑，未自动改写]*
    else:
        cleaned = answer

    return FaithfulnessResult(
        score=verdict.score,
        total_claims=len(claims),
        high_risk_claims=len(high_risk),
        supported_claims=len(high_risk) - len(verdict.unsupported_claims),
        unsupported_claims=verdict.unsupported_claims,
        cleaned_answer=cleaned,
        enabled=True,
    )
```

### 6.2 LLM-as-Judge Prompt（`nli_llm.py:JUDGE_PROMPT`）

```
你是一个 RAG 质量评估专家。

判断"LLM 回答"是否完全由"文档"支撑。

规则:
1. 逐句核对：回答中每个事实/数字/政策是否都能在文档中找到对应支撑
2. 文档没有提到但回答里有 → 视为 unsupported claim
3. 回答比文档保守（少说）→ 不算 unsupported
4. 整体评分 (0-1): 1.0=完全支撑, 0.5=部分支持, 0.0=完全不支持

【文档】
{context}

【LLM 回答】
{answer}

请输出 JSON（只输出 JSON，不要其他文字）:
{
  "score": 0.0 到 1.0,
  "reason": "一句话说明判断依据",
  "unsupported_claims": ["未被支撑的句子1", "未被支撑的句子2"]
}
```

### 6.3 配置

```python
ENABLE_FAITHFULNESS = True            # 总开关
NLI_USE_LLM = True                    # LLM-as-Judge 启用（mDeBERTa 路径已移除）
FAITHFULNESS_SKIP_THRESHOLD = 0.5     # unsupported 占比 > 50% 时跳过 sanitize
NLI_LLM_TIMEOUT = 30                  # 推理超时（秒）
NLI_LLM_MAX_CONTEXT_CHARS = 3000      # 喂给 Judge 的字符数上限
NLI_LLM_TEMPERATURE = 0.0             # 评估需要确定性
```

### 6.4 为何放弃 mDeBERTa 改用 LLM

| 维度 | mDeBERTa-v3 | Qwen LLM-as-Judge |
|------|------------|-------------------|
| 调用次数 | 5-10 次/答案（每 claim 一次） | 1 次/答案 |
| 总耗时 | 30s+ | 5-10s |
| 中文能力 | 弱（英文 SOTA，中文需 fine-tune） | 强（原生中文） |
| 可解释性 | label（entailment/neutral/contradiction） | reason（自然语言） |
| 维护成本 | 需独立服务部署 | 复用 LLM 基础设施 |
| 误判率（实测） | 高（90%+ 误判，2026-08 用户日志） | 待长期验证 |

### 6.5 集成位置

`_evaluate()` 在 `_verify()`（META + Citation）之后执行：

```python
def _evaluate(self, answer: str, context_docs: list) -> str:
    result = check_faithfulness(answer, context_docs)
    if result.cleaned_answer != answer:
        return result.cleaned_answer + ref_section
    return answer
```

**Fallback 行为**：LLM 推理失败时（超时/JSON 解析失败），`score=1.0` 视为全部支持，**不阻塞答案生成**。

### 6.6 评测指标（V2.0）

| 指标 | 含义 | 目标值 |
|------|------|--------|
| `judge_score_mae` | Judge score vs 人工标注的 MAE | < 0.15 |
| `judge_unsupported_f1` | 不可信 claim 识别 F1 | ≥ 0.70 |
| `judge_fallback_rate` | JSON 解析失败 / 超时率 | < 5% |
| `judge_latency_p95_ms` | 推理 P95 延迟 | < 10000ms |
| `judge_consistency` | 同一 (answer, context) 多次评分方差 | < 0.10 |

详见 [评测方案 §Faithfulness NLI](../rag_eval/README.md)。

---

## 7. 关键文件索引

### 7.1 索引

| 文件 | 职责 |
|---|---|
| `backend/rag/indexing/indexer.py` | `_index_file()` 9 阶段管线 |
| `backend/rag/indexing/doc_registry.py` | SQLite 文档注册表 |
| `backend/rag/indexing/operation_log.py` | 操作审计日志 |
| `backend/rag/indexing/chunk_store.py` | Chunk 持久化 |
| `backend/rag/preprocessing/loader.py` | 批量加载 |
| `backend/rag/preprocessing/cleaner.py` | 11 种清洗 |
| `backend/rag/preprocessing/chunking.py` | 3 种分块策略 |
| `backend/rag/preprocessing/metadata.py` | 文档分类 + 复杂度 |
| `backend/rag/preprocessing/keyword.py` | 关键词 + LLM Router |
| `backend/rag/preprocessing/keyword_store.py` | SQLite 规则库 + 60s 热加载 |
| `backend/rag/preprocessing/entity.py` | 品牌 / 平台 / 人名提取 |
| `backend/rag/preprocessing/filter.py` | SimHash + PII 脱敏 |
| `backend/rag/preprocessing/llm_enrichment.py` | LLM 摘要 / 实体 |
| `backend/rag/preprocessing/domain_data.py` | 业务词典（KNOWN_PERSON_NAMES / DOC_TYPE_RULES） |
| `backend/rag/tracer.py` | TraceCollector |

### 7.2 检索

| 文件 | 职责 |
|---|---|
| `backend/rag/pipeline.py` | RAGPipeline 入口 + contextvars |
| `backend/rag/chain.py` | 6 段检索链（RAGChain） |
| `backend/rag/retrieval/retrievers.py` | ChunkLevelRetriever + AdaptiveRetriever |
| `backend/rag/retrieval/hybrid.py` | Vector + BM25 + RRF |
| `backend/rag/retrieval/bm25_store.py` | BM25 持久化 + 增量 |
| `backend/rag/retrieval/base.py` | CustomRetriever（ChromaDB filter） |
| `backend/rag/retrieval/query_analyzer.py` | QueryAnalyzer（entities / time / intent） |
| `backend/rag/retrieval/multi_query.py` | MultiQueryRetriever |
| `backend/rag/retrieval/kb_filter.py` | 知识库过滤 |
| `backend/rag/reranker.py` | CrossEncoder Rerank |
| `backend/rag/context.py` | contextvars 协程安全 |
| `backend/rag/citation.py` | CitationFormatter |
| `backend/rag/evidence_gate/controller.py` | EvidenceGateController |
| `backend/rag/evidence_gate/operations.py` | evidence_gate_retrieval / evidence_gate_rerank |
| `backend/rag/evidence_gate/self_correction.py` | SelfCorrectionStrategy |
| `backend/rag/evidence_gate/models.py` | GateDecision / RejectInfo |
| `backend/rag/guardrails/scorer.py` | Faithfulness 评分 |
| `backend/rag/guardrails/claim_extractor.py` | 拆 claim |
| `backend/rag/guardrails/nli_checker.py` | NLI 比对 |
| `backend/rag/guardrails/risk_filter.py` | 风险过滤 |

### 7.3 存储

| 文件 | 职责 |
|---|---|
| `backend/rag/vectorstore/knowledge_store.py` | KnowledgeStore 抽象 + ChromaKnowledgeStore |
| `backend/config/rag.py` | RAG 配置（chunk / rerank / doc_type / gates） |

### 7.4 入口

| 文件 | 职责 |
|---|---|
| `backend/tools/rag.py` | `@tool search_knowledge_tool(question, kb_id)`（被 Multi-Agent 调用） |
| `backend/rag/routing/kb_router.py` | 多知识库路由 |

---

## 验证

最后验证：2026-08-10 · 与代码一致（6 段流水线 + 9 阶段埋点 + Evidence Gate 3 层 + Faithfulness NLI）。
