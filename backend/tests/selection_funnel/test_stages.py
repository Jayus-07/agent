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
    # 退款损耗默认 3%：100 × 0.03 = 3.0
    assert econ["refund_loss"] == 3.0
    # 100 − 40 − 5.5 − 5.0 − 15.0 − 3.0 = 31.5
    assert econ["net_profit"] == 31.5
    assert econ["margin"] == round(31.5 / 100, 4)
    assert econ["unit_cost_estimated"] is False
    assert econ["warnings"] == []


def test_calc_unit_economics_cost_estimated():
    econ = calc_unit_economics(price=100.0, unit_cost=None, default_cost_ratio=0.45)
    assert econ["unit_cost"] == 45.0
    assert econ["unit_cost_estimated"] is True
    # 100 − 45 − 5.5 − 5.0 − 15.0 − 3.0 = 26.5（退款损耗同样计入估计路径）
    assert econ["net_profit"] == 26.5
    assert any("估计" in w for w in econ["warnings"])


def test_calc_unit_economics_zero_refund_back_compatible():
    """refund_ratio=0 时口径退化为旧版毛利瀑布（不含退款损耗）。"""
    econ = calc_unit_economics(price=100.0, unit_cost=40.0,
                               fee_rate=0.055, logistics_fee=5.0,
                               ads_ratio=0.15, refund_ratio=0.0)
    assert econ["refund_loss"] == 0.0
    assert econ["net_profit"] == 34.5


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
    # refund_ratio=0.0：本用例只隔离毛利门控语义，退款损耗口径由 exact 用例覆盖
    kept, reasons, notes = econ_candidates(
        cands, "宠物零食", min_margin=0.30, unit_cost=None,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15, refund_ratio=0.0,
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


def test_econ_refund_loss_in_reason():
    """退款损耗进淘汰理由，数字可复算（六步法④经济口径补全）。"""
    cands = [{"url": "u1", "title": "贴线品", "price": 100.0}]
    kept, reasons, _ = econ_candidates(
        cands, "宠物零食", min_margin=0.40, unit_cost=40.0,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15, refund_ratio=0.03,
        default_cost_ratio=0.45)
    assert kept == []
    r = reasons[0]
    # 100 − 40 − 5.5 − 5.0 − 15.0 − 3.0 = 31.5 → 31.5%
    assert "31.5%" in r["value"]
    assert "退款损耗 3.0" in r["value"]


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


def test_econ_candidate_level_cost_overrides_brief():
    """候选级成本（导入表「成本」列）优先于需求级 brief.max_unit_cost（P1 补录源）。"""
    from backend.selection_funnel.stages.economist import econ_candidates
    cands = [
        {"url": "u-1", "title": "a", "price": 100.0, "unit_cost": 20.0},
        {"url": "u-2", "title": "b", "price": 100.0, "unit_cost": None},
    ]
    kept, reasons, notes = econ_candidates(
        cands, "宠物零食", min_margin=0.20, unit_cost=45.0,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15,
        refund_ratio=0.03, default_cost_ratio=0.45)
    assert len(kept) == 2 and reasons == []
    by_url = {c["url"]: c for c in kept}
    e1, e2 = by_url["u-1"]["economics"], by_url["u-2"]["economics"]
    assert e1["unit_cost"] == 20.0 and e1["unit_cost_estimated"] is False
    # 需求级 45 来自 brief（用户明确给的成本）→ actual 而非 estimated
    assert e2["unit_cost"] == 45.0 and e2["unit_cost_estimated"] is False


def test_econ_default_cost_ratio_is_estimated():
    """无任何成本来源（无候选级/需求级）→ 按默认比例估计并标记 estimated。"""
    from backend.selection_funnel.stages.economist import econ_candidates
    cands = [{"url": "u-1", "title": "a", "price": 100.0}]
    kept, _r, _n = econ_candidates(
        cands, "宠物零食", min_margin=0.20, unit_cost=None,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15,
        refund_ratio=0.03, default_cost_ratio=0.45)
    econ = kept[0]["economics"]
    assert econ["unit_cost"] == 45.0 and econ["unit_cost_estimated"] is True
    assert any("估计" in w for w in econ["warnings"])


def test_verify_freshness_levels():
    """数据新鲜度（P1 余量）：crawled_at 优先 / imported_at 兜底，超阈值 → stale；仅披露不淘汰。"""
    from datetime import datetime, timedelta
    from backend.selection_funnel.stages.verifier import _data_quality
    now = datetime.now()
    # stale：crawled_at 60 天前（监控抓取时间）
    dq = _data_quality({"title": "a",
                        "crawled_at": (now - timedelta(days=60)).isoformat(timespec="seconds")})
    assert dq["freshness"] == "stale" and dq["age_days"] > 30
    # fresh：imported_at 刚刚（导入行时间戳兜底口径）
    dq2 = _data_quality({"title": "b",
                         "imported_at": now.isoformat(timespec="seconds")})
    assert dq2["freshness"] == "fresh"
    # unknown：无时间戳 / 坏格式，不炸
    assert _data_quality({"title": "c"})["freshness"] == "unknown"
    assert _data_quality({"title": "d", "crawled_at": "not-a-date"})["freshness"] == "unknown"
    # 未来时间戳钳到 0 天，判 fresh
    dq3 = _data_quality({"title": "e",
                         "crawled_at": (now + timedelta(days=1)).isoformat(timespec="seconds")})
    assert dq3["freshness"] == "fresh" and dq3["age_days"] == 0.0


def test_evidence_lines_report_freshness():
    """证据与假设章的新鲜度披露：stale 点名 / 全 fresh 一句话 / 无时间戳给补数指引。"""
    from datetime import datetime, timedelta
    from backend.selection_funnel.reporter import _evidence_lines
    stale_c = {"url": "u1", "title": "老快照", "price": 10.0,
               "economics": {"margin": 0.35},
               "data_quality": {"completeness": 1.0, "missing": [],
                                "freshness": "stale", "age_days": 60.0}}
    fresh_c = {"url": "u2", "title": "新导入", "price": 10.0,
               "economics": {"margin": 0.35},
               "data_quality": {"completeness": 1.0, "missing": [],
                                "freshness": "fresh", "age_days": 0.1}}
    fr = [ln for ln in _evidence_lines([stale_c, fresh_c])
          if ln.startswith("- 数据新鲜度")]
    assert len(fr) == 1 and "1 条已过期" in fr[0] and "老快照" in fr[0]
    # 全无时间戳 → 如实说无法判断 + 指引补列
    unk = [{"url": "u", "title": "x", "economics": {"margin": None},
            "data_quality": {"completeness": 0.5, "missing": ["price"],
                             "freshness": "unknown", "age_days": None}}]
    lines2 = _evidence_lines(unk)
    assert lines2[-1].startswith("- 数据新鲜度") and "无法判断" in lines2[-1]
    # 全 fresh → 一句话带阈值
    lines3 = _evidence_lines([fresh_c])
    fr3 = [ln for ln in lines3 if ln.startswith("- 数据新鲜度")]
    assert len(fr3) == 1 and "天内" in fr3[0]
