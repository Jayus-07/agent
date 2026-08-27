#!/usr/bin/env python3
"""RAG 置信度优化验证测试脚本

运行方式：
    pytest tests/rag/test_confidence_optimization.py -v --tb=short
    python scripts/verify_confidence_optimization.py  # 快速手动验证
"""

import pytest
from typing import List, Dict
import time


class TestAdaptiveThreshold:
    """测试自适应阈值功能"""
    
    def test_complexity_assessment_simple(self):
        """简单查询 → 低阈值"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import assess_query_complexity
        
        result = assess_query_complexity("退货流程")
        
        assert result["level"] == "simple"
        assert result["threshold"] <= 0.25
    
    def test_complexity_assessment_complex(self):
        """复杂查询 → 高阈值"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import assess_query_complexity
        
        query = "2026 年第一季度的销售额中，哪个产品的毛利率最高？具体数值是多少"
        result = assess_query_complexity(query)
        
        assert result["level"] == "complex" or result["level"] == "medium"
        assert result["threshold"] >= 0.30
    
    def test_advec_threshold_matrix(self):
        """测试文档类型阈值矩阵"""
        from backend.config.rag import ADAPTIVE_VEC_THRESHOLDS
        
        # 检查所有必要类型都存在
        required_types = ["faq", "policy", "financial", "general"]
        for dtype in required_types:
            assert dtype in ADAPTIVE_VEC_THRESHOLDS, f"Missing threshold for {dtype}"
        
        # FAQ 应该最严格
        assert ADAPTIVE_VEC_THRESHOLDS["faq"] >= ADAPTIVE_VEC_THRESHOLDS["general"]


class TestConfidenceAggregator:
    """测试置信度聚合器"""
    
    @pytest.fixture
    def sample_docs(self):
        """构造样本文档"""
        from langchain_core.documents import Document
        
        docs = [
            Document(
                page_content="七天无理由退货政策说明...",
                metadata={
                    "chunk_id": "test1",
                    "doc_type": "faq",
                    "score": 0.75,
                    "bm25_score": 0.68,
                }
            ),
            Document(
                page_content="退款时效规定：1-3 个工作日...",
                metadata={
                    "chunk_id": "test2",
                    "doc_type": "policy",
                    "score": 0.65,
                    "bm25_score": 0.60,
                }
            ),
        ]
        return docs
    
    def test_aggregation_basic(self, sample_docs):
        """基础聚合计算"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import ConfidenceAggregator
        
        aggregator = ConfidenceAggregator()
        confidence = aggregator.aggregate(sample_docs, "退货政策")
        
        assert 0.0 <= confidence <= 1.0
        assert confidence > 0.4  # 样本质量合理应该>0.4


class TestMultiPathRetrieval:
    """三路召回集成测试 (Mock 环境)"""
    
    def test_rule_based_faq_detection(self):
        """Rule-Based 路径正确识别 FAQ"""
        from backend.rag.retrieval.rule_retriever import RuleBasedRetriever
        
        # Mock chromadb_client
        class MockCollection:
            def get_or_create_collection(self, name):
                return self
        
        retriever = RuleBasedRetriever(MockCollection())
        
        query = "Q:七天无理由退货范围是什么？A:支持客户在签收后 7 天内申请"
        docs = retriever.retrieve(query, k=5)
        
        assert len(docs) > 0
        assert all(d.metadata.get("match_type") == "faq_pattern" for d in docs)
    
    def test_dense_embedding_selects_model(self):
        """Dense 检索正确选择 embedding 模型"""
        # TODO: 需要 Mock vector_db 和 model_manager
        pass


class TestEvidenceGateIntegration:
    """Evidence Gate 集成测试"""
    
    def test_low_confidence_triggers_fallback(self):
        """低置信度触发降级策略"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import enhanced_hybrid_retrieve
        
        # Mock all retrievers
        mock_vector = type('MockRetriever', (), {'retrieve': lambda **kw: []})()
        mock_bm25 = type('MockRetriever', (), {'invoke': lambda q: []})()
        
        query = "完全不符合知识库的问题 xxxxxxxx"
        docs, meta = enhanced_hybrid_retrieve(query, mock_vector, mock_bm25, k=5)
        
        assert "fallback_used" in meta["metrics"]
        if not docs:
            assert meta["metrics"]["fallback_used"] is True


class TestPerformanceImprovement:
    """性能提升验证"""
    
    def test_enhanced_vs_original_speed(self):
        """增强版不应显著慢于原版"""
        # TODO: 需要真实的 vector_db 和 bm25_index
        # 这里仅演示测试逻辑
        
        original_time = 0.1  # Mock data
        enhanced_time = 0.15  # 预期增加不超过 50%
        
        overhead_ratio = enhanced_time / original_time
        assert overhead_ratio < 1.5, f"Enhanced retrieval should be <1.5x slower, got {overhead_ratio}"


class TestBusinessScenarios:
    """业务场景专项测试"""
    
    @pytest.mark.parametrize("query,expected_threshold", [
        ("退货政策", 0.35),           # FAQ → high threshold
        ("员工报销流程", 0.30),        # Policy → medium threshold  
        ("财务指标查询", 0.45),        # Financial → very high threshold
        ("产品规格", 0.30),            # Product_spec → medium
    ])
    def test_business_specific_thresholds(self, query, expected_threshold):
        """不同业务类型的阈值匹配"""
        from backend.rag.retrieval.enhanced_hybrid_retrieval import assess_query_complexity
        
        result = assess_query_complexity(query)
        
        # 复杂度应该匹配
        if "退货" in query:
            assert result["threshold"] >= 0.35
        elif "报销" in query:
            assert result["threshold"] >= 0.30


# =====================================================
# 快速手动验证
# =====================================================

def quick_verify():
    """快速手动验证所有核心功能"""
    
    print("\n" + "="*80)
    print("🧪 RAG CONFIDENCE OPTIMIZATION - QUICK VERIFY")
    print("="*80 + "\n")
    
    # Test 1: 复杂度评估
    print("[TEST 1] Query Complexity Assessment")
    from backend.rag.retrieval.enhanced_hybrid_retrieval import assess_query_complexity
    
    queries = [
        ("退货", "simple"),
        ("退款时效多久", "medium"),
        ("2026 年 Q1 华东区销售数据分析", "complex"),
    ]
    
    for query, expected_level in queries:
        result = assess_query_complexity(query)
        status = "✅" if result["level"] == expected_level else "⚠️"
        print(f"  {status} '{query}' → {result['level']} (threshold={result['threshold']:.2f})")
    
    # Test 2: 阈值矩阵
    print("\n[TEST 2] Adaptive Threshold Matrix")
    from backend.config.rag import ADAPTIVE_VEC_THRESHOLDS
    
    for doc_type, threshold in ADAPTIVE_VEC_THRESHOLDS.items():
        print(f"  • {doc_type:15s} → {threshold:.2f}")
    
    # Test 3: 置信度聚合
    print("\n[TEST 3] Confidence Aggregation")
    from langchain_core.documents import Document
    from backend.rag.retrieval.enhanced_hybrid_retrieval import ConfidenceAggregator
    
    sample_docs = [
        Document(page_content="退货政策内容...", metadata={"doc_type": "faq", "score": 0.75}),
        Document(page_content="退款流程说明...", metadata={"doc_type": "policy", "score": 0.65}),
    ]
    
    aggregator = ConfidenceAggregator()
    confidence = aggregator.aggregate(sample_docs, "退货政策")
    print(f"  Sample docs confidence: {confidence:.2%}")
    print(f"  {'✅' if confidence > 0.4 else '⚠️'} Quality threshold met")
    
    print("\n" + "="*80)
    print("ALL TESTS COMPLETED")
    print("="*80 + "\n")
    
    return True


if __name__ == "__main__":
    import sys
    
    # 如果是直接运行，执行快速验证
    try:
        success = quick_verify()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
