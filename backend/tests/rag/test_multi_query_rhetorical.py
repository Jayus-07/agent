"""反问句误触发多查询的回归（2026-10-09）。

缺陷：_classify_query_tier 原先用 q.count("？") 数标点判定多意图，
把「一个论点 + 一个反问强调」误判成两个检索诉求。
实测「rag搜索不应该要30s？生产环境不能这么慢吧？」被判 hybrid_multi_query
→ 触发 1 次 LLM 改写 + 3 路检索（白付 ~2.3s），实际只有一个诉求。

修法：统计「真实疑问句」（_count_real_questions），反问句不计入；
并把触发条件由「问号≥2 或 子句≥3」收紧为
「(问号≥2 且 子句≥2) 或 子句≥3」。

本组测试锁两侧边界：
  - 反问句不得再触发（回归点）；
  - 真·多意图必须仍然触发（防止矫枉过正）。
"""
import pytest

from backend.rag.retrieval.hybrid import (
    _classify_query_tier,
    _count_real_questions,
    _is_rhetorical,
)


# ── 反问识别单测 ──────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "生产环境不能这么慢吧",
    "这不应该优化吗",
    "这样不对吧",
    "难道不该优化吗",
    "rag搜索不应该要30s",
])
def test_rhetorical_detected(sentence):
    assert _is_rhetorical(sentence) is True


@pytest.mark.parametrize("sentence", [
    "为什么这么慢",
    "超时是多少",
    "怎么配置",
    "退款怎么处理",
    "有没有配置项",      # 正反问 A不A 是真问题
    "是不是有问题",      # 同上
    "为什么不对",        # 否定 + 疑问代词 = 真问题
])
def test_genuine_question_not_rhetorical(sentence):
    assert _is_rhetorical(sentence) is False


def test_count_real_questions_drops_rhetorical():
    """本次修复的核心断言：反问不计入真实疑问数。"""
    q = "rag搜索不应该要30s？生产环境不能这么慢吧？"
    # 旧口径：2 个问号 → 误判多意图
    assert q.count("？") + q.count("?") == 2
    # 新口径：0 个真实疑问
    assert _count_real_questions(q) == 0


def test_count_real_questions_keeps_real_multi():
    assert _count_real_questions("为什么这么慢？怎么回事？") == 2
    assert _count_real_questions("怎么配置？超时是多少？") == 2


# ── 分类器端到端 ──────────────────────────────────────

def test_original_repro_no_longer_triggers():
    """原始误报用例：必须回到 hybrid（不再触发多查询改写）。"""
    q = "rag搜索不应该要30s？生产环境不能这么慢吧？"
    assert _classify_query_tier(q) == "hybrid"


@pytest.mark.parametrize("query", [
    "为什么这么慢？怎么回事？",
    "是不是有问题？要不要改？",
    "A是什么？B怎么配置？C多少钱？",
    "对比一下两个方案，另外说明成本",
])
def test_real_multi_intent_still_triggers(query):
    """真·多意图不得被误伤（防矫枉过正）。"""
    assert _classify_query_tier(query) == "hybrid_multi_query"


@pytest.mark.parametrize("query", [
    "退款怎么处理？",
    "报销流程怎么走？",
    "退款审核时间是多少？",
    "什么是RAG",
])
def test_single_intent_not_triggered(query):
    """普通单意图问答不应触发改写（原有成本控制语义保持）。"""
    assert _classify_query_tier(query) != "hybrid_multi_query"
