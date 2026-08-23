"""scoring 单测 — 五维度加权评分与边界处理（spec §5.1）"""
import pytest

from backend.selection.scoring import (
    DEFAULT_WEIGHTS,
    score_product,
    split_keywords,
)


def _snap(**kw):
    """构造快照 dict，缺省为数据完整的理想快照"""
    base = {
        "id": 1,
        "url": "https://a.com",
        "platform": "taobao",
        "title": "测试商品",
        "price": 100.0,
        "original_price": 120.0,
        "currency": "CNY",
        "promo_text": "",
        "rating": 4.8,
        "review_count": 1000,
        "in_stock": 1,
        "highlights": "无线,降噪,长续航",
        "crawled_at": "2026-08-20T10:00:00",
    }
    base.update(kw)
    return base


class TestReputation:
    def test_high_rating_scores_high(self):
        result = score_product(_snap(rating=4.8), [_snap(rating=4.8)], [_snap(rating=4.8)])
        assert result["breakdown"]["reputation"] == pytest.approx(100.0)

    def test_missing_rating_neutral_with_note(self):
        result = score_product(_snap(rating=None), [_snap(rating=None)], [_snap(rating=None)])
        assert result["breakdown"]["reputation"] == 50.0
        assert "data_insufficient" in result["notes"]


class TestPrice:
    def test_single_item_pool_neutral_quantile(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert "single_item_pool" in result["notes"]
        # 分位中性 50 + 折扣分 > 0 → price 在 (25, 75) 区间
        assert 25.0 < result["breakdown"]["price"] < 75.0

    def test_cheapest_in_pool_scores_higher(self):
        cheap = _snap(url="https://cheap.com", price=50.0, original_price=None)
        expensive = _snap(url="https://exp.com", price=200.0, original_price=None)
        r_cheap = score_product(cheap, [cheap], [cheap, expensive])
        r_exp = score_product(expensive, [expensive], [cheap, expensive])
        assert r_cheap["breakdown"]["price"] > r_exp["breakdown"]["price"]

    def test_missing_price_neutral(self):
        result = score_product(_snap(price=None), [_snap(price=None)], [_snap(price=None)])
        assert result["breakdown"]["price"] == 50.0


class TestHeat:
    def test_missing_review_count_neutral(self):
        result = score_product(_snap(review_count=None), [_snap(review_count=None)],
                               [_snap(review_count=None)])
        assert result["breakdown"]["heat"] == 50.0

    def test_review_growth_boosts_heat(self):
        old = _snap(id=1, review_count=100, crawled_at="2026-08-10T10:00:00")
        new = _snap(id=2, review_count=1000, crawled_at="2026-08-20T10:00:00")
        stagnant = _snap(id=3, url="https://b.com", review_count=1000,
                         crawled_at="2026-08-20T10:00:00")
        r_growing = score_product(new, [new, old], [new, stagnant])
        r_flat = score_product(stagnant, [stagnant], [new, stagnant])
        assert r_growing["breakdown"]["heat"] > r_flat["breakdown"]["heat"]


class TestDifferentiation:
    def test_unique_highlights_score_high(self):
        me = _snap(highlights="独家,专利,新品")
        other = _snap(url="https://b.com", highlights="无线,降噪,长续航")
        result = score_product(me, [me], [me, other])
        assert result["breakdown"]["differentiation"] == pytest.approx(100.0)

    def test_identical_highlights_score_zero(self):
        me = _snap(highlights="无线,降噪")
        other = _snap(url="https://b.com", highlights="无线,降噪")
        result = score_product(me, [me], [me, other])
        assert result["breakdown"]["differentiation"] == pytest.approx(0.0)

    def test_empty_pool_neutral(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert result["breakdown"]["differentiation"] == 50.0


class TestStability:
    def test_insufficient_history_neutral(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert result["breakdown"]["stability"] == 50.0
        assert "insufficient_history" in result["notes"]

    def test_stable_price_high_stock_scores_high(self):
        snaps = [_snap(id=i, price=100.0 + i * 0.1, crawled_at=f"2026-08-{10 + i}T10:00:00")
                 for i in range(5)]
        latest = snaps[-1]
        result = score_product(latest, list(reversed(snaps)), [latest])
        assert result["breakdown"]["stability"] > 90.0


class TestAggregation:
    def test_total_in_range_and_breakdown_complete(self):
        snap = _snap()
        result = score_product(snap, [snap], [snap])
        assert 0.0 <= result["total"] <= 100.0
        assert set(result["breakdown"]) == {
            "reputation", "heat", "price", "differentiation", "stability"
        }

    def test_weights_normalized_when_not_summing_to_one(self):
        snap = _snap(rating=4.8)
        heavy = {"reputation": 1.0, "heat": 1.0, "price": 0.0,
                 "differentiation": 0.0, "stability": 0.0}
        result = score_product(snap, [snap], [snap], weights=heavy)
        # 权重归一化后 reputation/heat 各占 0.5
        expected = 0.5 * result["breakdown"]["reputation"] + 0.5 * result["breakdown"]["heat"]
        assert result["total"] == pytest.approx(expected)

    def test_notes_deduplicated(self):
        snap = _snap(rating=None, review_count=None, price=None)
        result = score_product(snap, [snap], [snap])
        assert result["notes"].count("data_insufficient") == 1


class TestSplitKeywords:
    def test_split_comma_and_chinese_comma(self):
        assert split_keywords("无线,降噪，长续航") == {"无线", "降噪", "长续航"}

    def test_empty_string(self):
        assert split_keywords("") == set()
