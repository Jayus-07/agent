# 🚀 RAG 置信度提升 - 分阶段实施方案

## 📊 **执行优先级**

| 阶段 | 目标 | 预期提升 | 时间成本 | 风险等级 | 优先级 |
|------|------|---------|---------|----------|--------|
| **P1** | 阈值调优 + 日志监控 | +15% 准确率 | 1 天 | ⭐低 | 🔴 立即 |
| **P2** | 三路径召回扩展 | +25% 召回率 | 3 天 | ⭐⭐中 | 🟡 本周 |
| **P3** | LLM-as-Judge 证据门控 | +20% 拒答精度 | 2 天 | ⭐中 | 🟢 下周 |
| **P4** | 自纠正查询重写 | +10% 复杂问题成功率 | 5 天 | ⭐⭐⭐高 | 🟡 下月 |

---

## 🎯 **Phase 1: 阈值调优与监控 (立即执行)**

### 1.1 动态阈值配置 (`config/rag.py`)

```python
# backend/config/rag.py::L176-196 现有配置扩展

# ⭐ 新增：自适应阈值矩阵
ADAPTIVE_VEC_THRESHOLDS = {
    "faq": 0.35,          # FAQ 售后 → 严格防幻觉
    "policy": 0.30,       # 制度规则 → 中等严格  
    "financial": 0.30,    # 财务 → 精确匹配
    "legal": 0.32,        # 法律 → 高准确度要求
    "general": 0.20,      # 通用场景 → 较宽松
}

# 高风险问题额外门槛
RISK_TYPE_THRESHOLDS = {
    "financial_query": 0.45,   # 财务指标查询
    "compliance_check": 0.50,  # 合规检查
    "customer_data_access": 0.40,  # 客户数据访问
}

# ⭐ 查询复杂度评估函数 (新增)
def assess_query_complexity(query: str) -> dict:
    """
    复杂度分级：
    - simple: <15 字，单主题
    - medium: 15-30 字，双主题混合
    - complex: >30 字或含多个实体
    
    Returns: {level, expected_threshold, recommended_k}
    """
    import re
    
    char_count = len(query)
    
    # 实体数量统计
    entities = []
    if re.search(r'\d{4}-\d{2}-\d{2}', query):
        entities.append("date")
    if re.search(r'¥\d+(,\d{3})*', query):
        entities.append("amount")
    if re.findall(r'[A-Z]{2,}|[a-z]{4,}', query):
        entities.append("acronym")
    
    # 复杂度判断
    if char_count < 15 and len(entities) <= 1:
        return {"level": "simple", "threshold": 0.25, "k_multiplier": 1.0}
    elif char_count < 30 and len(entities) <= 2:
        return {"level": "medium", "threshold": 0.30, "k_multiplier": 1.2}
    else:
        return {"level": "complex", "threshold": 0.35, "k_multiplier": 1.5}


# ⭐ 置信度评分聚合器 (新增)
class ConfidenceAggregator:
    """
    多路召回结果的综合置信度评分
    - 向量相似度 (40%)
    - BM25 分数 (30%)
    - 文档类型匹配度 (20%)
    - 实体覆盖度 (10%)
    """
    
    def __init__(self):
        self.weights = {
            "vector_sim": 0.40,
            "bm25_score": 0.30,
            "doc_type_match": 0.20,
            "entity_coverage": 0.10,
        }
    
    def aggregate(self, docs: list, query: str) -> float:
        """计算整体置信度"""
        scores = {}
        
        # 1. 向量相似度平均分
        vector_scores = [getattr(d, "score", 0) for d in docs[:5]]
        scores["vector_sim"] = sum(vector_scores) / len(vector_scores) if vector_scores else 0
        
        # 2. BM25 分数 (需要从 metadata 提取)
        bm25_scores = [d.metadata.get("bm25_score", 0) for d in docs[:5]]
        scores["bm25_score"] = sum(bm25_scores) / len(bm25_scores) if bm25_scores else 0
        
        # 3. 文档类型匹配度
        query_analysis = self._analyze_query_doc_types(query)
        doc_types_found = set(d.metadata.get("doc_type", "") for d in docs[:5])
        scores["doc_type_match"] = self._calc_doc_type_overlap(query_analysis, doc_types_found)
        
        # 4. 实体覆盖度
        query_entities = self._extract_entities(query)
        retrieved_entities = self._extract_retrieved_entities(docs)
        scores["entity_coverage"] = self._calc_entity_coverage(query_entities, retrieved_entities)
        
        # 加权总分
        total = sum(scores[k] * w for k, w in self.weights.items())
        
        return min(total, 1.0)  # 归一化到 0-1
    
    def _analyze_query_doc_types(self, query: str) -> set:
        """从 QueryAnalyzer 提取期望的 doc_types"""
        try:
            from backend.rag.retrieval.query_analyzer import QueryAnalyzer
            analysis = QueryAnalyzer().analyze(query)
            return set(analysis.doc_types)
        except Exception:
            return set()
    
    def _calc_doc_type_overlap(self, query_types: set, found_types: set) -> float:
        """计算文档类型覆盖率"""
        if not query_types:
            return 1.0  # 无特定类型要求 → 满分
        overlap = len(query_types & found_types)
        return overlap / len(query_types)
    
    def _extract_entities(self, text: str) -> set:
        """提取文本中的关键实体"""
        import re
        
        entities = set()
        # 数字实体 (金额、日期等)
        numbers = re.findall(r'\d+', text)
        entities.update(numbers)
        
        # 专有名词 (大写字母组合)
        acronyms = re.findall(r'[A-Z]{2,}', text)
        entities.update(acronyms)
        
        # 中文关键词
        chinese_kws = re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', text)
        entities.update(chinese_kws)
        
        return entities
    
    def _extract_retrieved_entities(self, docs: list) -> set:
        """从检索结果中提取实体"""
        all_text = " ".join([d.page_content for d in docs[:5]])
        return self._extract_entities(all_text)


# ⭐ 使用示例 (在 hybrid_retrieve 中集成)
def enhanced_hybrid_retrieve(query: str, k: int = 8, metadata_filter=None):
    """增强版混合检索 - 带置信度评估"""
    
    # Step 1: 分析查询复杂度
    complexity = assess_query_complexity(query)
    base_threshold = complexity["threshold"]
    effective_k = int(k * complexity["k_multiplier"])
    
    logger.info(f"[EnhancedRetrieve] Query complexity={complexity['level']}, threshold={base_threshold:.2f}, k={effective_k}")
    
    # Step 2: 执行原始 hybrid 检索
    docs = hybrid_retrieve(query, ..., k=effective_k, ...)
    
    # Step 3: 计算综合置信度
    aggregator = ConfidenceAggregator()
    overall_confidence = aggregator.aggregate(docs, query)
    
    # Step 4: 根据置信度决定是否降级或重试
    if overall_confidence < base_threshold:
        logger.warning(f"[EnhancedRetrieve] Low confidence ({overall_confidence:.2f} < {base_threshold:.2f}), triggering fallback")
        # 可触发:扩大 k 值、切换到宽松模式等
        docs = hybrid_retrieve(query, k=k*2, ...)  # 重试并扩大 k
    
    return docs[:k], overall_confidence


# 在 env 文件中添加配置项
# .env.example 更新内容：
VEC_MIN_SCORE_DEFAULT="0.25"               # 默认阈值上调至 0.25
ADAPTIVE_THRESHOLD_ENABLED=true           # 启用自适应阈值
CONFIDENCE_AGGREGATOR_ENABLED=true         # 启用置信度聚合器
```

---

### **Phase 2: 三路召回扩展 (本周实施)**

#### 2.1 Rule-Based 检索器 (FAQ/条款类优先)

```python
# backend/rag/retrieval/rule_retriever.py

import re
from typing import List
from langchain_core.documents import Document

class RuleBasedRetriever:
    """
    Rule-Based 精确检索 - 针对 FAQ、条款类文档
    优势：命中率 >90%,响应时间 <5ms
    """
    
    FAQ_PATTERNS = {
        "standard_qa": r'(?:Q\.?|问题)[:：]\s*(.+?)\s*(?:A\.?|回答)[:：]\s*(.+)',
        "clause_format": r'(?:第 [一二三四五六七八九十\d]+条|[〇零○][一二三四五六七八九十\d]+条)[:：\s]+(.+)',
        "policy_section": r'(?:政策|规定)[\s\S]{0,100}?(.+?)[\s\n](?:适用|生效|施行)',
    }
    
    def __init__(self, chromadb_client):
        self.client = chromadb_client
        self.collection = chromadb_client.get_or_create_collection("rule_index")
    
    def retrieve(self, query: str, k: int = 5) -> List[Document]:
        """通过规则匹配检索"""
        matches = []
        
        # 1. FAQ 模式匹配
        for pattern_name, pattern in self.FAQ_PATTERNS.items():
            try:
                results = re.findall(pattern, query, re.IGNORECASE | re.DOTALL)
                for result in results[:k]:
                    full_text = f"Q:{result[0]} A:{result[1]}" if isinstance(result, tuple) else result
                    matches.append(self._create_document(full_text, "faq_pattern", 1.0))
            except Exception as e:
                logger.debug(f"[RuleRetriever] Pattern '{pattern_name}' failed: {e}")
        
        # 2. 关键词精确匹配
        keyword_matches = self._exact_keyword_match(query, k)
        matches.extend(keyword_matches)
        
        # 3. 返回 Top-K
        return sorted(matches, key=lambda x: x.metadata.get("rule_score", 0), reverse=True)[:k]
    
    def _exact_keyword_match(self, query: str, k: int) -> List[Document]:
        """从知识库查找完全包含 query 的 chunk"""
        # TODO: 查询 ChromaDB WHERE page_content LIKE %query%
        # 简化版：直接返回空列表（待完善）
        return []
    
    def _create_document(self, content: str, match_type: str, score: float) -> Document:
        """创建带 metadata 的 Document"""
        import hashlib
        
        chunk_id = f"rule_{hashlib.md5(content.encode()).hexdigest()[:12]}"
        
        return Document(
            page_content=content,
            metadata={
                "chunk_id": chunk_id,
                "chunk_type": "rule_match",
                "match_type": match_type,
                "rule_score": score,  # 完美匹配给 1.0
                "source_file": "knowledge_base",
            }
        )
```

#### 2.2 Adaptive Dense Retriever (语义理解优先)

```python
# backend/rag/retrieval/adaptive_dense_retriever.py

class AdaptiveDenseRetriever:
    """
    自适应嵌入检索 - 根据文档类型选择最佳 embedding 模型
    支持：bge-small-zh-v1, bge-large-zh-v1.5, bge-m3
    """
    
    MODEL_REGISTRY = {
        "faq": "bge-small-zh-v1",     # 通用 FAQ 检索
        "policy": "bge-large-zh-v1.5", # 长文档理解强
        "financial": "bge-m3",         # 高精度场景
        "default": "bge-small-zh-v1",
    }
    
    def __init__(self, vector_db, model_manager):
        self.vector_db = vector_db
        self.model_manager = model_manager
    
    def retrieve(self, query: str, k: int = 8, doc_type_hint: str = None) -> List[Document]:
        """带类型感知的检索"""
        
        # 选择最佳模型
        model_name = self.MODEL_REGISTRY.get(doc_type_hint, "default")
        model = self.model_manager.get_model(model_name)
        
        # 动态调整 k (复杂问题扩大召回)
        effective_k = self._adapt_k(query, k)
        
        # 执行检索
        results = self.vector_db.similarity_search(
            query, 
            k=effective_k,
            score_threshold=0.25,  # 默认较严格
            embedding=model.embed_query,
        )
        
        # 添加模型信息到 metadata
        for doc in results:
            doc.metadata["embedding_model"] = model_name
            
        return results[:k]
    
    def _adapt_k(self, query: str, base_k: int) -> int:
        """根据查询长度动态调整 k"""
        word_count = len(query.split())
        
        if word_count < 10:
            return base_k
        elif word_count < 20:
            return int(base_k * 1.2)
        else:
            return int(base_k * 1.5)
```

#### 2.3 Hybrid Sparse Retriever (精确词匹配优先)

```python
# backend/rag/retrieval/hybrid_sparse_retriever.py

class HybridSparseRetriever:
    """
    BM25 + TF-IDF 混合稀疏检索
    适合：精确术语、产品型号、合同编号
    """
    
    def __init__(self, bm25_index, tfidf_index):
        self.bm25 = bm25_index
        self.tfidf = tfidf_index
    
    def retrieve(self, query: str, k: int = 8, weight_bm25: float = 0.6) -> List[Document]:
        """加权融合 BM25 和 TF-IDF"""
        
        bm25_docs = self.bm25.search(query, k=k*2)
        tfidf_docs = self.tfidf.search(query, k=k*2)
        
        fused = self._rrf_fusion(bm25_docs, tfidf_docs, k, weight_bm25)
        
        return fused[:k]
    
    def _rrf_fusion(self, sparse1, sparse2, top_k, weight1):
        """RRF 融合"""
        rank_map = {}
        
        for rank, doc in enumerate(sparse1, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + weight1 / (60 + rank)
        
        for rank, doc in enumerate(sparse2, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + (1-weight1) / (60 + rank)
        
        sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)
        doc_dict = {d.metadata.get("chunk_id"): d for d in sparse1 + sparse2}
        
        return [doc_dict[cid] for cid, _ in sorted_cids[:top_k]]
```

---

### **Phase 3: LLM-as-Judge Evidence Gate (下周实施)**

详见 `docs/EVIDENCE_GATE_LLMLIKEJUDGE.md`

---

### **Phase 4: Self-Correction Query Rewrite (下月实施)**

详见 `docs/SELF_CORRECTION_QUERY_REWRITE.md`
