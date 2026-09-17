"""tests/selection_funnel/test_stages.py — 漏斗各层单元测试

纯函数层不碰存储；淘汰理由必须可复算（数字对得上）。
"""
from backend.selection_funnel.economics import calc_unit_economics
from backend.selection_funnel.stages.economist import econ_candidates
from backend.selection_funnel.stages.ranker import build_reason, rank_candidates
from backend.selection_funnel.stages.screener import screen_candidates


def test_calc_unit_economics_exact():
    econ = calc_unit_economics(price=100.0, unit_cost=40.0,
                               fee_rate=0.055, logistics_fee=5.0,
                               ads_ratio=0.15)
    assert econ["platform_fee"] == 5.5
    assert econ["ads_fee"] == 15.0
    assert econ["net_profit"] == 34.5
    assert econ["margin"] == round(34.5 / 100, 4)
    assert econ["unit_cost_estimated"] is False
    assert econ["warnings"] == []


def test_calc_unit_economics_cost_estimated():
    econ = calc_unit_economics(price=100.0, unit_cost=None, default_cost_ratio=0.45)
    assert econ["unit_cost"] == 45.0
    assert econ["unit_cost_estimated"] is True
    assert any("估计" in w for w in econ["warnings"])


def test_calc_unit_economics_bad_price():
    econ = calc_unit_economics(price=None)
    assert econ["margin"] is None
    assert econ["warnings"]


def test_calc_unit_economics_cost_ge_price():
    econ = calc_unit_economics(price=50.0, unit_cost=60.0)
    assert econ["margin"] < 0
    assert any("没有利润空间" in w for w in econ["warnings"])


def test_screen_drops_on_positive_evidence():
    keep = {"url": "u1", "title": "好品", "rating": 4.6, "review_count": 200,
            "price": 100.0}
    low_rating = {"url": "u2", "title": "差评品", "rating": 3.2,
                  "review_count": 500, "price": 99.0}
    low_reviews = {"url": "u3", "title": "新品", "rating": 4.5,
                   "review_count": 5, "price": 149.0}
    missing_data = {"url": "u4", "title": "无数据品", "rating": None,
                    "review_count": None, "price": None}
    kept, reasons, notes = screen_candidates(
        [keep, low_rating, low_reviews, missing_data],
        "宠物零食", min_rating=4.0, min_reviews=10)
    assert [c["url"] for c in kept] == ["u1", "u4"]
    rules = {r["rule"] for r in reasons}
    assert rules == {"min_rating", "min_reviews"}
    # 缺数据不淘汰，但必须披露
    assert any("rating 缺失" in n for n in notes)
    assert kept[1]["screening"]["passed"] is True


def test_screen_price_band():
    kept, reasons, _ = screen_candidates(
        [{"url": "u1", "title": "t", "rating": 4.5, "review_count": 100,
          "price": 200.0}],
        "宠物零食", min_rating=4.0, min_reviews=10,
        price_min=50.0, price_max=150.0)
    assert kept == []
    assert reasons[0]["rule"] == "price_above_band"


def test_econ_margin_gate_with_computable_reason():
    cands = [
        {"url": "u1", "title": "高毛利", "price": 129.0},
        {"url": "u2", "title": "低毛利", "price": 29.0},
        {"url": "u3", "title": "无价格", "price": None},
    ]
    kept, reasons, notes = econ_candidates(
        cands, "宠物零食", min_margin=0.30, unit_cost=None,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15,
        default_cost_ratio=0.45)
    assert [c["url"] for c in kept] == ["u1", "u3"]
    assert len(reasons) == 1
    r = reasons[0]
    assert r["rule"] == "min_margin"
    # 理由可复算：29 − 13.05 − 1.59 − 5.0 − 4.35 = 5.01 → 17.3%
    assert "17.3%" in r["value"]
    assert "13.05" in r["value"] and "1.59" in r["value"]
    assert any("无价格" in n or "无法测算" in n for n in notes)
    # 无价格候选保留并披露
    assert kept[1]["economics"]["margin"] is None


def test_rank_deterministic_and_reason_traceable():
    cands = [
        {"url": "u1", "title": "A", "price": 129.0,
         "score": {"total": 88.0, "breakdown": {"reputation": 75.0}, "notes": []},
         "economics": {"price": 129.0, "margin": 0.306, "net_profit": 39.5},
         "pain_points": [], },
        {"url": "u2", "title": "B", "price": 99.0,
         "score": {"total": 88.0, "breakdown": {}, "notes": ["insufficient_history"]},
         "economics": {"price": 99.0, "margin": 0.294, "net_profit": 29.15},
         "pain_points": ["同池促销依赖度高：价格战竞争，差异化应避开纯价格维度"], },
    ]
    ranked = rank_candidates(cands, top_n=1)
    # 同分时毛利率高者在前，再按 url 兜底 —— 两次运行结果必然一致
    assert ranked[0]["url"] == "u1"
    assert ranked[0]["rank"] == 1
    ranked2 = rank_candidates(cands, top_n=5)
    assert [c["url"] for c in ranked2] == ["u1", "u2"]
    reason = build_reason(ranked2[1])
    assert "88.0" in reason and "29.4%" in reason
    assert "历史快照不足" in reason
    assert "价格战" in reason
