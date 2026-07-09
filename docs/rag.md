# RAG 检索管线

> 知识库检索：向量 + BM25 + Reranker + Citation Filter + LLM 答案生成。

## 1. 总览

`retrieval/pipeline.py` (RAGPipeline) 是主入口，协调：

```
向量检索 (ChromaDB) + BM25 关键词检索
  → 混合排序 (RRF) → 切片级筛选
  → BGE-Reranker 重排序
  → Citation Filter 来源验证（CrossEncoder 二次打分）
  → LLM 生成答案（带硬约束）
```

模型路径从 `config.py` 读取（环境变量覆盖），首次请求时自动创建向量库。

## 2. 模块结构

```
retrieval/
├── pipeline.py        # RAGPipeline 主入口（入口 + ask / search）
├── chain.py           # LCEL 链实现 + RAGChain 类
├── retrievers.py      # ChunkLevelRetriever / AdaptiveRetriever
├── hybrid.py          # hybrid_retrieve + rrf_fusion（混合排序）
├── reranker.py        # CrossEncoder (BGE-reranker-base)
├── context.py         # RequestContext (ContextVar，请求级 metadata)
├── query_analyzer.py  # QueryAnalyzer (intent / time / person 提取)
├── bm25.py            # BM25 索引构建
├── base.py            # CustomRetriever (基类)
└── metrics.py         # RetrievalMetrics (监控指标)
```

## 3. 入口方法

`RAGPipeline` 提供两个方法（生产路径仅 `ask`）：

| 方法 | 调用方 | 行为 |
|---|---|---|
| `ask(question, kb_id, ...)` | `api/routes/rag.py` + `multi_agent/tools.py` | 简单入口，**不**走 QueryAnalyzer 也不注 person_names |
| `search(question, ...)` | (无外部调用) | 完整版，走 QueryAnalyzer + RequestContext + person_names 注入 |

**注意**：`search()` 是"完整版"但**没有任何外部调用方**。`multi_agent/tools.py:search_knowledge_tool` 走 `ask()`，导致 worker 路径的检索质量结构性低于 REST API。

## 4. 检索流程（ask 路径）

```
RAGPipeline.ask(question, kb_id)
  1. ask_user_clarification (可选，未启用)
  2. _load_chromadb(kb_id)           # 加载向量库
  3. _init_bm25()                     # 构建 BM25 索引
  4. _init_retrievers()               # 构造 ChunkLevelRetriever + AdaptiveRetriever
  5. chain.search(query)
     ├─ QueryAnalyzer 提取 intent / person / time
     ├─ ChunkLevelRetriever.invoke(query)
     │   ├─ Stage 1: doc 级检索（person_name → 倒排索引）
     │   └─ Stage 2: chunk 级检索（multi_query + hybrid_retrieve）
     ├─ AdaptiveRetriever（按需文档补全）
     ├─ RerankCompressor（CrossEncoder 重排序）
     ├─ Citation Filter（再次 CrossEncoder 打分 + 折叠低分）
     └─ ParallelMultiQueryRetriever
  6. LLM 摘要生成（带硬校验）
```

## 5. 关键模块

### 5.1 ChunkLevelRetriever (`retrieval/retrievers.py`)

两阶段检索：Doc → Chunk + multi-query + hybrid + rerank。

- **Stage 1（doc 级）**：
  - 检查 `RequestContext.metadata_filter`（请求级 scope 限制）
  - 提取 `extract_person_names(query)` 匹配 `person_index` 倒排索引
  - 无 metadata_filter → similarity_search + 关键词过滤
- **Stage 2（chunk 级）**：
  - `hybrid_retrieve(q, chunk_retriever, bm25, k=HYBRID_SEARCH_K, ...)`
  - RRF 融合多 query 结果（去重 chunk_id）

### 5.2 AdaptiveRetriever (`retrieval/retrievers.py`)

两阶段自适应检索：
1. 通过 base_retriever 做 chunk 级检索
2. 检查 top chunks 的 doc_id 分布：
   - 集中在 ≤ `ADAPTIVE_MAX_CLUSTER_DOCS` 个文档中 → 补全文档全文
   - 分散 → 只返回 chunks（避免上下文污染）

### 5.3 hybrid_retrieve (`retrieval/hybrid.py`)

混合检索：向量 + BM25 + RRF 融合。

```python
def hybrid_retrieve(query, vector_retriever, bm25_retriever, k=20, doc_ids=None, metadata_filter=None):
    vector_docs = vector_retriever.invoke(query) if not doc_ids else ...
    bm25_docs = bm25_retriever.invoke(query)
    return rrf_fusion_docs(vector_docs, bm25_docs, k=k)
```

RRF 公式：`1 / (rrf_k + rank)`，融合 rank 而非 score。

### 5.4 RerankCompressor (`retrieval/reranker.py`)

使用 BGE-reranker-base (`CrossEncoder`) 对 chunk 重新打分，保留 `RERANK_TOP_K` 个。

### 5.5 Citation Filter (在 `chain.py`)

**关键安全机制**：chunk 支撑答案的最低 CrossEncoder 分数（`CITATION_SUPPORT_THRESHOLD = 0.4`，**高于**检索阈值 `0.3`）。

低于阈值的输出被 `<details>` 折叠 + 标记 "未支撑"，**不让 LLM 引用未支撑的内容**。

### 5.6 ParallelMultiQueryRetriever (`retrieval/chain.py`)

用 LLM 生成多角度查询 → **并发检索** → 去重 chunk_id → 提升召回率。

### 5.7 QueryAnalyzer (`retrieval/query_analyzer.py`)

分析 query 意图：
- `intent` (data / knowledge / report / chat)
- `persons`（从 query 提取人名，注入 metadata_filter）
- `time`（时间归一化）
- `kb_id` 推断

### 5.8 RequestContext (`retrieval/context.py`)

`ContextVar` 实现的请求级元数据（`person_names`, `metadata_filter`），通过 `set_context` / `get_context` 跨函数共享。

## 6. 关键配置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `EMBEDDING_MODEL_PATH` | BAAI/bge-small-zh-v1.5 | Embedding 模型本地路径 |
| `RERANKER_MODEL_PATH` | BAAI/bge-reranker-base | Reranker 模型本地路径 |
| `BM25_SEARCH_K` | 20 | BM25 召回数 |
| `HYBRID_SEARCH_K` | 20 | 混合检索融合后数量 |
| `RERANK_TOP_K` | 8 | Reranker 保留数 |
| `RERANK_SCORE_THRESHOLD` | 0.3 | Reranker 最低分 |
| `CITATION_SUPPORT_THRESHOLD` | 0.4 | Citation Filter 最低分（更严格） |
| `CHROMA_PATH` | data/chroma | ChromaDB 持久化路径 |
| `DOC_DB_PATH` | data/doc_db | 文档级 ChromaDB 路径 |
| `DOCS_DIRECTORY` | data/docs | 文档源目录 |
| `ADAPTIVE_CLUSTER_THRESHOLD` | 0.3 | 单文档占比触发补全 |
| `ADAPTIVE_MAX_CLUSTER_DOCS` | 2 | 补全的最大文档数 |
| `ENABLE_HISTORY_AWARE_RETRIEVAL` | true | 是否使用历史上下文 |

## 7. 文档预处理集成

RAG 管线在 ingestion 阶段（首次）自动调用 `preprocessing/`：

```
preprocessing/loader.py
  → load_documents_from_directory()  # 多格式（PDF/Word/Markdown）
preprocessing/chunking.py
  → ChunkStrategyRouter              # 类型感知分块（manual/policy/resume/project_report/general）
preprocessing/metadata.py
  → build_all_metadata_async()      # 章节/时间/域/实体/关键词
preprocessing/keyword.py
  → extract_chunk_keywords()         # 关键词 + jieba
preprocessing/entity.py
  → extract_person_names()           # 人名（硬编码 4 个 + LLM 提取）
```

详细分块策略见 `chunking.py:ChunkStrategyRouter`。

## 8. 修改指南

- **改 Embedding 模型**：替换 `EMBEDDING_MODEL_PATH` + 清空 `data/chroma/`
- **改 Reranker 阈值**：`RERANK_SCORE_THRESHOLD`（注意与 `CITATION_SUPPORT_THRESHOLD` 区分）
- **加新检索策略**：在 `retrievers.py` 加新 BaseRetriever 子类
- **改 Citation Filter 严格度**：`CITATION_SUPPORT_THRESHOLD`（越高越严格）
- **关 AdaptiveRetriever**：在 `pipeline.py:_init_retrievers` 注释掉 AdaptiveRetriever 包装
- **改 BM25 索引**：`pipeline.py` 在 `_init` 时调用 `build_bm25_retriever(self.docs, k=...)`，**每次启动重建**（O(N)），大量文档会拖慢

## 9. 已知问题 / 待优化

- `search()` 方法**无外部调用**（0 references），可考虑删除或挂路由
- `RAGPipeline._init` 每次启动**全量重建 BM25**（无持久化）
- `multi_agent/tools.py` 与 `api/deps.py` 各自有惰性单例（无锁，可能重复加载 embedding）
- `chunking.py` / `metadata.py` / `query_analyzer.py` 中三处章节正则实现，规则分散
