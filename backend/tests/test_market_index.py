"""market_index 单测 — mock Chroma，验证文档格式与检索封装"""
from unittest.mock import MagicMock, patch

import pytest

from backend.selection.market_index import MarketIndex, build_doc


def _snap():
    return {
        "id": 42,
        "url": "https://item.taobao.com/item.htm?id=1",
        "platform": "taobao",
        "title": "无线降噪耳机",
        "price": 129.0,
        "original_price": 199.0,
        "currency": "CNY",
        "promo_text": "限时立减",
        "rating": 4.8,
        "review_count": 12000,
        "in_stock": 1,
        "highlights": "无线,降噪,长续航",
        "crawled_at": "2026-08-23T08:00:00",
    }


class TestBuildDoc:
    def test_doc_text_and_id(self):
        doc_id, text, meta = build_doc(_snap())
        assert doc_id == "snap-42"
        assert "无线降噪耳机" in text
        assert "卖点:无线,降噪,长续航" in text
        assert "促销:限时立减" in text

    def test_metadata_fields(self):
        _, _, meta = build_doc(_snap())
        assert meta["url"] == "https://item.taobao.com/item.htm?id=1"
        assert meta["platform"] == "taobao"
        assert meta["snapshot_id"] == 42
        assert meta["price_band"] in ("low", "mid", "high")

    def test_missing_price_band_empty(self):
        snap = _snap()
        snap["price"] = None
        _, _, meta = build_doc(snap)
        assert meta["price_band"] == ""


class TestMarketIndex:
    def _make_index(self):
        idx = MarketIndex.__new__(MarketIndex)
        idx._chroma = MagicMock()
        return idx

    def test_index_snapshot_calls_upsert(self):
        idx = self._make_index()
        doc_id = idx.index_snapshot(_snap())
        assert doc_id == "snap-42"
        idx._chroma._collection.upsert.assert_called_once()

    def test_index_snapshot_without_id_returns_empty(self):
        idx = self._make_index()
        snap = _snap()
        snap["id"] = None
        assert idx.index_snapshot(snap) == ""
        idx._chroma._collection.upsert.assert_not_called()

    def test_search_trends_applies_filter(self):
        idx = self._make_index()
        doc = MagicMock()
        doc.page_content = "正文"
        doc.metadata = {"url": "u"}
        idx._chroma.similarity_search_with_score.return_value = [(doc, 0.2)]
        hits = idx.search_trends("耳机", k=5, metadata_filter={"platform": "taobao"})
        assert len(hits) == 1
        assert hits[0]["text"] == "正文"
        idx._chroma.similarity_search_with_score.assert_called_once_with(
            "耳机", k=5, filter={"platform": "taobao"}
        )
