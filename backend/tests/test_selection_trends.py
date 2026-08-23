"""trends 单测 — 临时 CompetitorStore 灌入快照，验证聚合"""
import pytest

from backend.competitor.store import CompetitorStore
from backend.selection.trends import compute_trends


@pytest.fixture
def store(tmp_path):
    s = CompetitorStore(db_path=str(tmp_path / "competitor_test.db"))
    s.save_snapshot({"url": "https://a.com", "platform": "taobao", "title": "A",
                     "price": 100.0, "review_count": 100, "in_stock": 1,
                     "highlights": "无线,降噪", "crawled_at": "2026-08-01T10:00:00"})
    s.save_snapshot({"url": "https://a.com", "platform": "taobao", "title": "A",
                     "price": 90.0, "review_count": 400, "in_stock": 1,
                     "highlights": "无线,长续航", "crawled_at": "2026-08-11T10:00:00"})
    s.save_snapshot({"url": "https://b.com", "platform": "jd", "title": "B",
                     "price": 200.0, "review_count": 50, "in_stock": 0,
                     "highlights": "降噪", "crawled_at": "2026-08-10T10:00:00"})
    return s


class TestComputeTrends:
    def test_review_growth_per_url(self, store):
        t = compute_trends(store, days=0)
        by_url = {g["url"]: g for g in t["review_growth"]}
        assert by_url["https://a.com"]["daily_delta"] == pytest.approx(30.0)  # 300 / 10 天
        assert "https://b.com" not in by_url  # 单快照无增速

    def test_highlight_freq_sorted_desc(self, store):
        t = compute_trends(store, days=0)
        freq = {h["keyword"]: h["count"] for h in t["highlight_freq"]}
        assert freq["降噪"] == 2
        assert freq["无线"] == 2
        assert freq["长续航"] == 1

    def test_price_quantiles_per_day(self, store):
        t = compute_trends(store, days=0)
        dates = {q["date"] for q in t["price_quantiles"]}
        assert "2026-08-11" in dates
        q = next(x for x in t["price_quantiles"] if x["date"] == "2026-08-11")
        assert q["p50"] <= q["p75"]

    def test_days_filter(self, store):
        t = compute_trends(store, days=10, now_iso="2026-08-12T00:00:00")
        assert t["sources"]["snapshot_count"] == 2  # 仅 8-01 之后的两条

    def test_platform_filter(self, store):
        t = compute_trends(store, days=0, platform="taobao")
        assert t["sources"]["snapshot_count"] == 2

    def test_empty_store(self, tmp_path):
        s = CompetitorStore(db_path=str(tmp_path / "empty.db"))
        t = compute_trends(s, days=0)
        assert t["items"] == []
        assert t["price_quantiles"] == []

    def test_price_quantile_interpolation(self, tmp_path):
        s = CompetitorStore(db_path=str(tmp_path / "quant.db"))
        for i, price in enumerate((100.0, 200.0, 300.0, 400.0)):
            s.save_snapshot({"url": f"https://q{i}.com", "platform": "taobao",
                             "title": f"Q{i}", "price": price, "review_count": 10,
                             "in_stock": 1, "highlights": "",
                             "crawled_at": "2026-08-10T10:00:00"})
        t = compute_trends(s, days=0)
        q = next(x for x in t["price_quantiles"] if x["date"] == "2026-08-10")
        assert q["p25"] == pytest.approx(175.0)
        assert q["p50"] == pytest.approx(250.0)
        assert q["p75"] == pytest.approx(325.0)
