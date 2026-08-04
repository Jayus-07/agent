# RAG 系统架构文档 — 入库到检索全链路

> 最后更新: 2026-07-19
> 范围: 文档上传 → Metadata Pipeline → 向量入库 → 混合检索 → LLM 回答

## 一、系统架构总览

```
                        Offline (入库)                    Online (检索)
                        ════════════                    ════════════

                        Document                         User Query
                           │                                │
                    ┌──────┴──────┐                  ┌──────┴──────┐
                    │  Parser      │                  │ Query       │
                    │  Cleaner     │                  │ Analyzer    │
                    │  Dedup       │                  └──────┬──────┘
                    │  Chunk       │                         │
                    └──────┬──────┘                  ┌──────┴──────┐
                           │                         │ Metadata    │
                    ┌──────┴──────┐                  │ Filter      │
                    │  Metadata   │                  └──────┬──────┘
                    │  Pipeline   │                         │
                    └──────┬──────┘                  ┌──────┴──────┐
                           │                         │ Hybrid      │
                    ┌──────┴──────┐                  │ Retrieval   │
                    │  Embedding  │                  │ (Vec+BM25)  │
                    └──────┬──────┘                  └──────┬──────┘
                           │                                │
                    ┌──────┴──────┐                  ┌──────┴──────┐
                    │  Vector DB  │                  │  Reranker   │
                    │  (Chroma)   │                  └──────┬──────┘
                    └─────────────┘                         │
                                                    ┌──────┴──────┐
                                                    │  LLM Answer │
                                                    └─────────────┘
```

## 二、入库 Pipeline

### 2.1 入口

```
POST /api/rag/upload
  → upload_document()           保存文件到 data/docs/
  → _run_index_background()     asyncio 后台任务
  → _do_index_sync()           线程池同步索引
  → _index_file()              Trace 根节点
```

### 2.2 Span 树 (8 步)

| 步骤 | Span ID | 类型 | 说明 |
|------|---------|------|------|
| ① | `index_load` | load | 文件大小/扩展名 |
| ② | `index_parse` | parse | PDF/DOCX/MD/TXT 解析 |
| ③ | `index_clean` | clean | DocumentCleaner (11 种清洗) |
| ④ | `index_dedup` | dedup | SHA256 缓存检查 |
| ⑤ | `index_chunk` | chunk | 分块 + ChunkFilter 质量过滤 |
| ⑥ | `index_metadata` | llm | **Metadata Pipeline (核心)** |
| ⑦ | `index_embed` | embedding | 向量嵌入 (成功静默, 失败 child span) |
| ⑧ | `index_vector_db` | vector_db | ChromaDB 写入 |

### 2.3 清洗步骤 (③ clean)

`DocumentCleaner` 11 种操作 (config 开关控制):

- 控制字符去除 (`\x00-\x1f`)
- 非法 Unicode 去除 (surrogates)
- 全角→半角 (数字/字母/标点)
- 空白字符规范化 (`\r\n`→`\n`, `\t`→空格)
- 合并连续空行 (>2→2)
- 中文标点统一 (`,`→`，`)
- HTML 标签剥离
- URL 处理 (删除/占位/保留)
- Email 处理 (删除/占位/保留)
- PDF 页眉去除 (重复行检测)
- PDF 页脚去除 (页码模式)
- OCR 清洗 (P1 占位)
- 独立页码去除

---

## 三、Metadata Pipeline (核心)

### 3.1 整体流程

```
_build_doc_metadata(full_text, base_meta)
  │
  ├── Step 1: classify_with_confidence()    → (doc_type, confidence)
  ├── Step 2: extract_rule_keywords()       → rule_kws_preview
  ├── Step 3: analyze_complexity()          → complexity dict
  ├── Step 4: LLM Decision Router           → llm_decision
  ├── Step 5: extract_doc_keywords_llm()    → llm_keywords (条件)
  ├── Step 6: 注入 chunk metadata (分层)     → chunk_keywords
  └── Step 7: Span 输出                     → rule_metadata + llm_metadata
```

### 3.2 文档分类 (classify_with_confidence)

#### 加权计分

`DOC_TYPE_RULES[(pattern, weight)]` 模式累计:

| 类型 | 示例模式 | 权重 |
|------|---------|------|
| listing | `Listing`, `五点描述`, `A+内容` | 5-10 |
| sop | `SOP`, `标准操作`, `作业指导` | 5-10 |
| ad_policy | `广告政策`, `Amazon Ads`, `竞价策略` | 5-10 |
| faq | `FAQ`, `常见问题`, `退货政策` | 5-10 |
| product_spec | `产品规格`, `使用手册`, `保养指南` | 5-10 |
| policy | `制度`, `管理条例` | 5-10 |
| compliance | `合规`, `GDPR`, `CCPA`, `隐私政策` | 8-10 |
| legal | `合同`, `违约责任`, `知识产权` | 8-10 |

#### 文件名辅助

`FILENAME_TYPE_HINTS`: 文件名命中关键词 → 该类型 +30 分

#### 文件夹路径辅助

`FOLDER_TYPE_HINTS`: 路径命中关键词 → 该类型 +40 分 + confidence +0.3

| 路径关键词 | → 类型 |
|-----------|--------|
| legal/contracts/合同 | legal |
| compliance/法规/regulatory | compliance |
| policy/policies/制度/hr/finance | policy |
| faq/help/常见问题 | faq |
| products/specs/规格 | product_spec |
| sop/operations/流程 | sop |

#### Confidence 计算

```
confidence = top_score / (top_score + second_score)
文件夹命中顶层类型 → +0.3 (上限 1.0)
LLM 仲裁命中 → 0.95
```

#### LLM 胶着仲裁

触发条件: `{legal, compliance, policy}` 中 ≥2 个且得分差 < 5

调轻量 LLM 从候选类型中三选一，避免 policy/compliance/legal 混淆。

### 3.3 风险关键词检测

`analyze_complexity()` 扫描前 3000 字，10 个风险词:

```
合同 | GDPR | 隐私 | 审计 | 监管 | 处罚 | 罚款 | 合规 | 诉讼 | 知识产权
```

命中次数 → `risk_keyword_hits` → 输入 LLM Router 评分。

### 3.4 LLM Decision Router

#### 评分公式

```
llm_score = 0
  + 40  如果 doc_type ∈ {policy, compliance, legal}
  + 30  如果 risk_keyword_hits ≥ 3
  + 15  如果 risk_keyword_hits ≥ 1
  + 20  如果 confidence < 0.7
  + 15  如果 10000 < token ≤ 50000
  + 15  如果 structure_score ≥ 20
  不加分 如果 token > 50000 (ultra_long 标记)
```

#### 决策表

| doc_type | 策略 | 决策方式 |
|----------|------|---------|
| policy | llm_force | **强制 LLM** (不管分数) |
| compliance | llm_force | **强制 LLM** (不管分数) |
| legal | llm_force | **强制 LLM** (不管分数) |
| faq | rule_first | score ≥ 50 → LLM |
| product_spec | rule_first | score ≥ 50 → LLM |
| listing | rule_first | score ≥ 50 → LLM |
| sop | rule_first | score ≥ 50 → LLM |
| general 等 | dual_merge | score ≥ 50 → LLM |

#### 场景推演

```
① /policy/退货制度.md
   doc_type=policy(+40) risk_hits=1(+15) → score=55
   force → LLM ✅
   → keywords: "消费者权益", "无理由退货", "退款流程"

② FAQ_物流.md
   doc_type=faq(0) risk_hits=0(0) → score=0
   0 < 50 → 跳过 ❌
   → 只用规则: "物流", "时效", "美国站"
   → 省 500 Token

③ GDPR合规手册.md (500页)
   doc_type=compliance(+40) risk_hits=5(+30) struct=40(+15) → score=85
   ultra_long 标记 → force → LLM ✅
   → 6000 字截断内提取, 标记供后续优化

④ unknown_general.txt (短文档, 无电商术语)
   doc_type=general(0) risk_hits=0(0) low_conf=0.45(+20) → score=20
   20 < 50 → 跳过 ❌
   → 分类不确定但太短不值得 LLM
```

### 3.5 关键词注入 (分层)

```
文档级 → registry:
  {doc_type, file_hash, doc_db_id, doc_keywords(全量)}

Chunk 级 → ChromaDB metadata:
  ch.metadata["doc_type"]        ← 文档级继承
  ch.metadata["person_names"]    ← 文档级继承
  ch.metadata["chunk_keywords"]  ← 该 chunk 独立提取 (不污染)
```

每个 chunk 跑一遍 `extract_rule_keywords(ch.text, doc_type)`，只存自己命中的词。

### 3.6 Span 输出

```json
{
  "rule_metadata": {
    "doc_type": "compliance",
    "confidence": 0.95,
    "business_domain": "regulatory",
    "person_names": "",
    "complexity": {"token_estimate": 4000, "structure_score": 25, "risk_keyword_hits": 3},
    "keywords_rule": [{"word": "GDPR", "source": "rule"}, ...]
  },
  "llm_metadata": {
    "llm_used": true,
    "llm_strategy": "llm_force",
    "llm_decision": {"llm_score": 85, "llm_reason": "high_value:compliance(+40); risk_hits:5(+30); forced"},
    "llm_tokens": {"prompt_tokens": 450, "completion_tokens": 180},
    "keywords_llm": [{"word": "数据合规", "source": "llm"}, ...]
  }
}
```

---

## 四、关键词规则管理

### 4.1 存储

SQLite `data/keyword_rules.db`，结构:

| 字段 | 说明 |
|------|------|
| keyword | 关键词 |
| doc_type | 归属文档类型 (faq/policy/compliance/general...) |
| category | 业务分类 (商品管理/订单履约/物流追踪...) |
| weight | 权重 (1-10) |
| enabled | 1=启用, 0=禁用 |
| source | seed=种子数据, manual=手动添加 |

### 4.2 热加载

- 60s TTL 缓存
- `get_keywords_for_doc_type(doc_type)` → 该类型专属词 + general 通用词
- 前端页面 `/knowledge/keywords` 按文档类型分组管理
- 修改后 60s 自动生效，无需重启

### 4.3 种子数据

150 条从 `config/rag.py` 的 `DEFAULT_KEYWORDS` + `SIGNAL_RULES` 自动导入，按 `_SEED_DOC_TYPE_MAP` 分配 doc_type。

---

## 五、检索 Pipeline

### 5.1 入口

```
RAGPipeline.ask(question, session_id)
  ├─ _prepare_context()      注入 metadata_filter
  ├─ _check_resources()      资源监控
  ├─ _execute_chain()        6 层链
  └─ _cleanup()              清除 contextvars
```

### 5.2 Query Analyzer

`QueryAnalyzer.analyze(query)` → `ParsedQuery`:

- Entities: persons(品牌/平台), organizations, sku_codes
- Time: time_range_start, time_range_end
- Classification: domains, doc_types
- Intent: entity/order/inventory/ad/fact/report/summary (7 类)

`ParsedQuery.to_metadata_filter()` → ChromaDB filter dict:
```json
{"doc_type": "compliance", "domain": "regulatory", "person_names": "..."}
```

### 5.3 6 层检索链

| 层 | 组件 | 作用 |
|----|------|------|
| 1 | HistoryAware | LLM 改写代词/补全省略 |
| 2 | MultiQuery | 复杂查询 → 3 路并行 → 去重 |
| 3 | ChunkLevel | **核心: Stage1 Doc 筛选 + Stage2 Hybrid** |
| 4 | Adaptive | 结果间距大 → doc 级扩展 |
| 5 | Reranker | CrossEncoder 精排 |
| 6 | LLM Generate | stuff chain + Citation 验证 |

### 5.4 Hybrid Retrieval (第 3 层核心)

```
Stage 1: Doc 级筛选
  · metadata_filter["person_names"] → person_index 反查 doc_ids
  · 或 doc 级 vector search → keyword overlap 过滤
  → 缩小到相关文档集合

Stage 2: Chunk 级 Hybrid
  hybrid_retrieve(query, vector_retriever, bm25, doc_ids, metadata_filter)
    │
    ├─ ThreadPool 并行:
    │   · vector_retriever.retrieve(query, k, doc_ids, metadata_filter)
    │     CustomRetriever → ChromaDB similarity_search
    │     filter={"$and": [{kb_id}, {doc_type}, ...]}
    │
    │   · bm25_retriever.invoke(query)
    │     BM25Retriever (langchain) → 关键词精确匹配
    │     post-hoc 按 doc_ids 过滤
    │
    └─ RRF 融合 (rrf_k=60):
         score = 1/(60+rank_vector) + 1/(60+rank_bm25)
         按 chunk_id 去重 → 取 top_k
```

### 5.5 Keyword 在检索中的作用

```
query: "GDPR合规要求"
  │
  ├─ QueryAnalyzer → doc_type=compliance → metadata_filter
  │
  ├─ Stage 1:
  │    extract_chunk_keywords("GDPR合规要求") → {"GDPR", "合规"}
  │    → doc 级 vetor search → 每 doc 读 chunk_keywords 计算交集
  │    → 过滤到关键词相关的文档
  │
  ├─ Stage 2: Hybrid
  │    ChromaDB filter={"doc_type": "compliance"}
  │    Vector: 语义召回 "数据保护条例"
  │    BM25:   精确匹配 "GDPR" "合规"
  │    RRF 融合 → top 8
  │
  └─ Reranker: CrossEncoder 精排 → top 3
```

---

## 六、Metadata 对检索的三个作用

| 作用 | 机制 | 举例 |
|------|------|------|
| **Filter** | ChromaDB `where` 子句 | `doc_type=compliance` 缩小范围 |
| **Rerank Feature** | keyword overlap 评分 | chunk 有关键词交集的排前面 |
| **Explainability** | 回答引用来源 | "来源: 退货政策.md, 匹配: 退款, 消费者权益" |

---

## 七、关键文件索引

### 入库

| 文件 | 职责 |
|------|------|
| `backend/app/api/routes/rag.py` | 上传 + 后台索引调度 |
| `backend/rag/indexing/indexer.py` | `_index_file_inner` 8-span 管线 |
| `backend/rag/indexing/operation_log.py` | 操作审计日志 (SQLite) |
| `backend/rag/preprocessing/metadata.py` | 文档分类 + 复杂度分析 |
| `backend/rag/preprocessing/keyword.py` | 关键词提取 + LLM Router |
| `backend/rag/preprocessing/keyword_store.py` | 关键词动态存储 + 热加载 |
| `backend/rag/preprocessing/cleaner.py` | 文档清洗 (11 种) |
| `backend/rag/preprocessing/entity.py` | 品牌/平台/人名提取 |
| `backend/rag/preprocessing/chunking.py` | 智能分块策略 |
| `backend/rag/preprocessing/filter.py` | 脏数据过滤 + PII 脱敏 |
| `backend/rag/tracer.py` | Trace/Span 收集器 |
| `backend/config/rag.py` | 规则配置 (DOC_TYPE_RULES, FOLDER_TYPE_HINTS 等) |

### 检索

| 文件 | 职责 |
|------|------|
| `backend/rag/pipeline.py` | RAGPipeline 入口 + contextvars |
| `backend/rag/chain.py` | 6 层检索链 |
| `backend/rag/retrieval/retrievers.py` | ChunkLevelRetriever (Stage1+2) |
| `backend/rag/retrieval/hybrid.py` | Vector+BM25 RRF 融合 |
| `backend/rag/retrieval/bm25_store.py` | BM25 持久化 + 增量更新 |
| `backend/rag/retrieval/base.py` | CustomRetriever (ChromaDB filter) |
| `backend/rag/retrieval/query_analyzer.py` | 查询结构化 + metadata_filter |
| `backend/rag/reranker.py` | CrossEncoder 重排 |
| `backend/rag/retrieval/multi_query.py` | 多路查询改写 |
| `backend/rag/context.py` | contextvars 协程安全 |

### 前端

| 文件 | 职责 |
|------|------|
| `frontend/src/app/knowledge/operations/page.tsx` | 操作中心 (批次折叠) |
| `frontend/src/app/knowledge/operations/traces/[id]/page.tsx` | 文档 Trace 详情 |
| `frontend/src/app/knowledge/keywords/page.tsx` | 关键词规则管理 |
| `frontend/src/app/observability/traces/[id]/page.tsx` | Agent Trace 详情 |
| `frontend/src/services/knowledge.ts` | 知识库 API 层 |
| `frontend/src/types/trace.ts` | Trace/Span 类型定义 |
