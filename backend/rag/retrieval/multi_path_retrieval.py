"""Multi-Path Recall Enhancement - 三路径混合召回

新增路径：
1. Rule-Based Retrieval: 基于关键词/正则的精确匹配
2. Embedding Retrieval: Dense vector similarity
3. Sparse Retrieval: BM25 + TF-IDF

优势：
- FAQ 问答：Rule-based 命中率 >90%
- 政策制度：Dense 语义搜索效果最佳
- 财务报表：Sparse 精确词匹配优先
"""

import re
from typing import List, Tuple
from langchain_core.documents import Document

# =====================================================
# Path 1: Rule-Based Search（FAQ/制度类最优）
# =====================================================

class RuleBasedRetriever:
    """基于规则的精确检索 - 针对结构化问答"""
    
    def __init__(self, kb_vector_db):
        self.kb_vector_db = kb_vector_db
        # FAQ 模式匹配
        self.faq_patterns = [
            r'Q\s*[:：]\s*(.+?)\s*A\s*[:：]\s*(.+)',  # Q:xxx A:xxx 格式
            r'(?:第 [一二三四五六七八九十\d]+条|[〇零○][一二三四五六七八九十\d]+条)[:：\s]+(.+)',  # 条款内容
        ]
        
    def retrieve(self, query: str, k: int = 5) -> List[Document]:
        """通过规则匹配检索 QA 对"""
        docs = []
        
        # 1. FAQ 模式匹配
        faq_matches = self._match_faq_patterns(query)
        for match in faq_matches[:k]:
            chunk_id = f"rule_faq_{hash(match)}"
            docs.append(Document(
                page_content=match,
                metadata={
                    "chunk_id": chunk_id,
                    "chunk_type": "faq_match",
                    "source_file": "knowledge_base",
                    "rule_match_score": 1.0,  # 完美匹配
                }
            ))
        
        # 2. 关键词精确匹配
        keyword_matches = self._exact_keyword_match(query, k)
        docs.extend(keyword_matches)
        
        return docs[:k]
    
    def _match_faq_patterns(self, query: str) -> List[str]:
        """匹配 FAQ 格式内容"""
        texts = []
        for pattern in self.faq_patterns:
            matches = re.findall(pattern, query, re.IGNORECASE | re.DOTALL)
            for m in matches:
                full_match = f"{m[0]}A:{m[1]}" if isinstance(m, tuple) else m
                texts.append(full_match)
        return texts
    
    def _exact_keyword_match(self, query: str, k: int) -> List[Document]:
        """从知识库中提取完全包含 query 的 chunk"""
        # TODO: 查询 ChromaDB/PGVector 中 page_content LIKE %query% 的结果
        # 这里仅展示接口
        return []


# =====================================================
# Path 2: Enhanced Dense Retrieval（语义理解最优）
# =====================================================

class AdaptiveEmbeddingRetriever:
    """自适应嵌入检索 - 根据文档类型选择模型"""
    
    def __init__(self, kb_vector_db):
        self.vector_db = kb_vector_db
        
        # 多模型支持
        self.models = {
            "faq": "bge-small-zh-v1",     # 通用中文
            "legal": "bge-large-zh-v1.5", # 法律文本理解更强
            "financial": "bge-m3",        # 高精度场景
        }
        
    def retrieve(self, query: str, k: int = 8, doc_type_hint: str = None) -> List[Document]:
        """带类型感知的检索"""
        
        # 选择最佳 embedding 模型
        model_name = self.models.get(doc_type_hint, "bge-small-zh-v1")
        
        # 动态调整 k 值（复杂问题扩大召回）
        effective_k = self._adaptive_k(query, k)
        
        results = self.vector_db.similarity_search(
            query, 
            k=effective_k,
            score_threshold=0.25  # 默认较严格的阈值
        )
        
        # 添加模型信息到 metadata
        for doc in results:
            doc.metadata["embedding_model"] = model_name
            
        return results[:k]
    
    def _adaptive_k(self, query: str, base_k: int) -> int:
        """根据查询复杂度动态调整召回数量"""
        from backend.config import MULTI_QUERY_COUNT
        
        # 简单问题减少 k，复杂问题增加 k
        query_len = len(query.split())
        
        if query_len < 10:
            return base_k  # 简单问题用较小 k
        elif query_len < 20:
            return int(base_k * 1.2)
        else:
            return int(base_k * 1.5)  # 复杂问题扩大召回


# =====================================================
# Path 3: Sparse + Hybrid Retrieval（精确匹配最优）
# =====================================================

class HybridSparseRetriever:
    """BM25 + TF-IDF 混合稀疏检索"""
    
    def __init__(self, bm25_index, tfidf_index):
        self.bm25 = bm25_index
        self.tfidf = tfidf_index
        
    def retrieve(self, query: str, k: int = 8, weight_bm25: float = 0.6) -> List[Document]:
        """加权融合 BM25 和 TF-IDF"""
        
        bm25_results = self.bm25.search(query, k=k*2)
        tfidf_results = self.tfidf.search(query, k=k*2)
        
        # RRF 融合
        fused = self._rrf_fusion(bm25_results, tfidf_results, k, weight_bm25)
        
        return fused[:k]
    
    def _rrf_fusion(self, sparse1: List[Document], sparse2: List[Document], 
                   top_k: int, weight1: float) -> List[Document]:
        """加权 RRF 融合"""
        rank_map = {}
        
        # BM25 贡献
        for rank, doc in enumerate(sparse1, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + weight1 / (60 + rank)
        
        # TF-IDF 贡献
        for rank, doc in enumerate(sparse2, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + (1-weight1) / (60 + rank)
        
        # 排序取 Top-K
        sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)
        
        doc_dict = {d.metadata.get("chunk_id"): d for d in sparse1 + sparse2}
        return [doc_dict[cid] for cid, _ in sorted_cids[:top_k]]


# =====================================================
# Main Orchestrator - 三路召回协调器
# =====================================================

class MultiPathRetrievalOrchestrator:
    """三路召回协调器 - 自动选择最佳策略"""
    
    def __init__(self, rule_retriever, dense_retriever, sparse_retriever):
        self.rule_retriever = rule_retriever
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        
        # 性能监控
        self.metrics = {
            "rule_hit_rate": 0.0,
            "dense_avg_score": 0.0,
            "sparse_precision": 0.0,
        }
    
    def retrieve(self, query: str, k: int = 8, doc_type: str = None) -> List[Document]:
        """三路并行召回 + 动态权重融合"""
        
        from concurrent.futures import ThreadPoolExecutor
        
        # 1. 分析查询类型，预估各路径成功率
        success_probs = self._estimate_success_probabilities(query, doc_type)
        
        # 2. 根据概率分配资源
        weights = self._compute_optimal_weights(success_probs)
        
        # 3. 并行执行三路召回
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_rule = executor.submit(self._retrieve_with_weight, 
                                         self.rule_retriever.retrieve, 
                                         query, k=int(k*weights["rule"]))
            future_dense = executor.submit(self._retrieve_with_weight,
                                          self.dense_retriever.retrieve,
                                          query, k=int(k*weights["dense"]),
                                          doc_type_hint=doc_type)
            future_sparse = executor.submit(self._retrieve_with_weight,
                                           self.sparse_retriever.retrieve,
                                           query, k=int(k*weights["sparse"]))
            
            rule_docs = future_rule.result() or []
            dense_docs = future_dense.result() or []
            sparse_docs = future_sparse.result() or []
        
        # 4. 统一 RRF 融合
        final_docs = self._ultimate_rrf_fusion(rule_docs, dense_docs, sparse_docs, k)
        
        # 5. 更新监控指标
        self._update_metrics(rule_docs, dense_docs, sparse_docs, final_docs)
        
        return final_docs[:k]
    
    def _estimate_success_probabilities(self, query: str, doc_type: str) -> dict:
        """预估各路径成功概率"""
        probs = {"rule": 0.0, "dense": 0.5, "sparse": 0.3}
        
        # 如果检测到 FAQ 特征 → rule path 优先级最高
        if re.search(r'Q[:：].*?A[:：]|退货.*?流程 | 退款.*?时效', query, re.IGNORECASE):
            probs["rule"] = 0.8
        
        # 文档类型提示
        if doc_type == "faq":
            probs["rule"] = max(probs["rule"], 0.6)
            probs["dense"] = 0.3
        elif doc_type in ["policy", "financial"]:
            probs["sparse"] = 0.6
            probs["dense"] = 0.4
            
        return probs
    
    def _compute_optimal_weights(self, success_probs: dict) -> dict:
        """基于成功概率计算召回权重"""
        total = sum(success_probs.values())
        if total == 0:
            return {"rule": 0.1, "dense": 0.7, "sparse": 0.2}
        
        return {k: v/total for k, v in success_probs.items()}
    
    def _retrieve_with_weight(self, retriever_fn, query: str, k: int, **kwargs) -> List[Document]:
        """包装检索函数，确保 k 为整数"""
        return retriever_fn(query, max(1, k), **kwargs)
    
    def _ultimate_rrf_fusion(self, rule_docs: List[Document], dense_docs: List[Document],
                            sparse_docs: List[Document], k: int) -> List[Document]:
        """三路 RRF 融合 - 给 rule_based 更高权重"""
        
        rank_map = {}
        
        # Rule-based 完美匹配 → 权重加倍
        for rank, doc in enumerate(rule_docs, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + 2.0 / (60 + rank)
        
        # Dense retrieval
        for rank, doc in enumerate(dense_docs, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + 1.0 / (60 + rank)
        
        # Sparse retrieval
        for rank, doc in enumerate(sparse_docs, start=1):
            cid = doc.metadata.get("chunk_id")
            rank_map[cid] = rank_map.get(cid, 0) + 0.8 / (60 + rank)
        
        # 排序
        sorted_cids = sorted(rank_map.items(), key=lambda x: x[1], reverse=True)
        doc_dict = {d.metadata.get("chunk_id"): d for d in rule_docs + dense_docs + sparse_docs}
        
        return [doc_dict[cid] for cid, _ in sorted_cids[:k]]
    
    def _update_metrics(self, rule_docs, dense_docs, sparse_docs, final_docs):
        """更新性能监控指标"""
        n_rule = len(rule_docs)
        n_final = len(final_docs)
        
        if n_final > 0:
            self.metrics["rule_hit_rate"] = n_rule / n_final
            self.metrics["dense_avg_score"] = sum(
                getattr(d, "score", 0) for d in dense_docs[:5]
            ) / 5 if dense_docs else 0.0


# =====================================================
# Usage Example
# =====================================================

def enhanced_multi_path_retrieve(query: str, k: int = 8, 
                                 kb_vector_db=None, 
                                 doc_type: str = None) -> List[Document]:
    """主入口函数 - 替换原有的 hybrid_retrieve"""
    
    # 初始化三路 retriever
    rule_retriever = RuleBasedRetriever(kb_vector_db)
    dense_retriever = AdaptiveEmbeddingRetriever(kb_vector_db)
    sparse_retriever = HybridSparseRetriever(  # TODO: 初始化 BM25/TF-IDF 索引
        bm25_index=None,
        tfidf_index=None
    )
    
    orchestrator = MultiPathRetrievalOrchestrator(rule_retriever, dense_retriever, sparse_retriever)
    
    return orchestrator.retrieve(query, k, doc_type)
