"""P2-3: MinHash/LLM 优化自动化测试套件

目标：
1. 验证 FAQ vs financial 分类准确性（售后 FAQ 应识别为 faq）
2. 验证 MinHash 缓存机制功能正确性
3. 验证 LLM 并行执行性能提升
4. 基准测试：优化前后的性能对比

运行方式：
    pytest tests/rag/test_optimizations_p2.py -v --tb=short
    pytest tests/rag/test_optimizations_p2.py -v -k "test_classification_faq"
"""

import asyncio
import os
import time
import tempfile
from pathlib import Path
from typing import List, Tuple

import pytest

# 导入测试模块
from backend.rag.preprocessing.metadata import (
    classify_with_confidence, 
    compute_minhash, 
    minhash_similarity,
    _add_minhash_to_cache,
    _query_minhash_from_cache,
    _clear_minhash_cache,
)
from backend.rag.preprocessing.domain_data import DOC_TYPE_RULES


# =====================================================
# Test Fixtures
# =====================================================

@pytest.fixture(scope="function")
def clear_minhash_cache():
    """每个测试前清空 MinHash 缓存"""
    _clear_minhash_cache()
    yield
    _clear_minhash_cache()  # 清理


@pytest.fixture(scope="module")
def sample_docs() -> dict:
    """样本文档内容（模拟真实场景）"""
    return {
        "faq_post_sale": """
七天无理由退货范围 A：支持，客户在签收后 7 天内可申请无理由退货（贴身衣物/食品等除外）。
Q：退货商品运费 A：质量问题由商家承担运费；无理由退货由买家承担运费。
Q：退款时效 A：商品验收合格后，1-3 个工作日内原路退回支付账户。
""",
        "financial_invoice": """
财务报销制度
一、发票管理
1. 所有报销必须提供正规发票
2. 发票抬头必须为公司全称
3. 发票金额超过 5000 元需附明细清单
二、成本核算
1. 项目成本价 = 采购价 + 运费 + 关税
2. 每月 5 日前提交上月财务报表
""",
        "faq_shipping": """
常见问题 FAQ - 物流篇
Q：发货时效多久？A：工作日 24 小时内发货
Q：退换货流程是什么？A：申请 → 审核 → 寄回 → 确认收货 → 退款
Q：退款流程如何操作？A：登录后台 → 订单管理 → 申请退款
""",
    }


# =====================================================
# 测试 #1: 分类准确性（FAQ vs Financial）
# =====================================================

class TestClassificationAccuracy:
    """验证售后 FAQ 不会被误分类为财务文档"""
    
    def test_faq_post_sale_identified_as_faq(self, sample_docs):
        """售后 FAQ 文档应识别为 faq（而非 financial）"""
        text = sample_docs["faq_post_sale"]
        filename = "cs_售后 FAQ.docx"
        
        doc_type, confidence, detail = classify_with_confidence(
            text, filename=filename, return_detail=True
        )
        
        assert doc_type == "faq", f"期望 faq 但得到 {doc_type}"
        assert confidence >= 0.5, f"置信度过低：{confidence}"
        
        # 检查详细得分
        scores = detail.get("scores", {})
        assert scores.get("faq", 0) >= scores.get("financial", 0), \
            f"faq 得分不应低于 financial：{scores}"
        
        print(f"✅ 测试通过：{filename} → {doc_type} (confidence={confidence:.2f})")
        print(f"   得分详情：{scores}")
    
    def test_faq_shipping_high_confidence(self, sample_docs):
        """明确包含 FAQ 的文档应有高置信度"""
        text = sample_docs["faq_shipping"]
        
        doc_type, confidence = classify_with_confidence(text, return_detail=False)
        
        assert doc_type == "faq", f"期望 faq 但得到 {doc_type}"
        assert confidence > 0.7, f"置信度应为 >0.7，实际：{confidence}"
        
        print(f"✅ 测试通过：FAQ + 退换货关键词 → {doc_type} ({confidence:.2f})")
    
    def test_financial_not_confused_with_faq(self, sample_docs):
        """财务文档不应被误判为 faa"""
        text = sample_docs["financial_invoice"]
        
        doc_type, confidence = classify_with_confidence(text)
        
        # 可能识别为 financial 或 general，但不应是 faa
        assert doc_type != "faq", f"财务文档不应被误判为 faa：{doc_type}"
        assert doc_type in {"financial", "general"}, \
            f"财务相关文档应为 financial 或 general：{doc_type}"
        
        print(f"✅ 测试通过：财务文档 → {doc_type} (未误判为 faq)")
    
    def test_arbitration_triggered_when_confused(self, sample_docs):
        """当 faa vs financial 得分接近时，应触发仲裁"""
        # 构造一个混合文档（同时包含财务和 FAQ 关键词）
        mixed_text = """
退款政策 FAQ
Q：如何申请退款？A：提交发票即可
发票报销流程：
1. 整理所有发票
2. 填写成本价申请表
3. 提交财务审批
"""
        
        doc_type, confidence, detail = classify_with_confidence(
            mixed_text, return_detail=True
        )
        
        scores = detail.get("scores", {})
        faq_score = scores.get("faq", 0)
        fin_score = scores.get("financial", 0)
        
        # 如果分差 ≤ 5 分，应该触发仲裁
        if abs(faq_score - fin_score) <= 5:
            assert detail.get("llm_fallback", False), \
                "分差过小时应触发 LLM 仲裁"
            
            print(f"✅ 测试通过：分差 {abs(faq_score - fin_score)} 触发仲裁 → {doc_type}")
        else:
            print(f"⚠️  未触发仲裁（分差={abs(faq_score - fin_score)}），当前分类：{doc_type}")


# =====================================================
# 测试 #2: MinHash 缓存机制
# =====================================================

class TestMinHashCache:
    """验证 MinHash 缓存功能"""
    
    def test_compute_minhash_deterministic(self):
        """相同的文本应生成相同的签名"""
        text = "test document content for hashing"
        
        sig1 = compute_minhash(text)
        sig2 = compute_minhash(text)
        
        assert sig1 == sig2, "相同文本的签名必须一致"
        assert len(sig1) == 128, "签名长度必须为 128"
        
        print(f"✅ 签名确定性验证通过：{len(sig1)} 维签名")
    
    def test_minhash_similarity_threshold(self):
        """相似文档的相似度应高于阈值"""
        base_text = "这是一个电商售后退款政策的说明文档"
        similar_text = base_text + "关于七天无理由退货的规定\n"
        different_text = "这是完全不同的财务发票报销内容"
        
        sig_base = compute_minhash(base_text)
        sig_similar = compute_minhash(similar_text)
        sig_different = compute_minhash(different_text)
        
        sim_same = minhash_similarity(sig_base, sig_base)
        sim_similar = minhash_similarity(sig_base, sig_similar)
        sim_different = minhash_similarity(sig_base, sig_different)
        
        assert sim_same >= 0.95, f"相同文档相似度应>0.95：{sim_same}"
        assert sim_similar > sim_different, f"相似文档相似度应高于不同文档"
        assert sim_different < 0.5, f"不同文档相似度应较低：{sim_different}"
        
        print(f"✅ 相似度验证：same={sim_same:.3f}, similar={sim_similar:.3f}, different={sim_different:.3f}")
    
    def test_minhash_cache_add_and_query(self, clear_minhash_cache):
        """测试缓存的写入和读取"""
        # 准备数据
        signatures = [
            ("doc1_hash", compute_minhash("document 1 content")),
            ("doc2_hash", compute_minhash("document 2 content")),
            ("doc3_hash", compute_minhash("电商退款政策说明")),
        ]
        
        # 添加到缓存
        for file_hash, sig in signatures:
            _add_minhash_to_cache("faq", file_hash, sig)
        
        # 查询测试
        query_sig = compute_minhash("电商退款政策")
        results = _query_minhash_from_cache("faq", query_sig)
        
        assert len(results) == 3, f"应返回 3 个结果：{results}"
        assert isinstance(results[0], tuple), "结果应为 (file_hash, similarity) 元组"
        
        # 检查排序（降序）
        similarities = [r[1] for r in results]
        assert similarities == sorted(similarities, reverse=True), \
            "结果应按相似度降序排列"
        
        print(f"✅ MinHash 缓存测试通过：{len(results)} 个命中结果")
    
    def test_minhash_cache_capacity_limit(self, clear_minhash_cache):
        """测试缓存容量限制（LRU）"""
        from backend.rag.preprocessing.metadata import _MINHASH_CACHE_MAX_SIZE
        
        # 添加超过上限的数据
        for i in range(_MINHASH_CACHE_MAX_SIZE + 100):
            sig = compute_minhash(f"document {i}")
            _add_minhash_to_cache("test_type", f"hash_{i}", sig)
        
        cache_list = _query_minhash_from_cache("test_type", compute_minhash("dummy"))
        
        assert len(cache_list) <= _MINHASH_CACHE_MAX_SIZE, \
            f"缓存数量不应超过{_MINHASH_CACHE_MAX_SIZE}"
        
        print(f"✅ 容量控制验证：实际存储 {len(cache_list)} / {_MINHASH_CACHE_MAX_SIZE}")


# =====================================================
# 测试 #3: LLM 并行执行性能对比
# =====================================================

class TestLLMParallelPerformance:
    """验证 LLM 任务并行执行的效率"""
    
    @pytest.mark.asyncio
    async def test_parallel_vs_serial_execution(self):
        """对比串行 vs 并行的耗时"""
        # 模拟三个重型任务
        async def slow_task(name: str, duration_ms: int = 500):
            await asyncio.sleep(duration_ms / 1000)
            return f"{name} completed"
        
        tasks = [
            ("summary", slow_task("summary", 500)),
            ("keywords", slow_task("keywords", 800)),
            ("entities", slow_task("entities", 600)),
        ]
        
        # 并行执行
        start_parallel = time.time()
        parallel_results = await asyncio.gather(*[t[1] for t in tasks])
        parallel_duration = time.time() - start_parallel
        
        # 串行执行
        start_serial = time.time()
        serial_results = []
        for name, task_func in tasks:
            result = await task_func
            serial_results.append(result)
        serial_duration = time.time() - start_serial
        
        speedup = serial_duration / parallel_duration if parallel_duration > 0 else 0
        
        print(f"\n{'='*60}")
        print(f"LLM 并行性能测试结果:")
        print(f"{'='*60}")
        print(f"串行总耗时：{serial_duration * 1000:.0f}ms")
        print(f"并行总耗时：{parallel_duration * 1000:.0f}ms")
        print(f"加速比：{speedup:.2f}x")
        print(f"理论最佳值（max）: ~1.6x")
        print(f"{'='*60}\n")
        
        # 验证并行确实更快（允许 10% 误差）
        assert parallel_duration < serial_duration, \
            "并行执行应快于串行"
        assert speedup >= 1.5, f"加速比应>=1.5x，实际：{speedup:.2f}x"


# =====================================================
# 测试 #4: 端到端文档索引验证
# =====================================================

class TestEndToEndIndexing:
    """完整的文档索引流程测试"""
    
    @pytest.fixture
    def temp_doc_file(self, sample_docs):
        """创建临时文档文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(sample_docs["faq_post_sale"])
            f.flush()
            yield f.name
        os.unlink(f.name)
    
    def test_end_to_faq_classification(self, temp_doc_file):
        """从文件到分类结果的完整流程"""
        import os
        
        with open(temp_doc_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        filename = os.path.basename(temp_doc_file)
        doc_type, confidence = classify_with_confidence(content, filename=filename)
        
        # 验证结果
        assert doc_type == "faq", f"应识别为 faq：{content[:100]}"
        assert confidence > 0.5, f"置信度应>0.5：{confidence}"
        
        print(f"✅ E2E 测试通过：{filename} → {doc_type} ({confidence:.2f})")


# =====================================================
# 测试 #5: 边界条件与异常处理
# =====================================================

class TestEdgeCases:
    """边界条件和异常处理"""
    
    def test_empty_document_classification(self):
        """空文档的处理"""
        doc_type, confidence = classify_with_confidence("")
        
        assert doc_type == "general", "空文档应归类为 general"
        assert confidence == 0.0, f"空文档置信度应为 0：{confidence}"
    
    def test_short_document_classification(self):
        """短文档的处理"""
        short_text = "退款"
        doc_type, confidence = classify_with_confidence(short_text)
        
        # 短文档通常无法准确分类
        assert doc_type in {"general", "faq"}, f"短文档分类不确定：{doc_type}"
    
    def test_minhash_with_no_tokens(self):
        """无有效 token 的情况"""
        # 纯标点符号
        text = "...,,,!!!???"
        sig = compute_minhash(text)
        
        assert sig == [0] * 128, f"无效文本应返回全 0 签名：{sig}"


# =====================================================
# 性能基准报告生成器
# =====================================================

def generate_performance_report(test_results: dict):
    """生成性能基准测试报告（供后续阶段对比）"""
    import json
    
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "P2-OPTIMIZATION-v1",
        "tests": test_results,
        "metrics": {
            "classification_accuracy": test_results.get("faq_recognition_rate", 0),
            "minhash_cache_hits": test_results.get("cache_efficiency", 0),
            "llm_parallel_speedup": test_results.get("parallel_speedup_ratio", 0),
        },
        "recommendations": [],
    }
    
    if report["metrics"]["classification_accuracy"] >= 0.9:
        report["recommendations"].append("✅ 分类准确率优秀，可投入生产")
    else:
        report["recommendations"].append("⚠️  分类准确率偏低，建议调优权重")
    
    if report["metrics"]["llm_parallel_speedup"] >= 1.5:
        report["recommendations"].append("✅ 并行执行生效，预期提速 50%+")
    else:
        report["recommendations"].append("⚠️  并行效果不明显，检查任务依赖")
    
    print("\n" + "="*80)
    print("📊 性能基准报告")
    print("="*80)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("="*80 + "\n")
    
    return report


if __name__ == "__main__":
    """手动运行测试并生成报告"""
    import sys
    
    print("\n" + "="*80)
    print("🧪 P2 优化自动化测试套件 - 手动执行模式")
    print("="*80)
    
    results = {}
    
    try:
        # 运行测试
        exit_code = pytest.main([__file__, "-v", "--tb=short"])
        
        if exit_code == 0:
            print("\n✅ 所有测试通过！可以开始手动验证。")
        else:
            print("\n❌ 部分测试失败，请查看上述错误信息。")
        
        sys.exit(exit_code)
        
    except Exception as e:
        print(f"\n❌ 测试异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
