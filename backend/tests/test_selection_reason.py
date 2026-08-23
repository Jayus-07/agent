"""LLM 推荐理由生成单测 — 事实锁定校验（mock LLM）"""
from unittest.mock import MagicMock, patch

from backend.selection.recommender import generate_reason

_PAYLOAD = {
    "title": "无线降噪耳机",
    "platform": "taobao",
    "latest_price": 129.0,
    "currency": "CNY",
    "rating": 4.8,
    "review_count": 12000,
    "highlights": "无线,降噪,长续航",
    "score": {
        "total": 82.5,
        "breakdown": {"reputation": 90, "heat": 75, "price": 80,
                      "differentiation": 70, "stability": 88},
        "notes": [],
    },
}


class TestGenerateReason:
    def test_fallback_when_llm_fails(self):
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("llm down")
            result = generate_reason(_PAYLOAD)
        assert result["llm_reason"]
        assert "82.5" in result["llm_reason"]
        assert "LLM" in result["llm_risks"]

    def test_number_tampering_falls_back(self):
        fake = MagicMock()
        fake.content = "该商品潜力分高达 99 分，评价数 999999，强烈推荐。"
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.return_value = fake
            result = generate_reason(_PAYLOAD)
        # LLM 篡改数字 → 回退模板，不含伪造数字
        assert "999999" not in result["llm_reason"]
        assert "82.5" in result["llm_reason"]

    def test_valid_llm_output_kept(self):
        fake = MagicMock()
        fake.content = "潜力分 82.5，评价数 12000，评分 4.8，口碑与热度俱佳。"
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.return_value = fake
            result = generate_reason(_PAYLOAD)
        assert result["llm_reason"] == fake.content

    def test_notes_mentioned_in_risks(self):
        payload = {**_PAYLOAD, "score": {**_PAYLOAD["score"], "notes": ["data_insufficient"]}}
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.side_effect = RuntimeError("llm down")
            result = generate_reason(payload)
        # note 经 _NOTE_LABELS 映射为中文标签
        assert "部分字段缺失" in result["llm_risks"]

    def test_title_numbers_allowed(self):
        # title/highlights 中的数字也在 HumanMessage 中给出，引用不算编造
        payload = {**_PAYLOAD,
                   "title": "iPhone 15 手机",
                   "highlights": "24小时续航"}
        fake = MagicMock()
        fake.content = "iPhone 15 主打 24小时续航，潜力分 82.5，值得入手。"
        with patch("backend.selection.recommender.llm") as mock_llm:
            mock_llm.invoke.return_value = fake
            result = generate_reason(payload)
        assert result["llm_reason"] == fake.content
