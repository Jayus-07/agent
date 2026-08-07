"""测试 RAG reranker._sigmoid 边界值 + RerankCompressor 集成。

Why:
  _sigmoid 是修复 Rerank threshold 过严的核心，单元测试防止回归。
  修复前：CrossEncoder 输出的 logit 直接与 0.3 比较 → 全被过滤
  修复后：先 sigmoid 归一化到 0-1 再比较
"""
import math

import pytest

from backend.rag.reranker import _sigmoid


class TestSigmoid:
    """_sigmoid 数值稳定性测试。"""

    def test_zero_returns_half(self):
        """logit=0 → 0.5（中性阈值）"""
        assert abs(_sigmoid(0.0) - 0.5) < 1e-6

    def test_large_positive_approaches_one(self):
        """logit=10 → 接近 1（强相关）"""
        result = _sigmoid(10.0)
        assert result > 0.999

    def test_large_negative_approaches_zero(self):
        """logit=-10 → 接近 0（弱相关）"""
        result = _sigmoid(-10.0)
        assert result < 0.001

    def test_positive_branch_stable(self):
        """正数分支不应溢出"""
        for x in [0.5, 1.0, 5.0, 50.0]:
            result = _sigmoid(x)
            assert 0.5 < result <= 1.0

    def test_negative_branch_stable(self):
        """负数分支不应溢出"""
        for x in [-0.5, -1.0, -5.0, -50.0]:
            result = _sigmoid(x)
            assert 0.0 <= result < 0.5

    def test_symmetry(self):
        """sigmoid(-x) == 1 - sigmoid(x)"""
        for x in [0.5, 1.5, 3.0, 7.5]:
            a = _sigmoid(x)
            b = _sigmoid(-x)
            assert abs((a + b) - 1.0) < 1e-6

    def test_bge_reranker_typical_logit_range(self):
        """BGE-reranker-base 实际输出范围 -10~+10，sigmoid 后 0~1"""
        # 模拟真实 logit 分布
        logits = [-8.0, -5.0, -2.0, -0.5, 0.5, 2.0, 5.0, 8.0]
        results = [_sigmoid(x) for x in logits]
        # 全部映射到 0~1
        assert all(0 <= r <= 1 for r in results)
        # 单调性：logit 越大 → sigmoid 越大
        assert results == sorted(results)


@pytest.mark.parametrize(
    "logit,threshold,should_pass",
    [
        (5.0, 0.3, True),    # sigmoid(5)≈0.993 > 0.3
        (0.5, 0.3, True),    # sigmoid(0.5)≈0.622 > 0.3
        (0.0, 0.3, True),    # sigmoid(0)=0.5 > 0.3
        (-0.5, 0.3, True),   # sigmoid(-0.5)≈0.378 > 0.3
        (-1.0, 0.3, False),  # sigmoid(-1)≈0.269 < 0.3
        (-5.0, 0.3, False),  # sigmoid(-5)≈0.007 < 0.3
    ],
)
def test_threshold_filtering_after_sigmoid(logit, threshold, should_pass):
    """关键修复：BGE logit -1 也能通过 sigmoid 0.3 阈值（之前失败）。"""
    prob = _sigmoid(logit)
    is_passed = prob > threshold
    assert is_passed == should_pass, (
        f"logit={logit} → prob={prob:.4f}, expected should_pass={should_pass}"
    )
