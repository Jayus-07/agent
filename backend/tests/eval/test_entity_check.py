"""拒答校准：查询实体存在性校验单元测试（P1-1，V1.3）。

hard negative 的 rerank 分数落在正样本主区间，纯分数启发式分不开；
对 should_reject 用例追加判据：问题核心实体不在召回内容中 → 判拒答。
"""
from backend.evaluation.runners.builtin import (
    _entities_all_present,
    _extract_query_entities,
)


def test_extract_entities_filters_stopwords():
    """疑问词/泛称被过滤，核心实体保留"""
    entities = _extract_query_entities("笔记本电脑的保修期是多久？")
    assert "保修期" in entities
    assert "多久" not in entities
    assert "是什么" not in entities
    # 单字与纯数字不构成实体
    assert all(len(e) >= 2 and not e.isdigit() for e in entities)


def test_extract_entities_business_question():
    """业务问题的领域词被保留"""
    entities = _extract_query_entities("出口退税的申报流程是怎样的？")
    assert any("退税" in e for e in entities)
    assert "申报" in entities


def test_entities_all_present_true():
    """全部实体见于 top_n 召回文本 → True"""
    details = [
        {"page_content": "笔记本电脑保修政策：整机 1 年。"},
        {"page_content": "保修期内免费维修。"},
    ]
    assert _entities_all_present(["笔记本电脑", "保修"], details) is True


def test_entities_one_missing_returns_false():
    """任一实体缺失 → False（主题相近但无答案）"""
    details = [
        {"page_content": "商品保修期多久？电子产品一般 1 年。"},
    ]
    # 召回内容谈"保修"但没有"笔记本电脑"
    assert _entities_all_present(["笔记本电脑", "保修"], details) is False


def test_entities_empty_returns_true():
    """无实体可校验 → 不干预原判（True）"""
    assert _entities_all_present([], [{"page_content": "any"}]) is True


def test_entities_space_insensitive():
    """归一化匹配：空格差异不影响命中"""
    details = [{"page_content": "响应时效 48小时内核查"}]
    assert _entities_all_present(["48 小时"], details) is True


def test_entities_top_n_limit():
    """只检索 top_n 个 chunk，超出的内容不计入"""
    details = [{"page_content": f"无关内容 {i}"} for i in range(3)]
    details.append({"page_content": "笔记本电脑保修 1 年"})  # 第 4 个
    assert _entities_all_present(["笔记本电脑"], details, top_n=3) is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
