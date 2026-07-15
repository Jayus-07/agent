# RAG 全链路文档

## 一、文档上传（一次性）

用户通过知识库管理页面上传 `.md` / `.txt` / `.pdf` / `.docx` 文件。

```
前端 UploadDialog
  → POST /rag/upload (FormData)
  → api/routes/rag.py  upload_document()
    → 保存到 data/docs/ 目录
    → IncrementalIndexer.sync()  触发增量同步
```

## 二、入库 Pipeline（一次性，每个文档）

在 `retrieval/pipeline.py` RAGPipeline._init() 中完成。首次启动或文档变更时触发。

### 2.1 文档加载 _load_and_chunk()

```
load_documents_from_directory(DOCS_DIRECTORY)
  ├── 遍历 data/docs/ 目录
  ├── 按扩展名选择 Loader:
  │     .txt → TextLoader(encoding="utf-8")
  │     .md  → TextLoader(encoding="utf-8")
  │     .pdf → PyPDFLoader
  │     .docx→ Docx2txtLoader
  ├── loader.load() → List[Document]
  │
  ├── 【清洗】 DocumentCleaner.clean(text, source_type)
  │     预处理/cleaner.py
  │     ├── _remove_control_chars()     去除 \x00-\x1f
  │     ├── _remove_surrogates()        去除非法 Unicode
  │     ├── _normalize_fullwidth()      全角半角统一
  │     ├── _normalize_whitespace()     \r\n→\n, \t→空格
  │     ├── _merge_blank_lines()        >2空行→2空行
  │     ├── _unify_cn_punctuation()     中英文标点统一
  │     ├── _strip_html()               去除 HTML 标签
  │     ├── _handle_urls()              URL→[URL]占位
  │     ├── _remove_pdf_headers()       PDF页眉检测+去除
  │     ├── _remove_pdf_footers()       PDF页脚检测+去除
  │     └── _remove_page_numbers()      去除独立页码行
  │     配置: 全部默认 false，需在 .env 中开启
  │
  └── split_documents(docs, file_path)
        → ChunkStrategyRouter.route()
```

### 2.2 类型判断 classify_doc_type()

```
预处理/metadata.py  classify_doc_type(full_text)
  ├── 遍历 DOC_TYPE_RULES 字典
  ├── 正则匹配关键词:
  │     "SOP" → sop
  │     "制度"/"规范"/"手册" → policy
  │     "Listing"/"A+"/"关键词策略" → listing
  │     "FAQ"/"常见问题" → faq
  │     "ROI"/"广告"/"千川" → ad_policy
  │     "产品规格"/"参数" → product_spec
  │     "培训"/"课程" → training
  └── 未匹配 → general
```

### 2.3 切片 ChunkStrategyRouter.route()

```
预处理/chunking.py  ChunkStrategyRouter
  │
  ├── doc_type ∈ {manual, policy, sop, ad_policy, product_spec}
  │     → ManualPolicyChunkStrategy
  │        _find_sections() 找标题:
  │          ├── Markdown: /^(#{1,3})\s+(.+)$/  (## 一、xxx, ### 1.1 xxx)
  │          └── 中文编号: /一、|二、|1.|1)|第X章/
  │        在每个标题边界切开，保持章节完整性
  │        不设最大长度限制
  │
  ├── doc_type ∈ {listing, faq, training}
  │     → ProjectReportChunkStrategy
  │        同 ManualPolicy，但单段超过 PROJECT_CHUNK_SIZE(1500) 时子切分
  │
  └── doc_type = general (fallback)
        → GeneralChunkStrategy
           RecursiveCharacterTextSplitter
           按 \n\n → \n → 。 → . 逐级切分
           GENERAL_CHUNK_SIZE=1000, OVERLAP=100
```

### 2.4 脏数据过滤

```
预处理/filter.py  ChunkFilter.should_keep(text, metadata)
  ├── 空白检查 → 拒绝
  ├── 长度检查 (min=10) → 拒绝
  ├── 符号比率 (>0.8) → 拒绝
  ├── 中文占比 (<0.3) → 拒绝
  ├── SimHash 去重 (阈值=3) → 拒绝
  └── PII 脱敏 (默认关闭)
        ├── 手机号: 138****5678
        ├── 身份证: 110101********1234
        └── 银行卡: 6222****1234

  metadata["filter_status"] = "clean" | "filtered"
  metadata["filter_reason"] = "empty" | "too_short" | "all_symbols" | ...
```

### 2.5 Metadata 提取

```
预处理/metadata.py  build_all_metadata_async(docs, doc_map)
  每个 Chunk 的 metadata:
    ├── doc_id          MD5(文件名)[:10]
    ├── chunk_id        {doc_id}_{index}
    ├── source_file     文件名
    ├── file_path       完整路径
    ├── kb_id           目录名
    ├── doc_type        classify_doc_type()
    ├── business_domain  detect_business_domain()  9个电商领域
    ├── time_refs        extract_time_refs()        ISO日期
    ├── keywords         extract_chunk_keywords()   jieba + 电商词典
    ├── sections         extract_sections()         章节标题
    ├── person_names     extract_person_names()     品牌/平台名
    └── summary          LLM生成(仅resume/report类型)
```

### 2.6 向量化 + 入库

```
HuggingFaceEmbeddings("bge-small-zh-v1.5") → 768维向量

ChromaKnowledgeStore.from_documents(docs, embedding)
  ├── Chunk 级: data/chroma/     (每个 chunk 一条向量)
  └── Doc 级:   data/doc_db/     (每个文档一条向量)

IncrementalIndexer (增量同步)
  ├── _scan_disk()      → {path: (sha256, size, mtime)}
  ├── _compute_delta()  → ADDED / MODIFIED / DELETED / UNCHANGED
  └── _apply_delta()    → 逐文件处理

DocumentRegistry (SQLite)
  ├── file_path (PK), file_name, kb_id, doc_id
  ├── file_hash, file_size, chunk_count, chunk_ids
  └── status: uploading/parsing/embedding/active/failed/deleted
```

### 2.7 BM25 索引

```
retrieval/bm25_store.py  BM25Store
  ├── build(docs, k=BM25_SEARCH_K=10)
  │     → BM25Retriever.from_documents(docs)
  │     → pickle 持久化: data/bm25/corpus.pkl + docs.pkl + meta.json
  └── load()
        → 从磁盘加载，避免每次启动重建
```

## 三、查询 Pipeline（每次提问）

用户在前端 Agent 对话页输入问题。

### 3.0 前端 → 后端

```
ChatInput.tsx
  → POST /chat/stream {"question":"退货流程是什么？","session_id":"xxx"}
  → api/routes/chat.py
  → MultiAgentSystem.ask()
  → Planner → Supervisor → RAG Skill
  → search_knowledge_tool(question)
  → pipeline.ask(question, session_id)
  → retrieval/pipeline.py  RAGPipeline.ask()
  → self.lc_chain.ask(question, session_id)
  → retrieval/chain.py  RAGChain.ask()
```

### 3.1 Trace 初始化

```python
trace_collector.start(question, session_id)
  → TraceRecord(
      id="a1b2c3d4",
      request_id="a1b2c3d4",
      timestamp="2026-07-15T10:22:16Z",
      session_id="multi-agent-default",
      question="退货流程是什么？"
    )
```

### 3.2 ▶ mq_check — MultiQuery 判断

```python
# 位置: retrieval/chain.py  RAGChain.ask()
trace_collector._start("mq_check")

MultiQueryRetriever._should_use_multi(query)
  → MULTI_QUERY_MODE = "auto"
  → _is_complex("退货流程是什么？")
      → COMPLEX_PATTERNS = ["分析","对比","流程","怎么","原因",...]
      → 命中 "流程" → (True, "关键词: 流程")

trace_collector._end("mq_check", "MultiQuery",
  status="success",
  metrics={"triggered": true, "mode": "auto", "variants": 0, "filtered": 0}
)
```

### 3.3 ▶ query_rewrite — LLM改写（仅复杂问题）

```python
# 位置: retrieval/multi_query.py  _rewrite()
trace_collector._start("query_rewrite")

prompt = QUERY_REWRITE_PROMPT.format(count=3, question="退货流程是什么？")
llm.invoke([HumanMessage(prompt)])        # ← LLM #1
  → DeepSeek API
  → ChatOpenAI.invoke()
  → AIMessage("退货流程是什么？\n退款退货流程\n退货步骤")

Proxy._record_tokens(result)
  → response_metadata.token_usage
  → {prompt_tokens: 85, completion_tokens: 149, total_tokens: 234}

_filter(queries)
  → Jaccard 去重 (similarity_threshold=0.9)
  → 最小长度检查 (min_length=3)

trace_collector._end("query_rewrite", "LLM改写",
  metrics={"variants": 3, "prompt_tokens": 85, "completion_tokens": 149, "total_tokens": 234}
)
```

### 3.4 并发检索

```python
# 3个查询变体 → ThreadPoolExecutor(3) → 并发调用 ChunkLevelRetriever
ThreadPoolExecutor.submit(ChunkLevelRetriever.invoke, query)
```

### 3.5 ▶ retrieval — Chunk 级检索

```python
# 位置: retrieval/retrievers.py  ChunkLevelRetriever._get_relevant_documents()
trace_collector._start("retrieval")

# Stage 1: Doc 级检索
doc_db.similarity_search(query, k=5)
  → ChromaDB embedding → cosine_similarity → top5
  → _filter_docs_by_keywords()
      → jieba 关键词提取
      → 与 doc metadata 关键词比对
      → doc_ids = ["8bc680c6c2", "4ef66c5c4e"]

# Stage 2: Chunk 级检索 → hybrid_retrieve()
```

### 3.6 ▶ hybrid_retrieval — 混合检索

```python
# 位置: retrieval/hybrid.py  hybrid_retrieve()
trace_collector._start("hybrid_retrieval")

vector_retriever.retrieve(query, k=8, doc_ids=[...])
  → CustomRetriever → vectordb.similarity_search_with_score()
  → ChromaDB: embedding(query) → cosine → top8
  → 16 个候选 (vector_hits)

bm25_retriever.invoke(query)
  → BM25Okapi.get_scores(query)
  → 0 个 (bm25_hits)

# RRF 融合
for rank, doc in enumerate(vector_docs):
    score = 1 / (60 + rank)
for rank, doc in enumerate(bm25_docs):
    score += 1 / (60 + rank)
→ 排序 → 取 top k
→ 6 个 merged (merged_hits)

trace_collector._end("hybrid_retrieval", "混合检索",
  metrics={"vector_hits": 16, "bm25_hits": 0, "merged_hits": 6}
)
```

### 3.7 去重 + Adaptive

```python
# 多查询结果合并 → chunk_id 去重 → 4 个 unique docs

AdaptiveRetriever._get_relevant_documents()
  → 统计 doc_id 分布
  → 集中在 1-2 个文档? → 补全文档全文
  → 分散? → 直接用 chunks
  → 最终: 10 个候选
```

### 3.8 ▶ rerank — CrossEncoder 重排

```python
# 位置: retrieval/reranker.py  RerankCompressor.compress_documents()
trace_collector._start("rerank")

pairs = [(query, doc.page_content[:800]) for doc in documents]   # 10 对
scores = CrossEncoder.predict(sentences=pairs)                   # bge-reranker-base, 本地推理
filtered = [doc for doc,score in zip(docs,scores) if score > 0.3]  # RERANK_SCORE_THRESHOLD
result = filtered[:5]                                            # RERANK_TOP_K

trace_collector._end("rerank", "Rerank",
  metrics={"input_docs": 10, "output_docs": 5, "threshold": 0.3}
)
```

### 3.9 ▶ llm_generate — LLM 答案生成

```python
# 位置: retrieval/chain.py  _timed_stuff()
trace_collector._start("llm_generate")

# 1. _index_docs: 给 5 个 doc 的 metadata["index"] = 1~5
# 2. format_docs: 拼成 context 字符串
# 3. QA_PROMPT:
#      System: "只根据下方资料回答，严禁使用资料之外的知识。
#              每个事实必须标注来源 [1][2]"
#      context: "[1] 售后流程.md\n退款审核必须24小时内完成...\n---\n[2] ..."
#      Human: "退货流程是什么？"

llm.invoke(messages)                       # ← LLM #2
  → DeepSeek API → ChatOpenAI.invoke()
  → AIMessage("根据资料，退货流程如下...")

Proxy._record_tokens(result)
  → {prompt_tokens: 171, completion_tokens: 152, total_tokens: 323}

trace_collector._end("llm_generate", "LLM生成",
  metrics={"prompt_tokens": 171, "completion_tokens": 152, "total_tokens": 323}
)
```

### 3.10 ▶ citation — 引用验证

```python
# 位置: retrieval/chain.py  _verify_support()
trace_collector._start("citation")

# 复用 Rerank 分数（不重新跑 CrossEncoder）
for doc in context_docs:
    score = doc.metadata.get("rerank_score", 0.5)
    if score > 0.4:  # CITATION_SUPPORT_THRESHOLD
        verified.append(doc)

trace_collector._end("citation", "Citation",
  metrics={"verified_citations": 2, "total_citations": 5}
)
```

### 3.11 后处理

```python
_strip_think_blocks(answer)     # 去掉 <think>...</think>
_format_references(verified)     # 生成 "### 参考文献\n1. **x.md**"
answer = answer + references
# 返回给前端
```

### 3.12 Trace 归档

```python
trace_collector.finish(trace, answer, total_ms, model, provider)
  → record.model = "deepseek-v4-flash"
  → record.provider = "deepseek"
  → 计算 duration_ratio = step.duration_ms / total_ms
  → 聚合 usage = sum(all steps)
  → deque.appendleft(record)
```

## 四、Trace 读取（前端可观测中心）

```
前端 Agent Trace 页面打开
  → GET /observability/rag-traces?limit=50
  → GET /observability/rag-traces/stream  (SSE)
  → trace_collector.list(50)
  → 返回:
      {
        "id": "a1b2c3d4",
        "request_id": "a1b2c3d4",
        "timestamp": "2026-07-15T10:22:16Z",
        "session_id": "multi-agent-default",
        "model": {"name": "deepseek-v4-flash", "provider": "deepseek"},
        "question": "退货流程是什么？",
        "duration_ms": 6805,
        "usage": {"prompt_tokens": 256, "completion_tokens": 301, "total_tokens": 557},
        "steps": [
          {"id":"mq_check",        "label":"MultiQuery",  "status":"success",   "duration_ms":0,    "metrics":{"triggered":true, "mode":"auto", "variants":3, "filtered":3}},
          {"id":"query_rewrite",   "label":"LLM改写",     "status":"success",   "duration_ms":2487, "metrics":{"variants":3,"prompt_tokens":85,"completion_tokens":149,"total_tokens":234}},
          {"id":"hybrid_retrieval","label":"混合检索",     "status":"success",   "duration_ms":267,  "metrics":{"vector_hits":16,"bm25_hits":0,"merged_hits":6}},
          {"id":"retrieval",       "label":"检索",        "status":"success",   "duration_ms":1475, "metrics":{"retrieved_chunks":6}},
          {"id":"rerank",          "label":"Rerank",       "status":"success",   "duration_ms":725,  "metrics":{"input_docs":10,"output_docs":5,"threshold":0.3}},
          {"id":"llm_generate",    "label":"LLM生成",     "status":"success",   "duration_ms":2279, "metrics":{"prompt_tokens":171,"completion_tokens":152,"total_tokens":323}},
          {"id":"citation",        "label":"Citation",     "status":"success",   "duration_ms":0,    "metrics":{"verified_citations":2,"total_citations":5}}
        ]
      }
```

## 五、埋点位置速查

| 步骤 | Step ID | 文件 | 函数 |
|------|---------|------|------|
| MultiQuery 判断 | mq_check | retrieval/chain.py | RAGChain.ask() |
| LLM 改写 | query_rewrite | retrieval/multi_query.py | _rewrite() |
| 混合检索 | hybrid_retrieval | retrieval/hybrid.py | hybrid_retrieve() |
| Chunk 检索 | retrieval | retrieval/retrievers.py | ChunkLevelRetriever._get_relevant_documents() |
| Rerank 重排 | rerank | retrieval/reranker.py | RerankCompressor.compress_documents() |
| LLM 生成 | llm_generate | retrieval/chain.py | _timed_stuff() |
| Citation 验证 | citation | retrieval/chain.py | _verify_support() |

## 六、核心配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| LLM_MODEL | deepseek-v4-flash | 默认大模型 |
| LLM_TEMPERATURE | 0.1 | 生成温度 |
| LLM_CONTEXT_LENGTH | 8192 | 上下文窗口 |
| CHUNK_SIZE | 300 | 默认切片大小 |
| CHUNK_OVERLAP | 30 | 切片重叠 |
| HYBRID_SEARCH_K | 8 | 混合检索返回数 |
| BM25_SEARCH_K | 10 | BM25 检索返回数 |
| RERANK_TOP_K | 5 | Rerank 后返回数 |
| RERANK_SCORE_THRESHOLD | 0.3 | Rerank 分数阈值 |
| CITATION_SUPPORT_THRESHOLD | 0.4 | 引用支撑阈值 |
| MULTI_QUERY_MODE | auto | MultiQuery 模式(auto/on/off) |
| MULTI_QUERY_COUNT | 3 | 查询变体数 |
| MULTI_QUERY_TOP_K_PER | 5 | 每变体检索数 |
| MULTI_QUERY_SIMILARITY | 0.9 | Jaccard 去重阈值 |
| MAX_CONCURRENT_REQUESTS | 4 | 最大并发请求数 |
