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


def _draft_cand(url: str, margin: float | None, dq: dict) -> dict:
    return {"url": url, "title": f"品{url}", "price": 100.0,
            "economics": {"margin": margin, "unit_cost_estimated": False},
            "pain_points": [], "data_quality": dq}


def test_decision_draft_tiers_with_data_quality():
    """P2 分层并入决策草案：stale / 低完整度 → 降级条件做；汇总行三档计数。"""
    from backend.selection_funnel.reporter import _decision_draft_lines
    cands = [
        # buffer 12pp、成本实际、无痛点、数据好 → 做
        _draft_cand("u1", 0.32, {"completeness": 1.0, "missing": [],
                                 "freshness": "fresh", "age_days": 1.0}),
        # buffer 12pp 但数据过期 → 条件做（数据已过期）
        _draft_cand("u2", 0.32, {"completeness": 1.0, "missing": [],
                                 "freshness": "stale", "age_days": 60.0}),
        # buffer 12pp 但完整度 50% → 条件做（数据完整度）
        _draft_cand("u3", 0.32, {"completeness": 0.5, "missing": ["sales"],
                                 "freshness": "fresh", "age_days": 1.0}),
    ]
    lines = _decision_draft_lines(cands, min_margin=0.20)
    assert "数据已过期（60 天）" in " ".join(lines)
    assert "数据完整度 50% 偏低" in " ".join(lines)
    summary = lines[-1]
    assert summary.startswith("- 分层汇总") and "做 1 条" in summary \
        and "条件做 2 条" in summary and "放弃 0 条" in summary
    # age_days 缺失的 stale 不炸（防御口径）
    lines2 = _decision_draft_lines(
        [_draft_cand("u9", 0.32, {"completeness": 1.0, "missing": [],
                                  "freshness": "stale"})], min_margin=0.20)
    assert "数据已过期，重新抓取" in " ".join(lines2)


def test_decision_draft_tier_counts_abandon():
    """放弃分支也计入分层汇总（利润率低于线）。"""
    from backend.selection_funnel.reporter import _decision_draft_lines
    lines = _decision_draft_lines(
        [_draft_cand("u1", 0.10, {"completeness": 1.0, "missing": [],
                                  "freshness": "fresh"})], min_margin=0.20)
    assert "放弃 1 条" in lines[-1]
    assert any("**放弃**" in ln for ln in lines)


def test_candidates_from_funnel_normalization():
    """漏斗 Top-N → 决策候选：字段归一 / 缺 title 用 url / 脏行剔除 / 空回落。"""
    from backend.orchestration.workflows.selection_decision import candidates_from_funnel
    top = [
        {"rank": 1, "title": "冻干鸡肉", "url": "u-a", "platform": "淘宝",
         "price": 129.0, "rating": 4.8, "review_count": 12000,
         "highlights": "冻干", "score_total": 88.0, "margin": 0.276},
        {"rank": 2, "url": "u-b"},                      # 无 title → 用 url 兜底
        {"rank": 3, "title": "", "url": ""},            # 双缺 → 剔除
        "not-a-dict",                                    # 非法行 → 剔除
    ]
    cands = candidates_from_funnel({"funnel_candidates": top})
    assert len(cands) == 2
    assert cands[0]["title"] == "冻干鸡肉" and cands[0]["review_count"] == 12000
    assert "score_total" not in cands[0]   # 只取决策所需字段，不带评分/利润
    assert cands[1]["title"] == "u-b"
    assert candidates_from_funnel({}) == []
    assert candidates_from_funnel({"funnel_candidates": []}) == []


def test_build_workflow_inputs_injects_funnel_top():
    """主图执行器输入组装：selection_decision 注入漏斗 Top-N，其他 workflow 不注入。"""
    from backend.orchestration.graph.direct_executor import _build_workflow_inputs
    state = {"question": "对Top1跑选品决策", "session_id": "s1",
             "funnel_context": {"top": [{"title": "a", "url": "u-a"}]}}
    inputs = _build_workflow_inputs("selection_decision", state)
    assert inputs["funnel_candidates"] == [{"title": "a", "url": "u-a"}]
    assert inputs["session_id"] == "s1"
    # 其他 workflow 契约不变
    assert "funnel_candidates" not in _build_workflow_inputs("daily_report", state)
    # 无漏斗上下文 → 不注入
    assert "funnel_candidates" not in _build_workflow_inputs(
        "selection_decision", {"question": "q", "session_id": "s2"})
    # 漏斗空跑（top 空）→ 不注入
    assert "funnel_candidates" not in _build_workflow_inputs(
        "selection_decision", {"question": "q", "session_id": "s3",
                               "funnel_context": {"top": []}})


def test_decision_report_discloses_funnel_source():
    """决策报告披露候选来源：funnel_topn / watchlist 两分支。"""
    from backend.selection_decision.report import build_report
    base_out = {"competitor_data": {"candidates": [], "count": 2,
                                    "source": "funnel_topn",
                                    "note": "承接漏斗推荐单"}}
    md = build_report({"category": "宠物零食"}, base_out, verdict="no_go",
                      failed_gates=["market"])
    assert "候选来源：选品漏斗 Top-2" in md and "承接漏斗推荐单" in md
    md2 = build_report({"category": "宠物零食"},
                       {"competitor_data": {"source": "watchlist"}},
                       verdict="no_go", failed_gates=["market"])
    assert "候选来源：竞品监控池" in md2


# ── P2 余量：来源健康度 / 多次快照趋势 / 规则版本 ──────────────

def test_pool_source_health_statuses(patch_stores):
    """来源健康度：ok / empty / error（单源炸不炸池都如实记录）。"""
    from backend.selection_funnel.stages import pool_builder as pb
    patch_stores([{"url": "u-1", "title": "宠物零食冻干鸡肉", "price": 59.0,
                   "snapshot_id": 1}])
    pool, _notes, _reasons, sources = pb.build_pool("宠物零食")
    by_src = {s["source"]: s for s in sources}
    assert by_src["import"]["status"] == "empty" and by_src["import"]["count"] == 0
    assert by_src["watchlist"]["status"] == "ok" and by_src["watchlist"]["count"] == 1

    # 单源读取异常 → error 状态 + 池不炸（双保险路径）
    def _boom():
        raise RuntimeError("db locked")
    orig = pb._load_import_candidates
    pb._load_import_candidates = _boom
    try:
        _pool, notes, _r, sources2 = pb.build_pool("宠物零食")
    finally:
        pb._load_import_candidates = orig
    by_src2 = {s["source"]: s for s in sources2}
    assert by_src2["import"]["status"] == "error"
    assert any("失败" in n for n in notes)


def test_verify_trend_from_snapshot_history(patch_stores):
    """多次快照趋势：两端点对比（价格降 10%）；单点如实 snapshots=1。"""
    from backend.selection_funnel.stages.verifier import _trend
    hist = [  # 新→旧（store 口径）
        {"price": 90.0, "review_count": 1500, "rating": 4.7, "crawled_at": "2026-09-10"},
        {"price": 100.0, "review_count": 1200, "rating": 4.8, "crawled_at": "2026-09-01"},
    ]
    t = _trend(hist)
    assert t["snapshots"] == 2
    assert t["price"] == {"first": 100.0, "last": 90.0, "pct": -0.1, "direction": "down"}
    assert t["reviews"]["direction"] == "up" and t["reviews"]["pct"] == 0.25
    assert t["rating"]["first"] == 4.8 and t["rating"]["last"] == 4.7
    assert _trend([{"price": 59.0}]) == {"snapshots": 1}
    assert _trend([]) == {"snapshots": 0}
    # 缺端点字段不补造
    assert _trend([{"price": None}, {"price": 50.0}])["price"] is None


def test_trend_lines_render():
    """报告趋势章：有历史给首末对比，全单点给补数指引。"""
    from backend.selection_funnel.reporter import _trend_lines
    with_hist = {"url": "u1", "title": "监控款", "trend": {
        "snapshots": 2, "price": {"first": 100.0, "last": 90.0, "pct": -0.1,
                                  "direction": "down"},
        "reviews": {"first": 1200, "last": 1500, "pct": 0.25,
                    "direction": "up"}, "rating": None}}
    single = {"url": "u2", "title": "导入款", "trend": {"snapshots": 1}}
    lines = _trend_lines([with_hist, single])
    assert any("价格 100→90（-10.0%）" in ln and "评价 1200→1500（+25.0%）" in ln
               for ln in lines)
    assert any("其余 1 条为单点候选" in ln for ln in lines)
    # 全单点 → 口径指引
    lines2 = _trend_lines([single])
    assert len(lines2) == 1 and "单点数据" in lines2[0] and "监控" in lines2[0]


def test_config_rules_version_stable_and_changes(monkeypatch):
    """规则版本指纹：同一配置恒同指纹；类目规则变更 → 新指纹。"""
    from backend.config.selection_funnel import build_config_snapshot
    v1 = build_config_snapshot("宠物零食", 0.20)["rules_version"]
    v2 = build_config_snapshot("宠物零食", 0.20)["rules_version"]
    assert v1 == v2 and len(v1) == 8
    # 阈值变更 → 新版本
    v3 = build_config_snapshot("宠物零食", 0.25)["rules_version"]
    assert v3 != v1
    # 类目规则变更 → 新版本
    import backend.config.selection_funnel as cfg
    monkeypatch.setattr(cfg, "CATEGORY_RULES",
                        {**cfg.CATEGORY_RULES, "宠物零食": {"min_margin": 0.35}})
    v4 = build_config_snapshot("宠物零食", 0.20)["rules_version"]
    assert v4 != v1


# ── 导入池历史趋势接线（2026-09-18）：verifier 批量取同款历史 ──────────
def test_verify_import_history_wires_trend(isolated_import_store):
    """同款跨两批次导入 → 趋势从导入池历史算出并标注来源；稳定性不再 insufficient。"""
    from backend.selection_funnel.stages.verifier import verify_candidates
    store = isolated_import_store
    store.add_batch([{"title": "冻干鸡肉", "url": "u-a", "price": 59.0,
                      "review_count": 100, "rating": 4.6}], category="宠物零食")
    store.add_batch([{"title": "冻干鸡肉", "url": "u-a", "price": 49.0,
                      "review_count": 150, "rating": 4.7}], category="宠物零食")
    cand = {"url": "u-a", "title": "冻干鸡肉", "platform": "淘宝", "price": 49.0,
            "rating": 4.7, "review_count": 150, "category": "宠物零食",
            "imported_at": "2026-09-18T00:00:00"}
    enriched, notes = verify_candidates([cand])
    t = enriched[0]["trend"]
    assert t["snapshots"] == 2
    assert t["source"] == "import_pool"
    assert t["price"]["first"] == 59.0 and t["price"]["last"] == 49.0
    assert t["price"]["direction"] == "down"
    assert "insufficient_history" not in enriched[0]["score"]["notes"]
    assert notes == []   # 正常路径不产生噪音 note


def test_verify_import_history_failure_degrades_to_single_point(monkeypatch,
                                                                 isolated_import_store):
    """导入池历史读取失败 → 降级单点披露，不炸验证层。"""
    from backend.selection_funnel import import_pool
    from backend.selection_funnel.stages import verifier
    monkeypatch.setattr(import_pool, "get_import_store",
                        lambda: (_ for _ in ()).throw(RuntimeError("pg down")))
    cand = {"title": "冻干鸡肉", "platform": "淘宝", "price": 49.0,
            "imported_at": "2026-09-18T00:00:00"}
    enriched, notes = verifier.verify_candidates([cand])
    assert enriched[0]["trend"] == {"snapshots": 0}   # 历史为空 → 单点/零点如实披露
    assert any("同款历史读取失败" in n for n in notes)


def test_verify_watchlist_candidate_still_uses_competitor_history(patch_stores):
    """监控候选（无 imported_at）仍走竞品 store.history，趋势口径不变。"""
    from conftest import make_snap
    from backend.selection_funnel.stages.verifier import verify_candidates
    patch_stores([make_snap(url="u-w", price=59.0, review_count=100),
                  make_snap(url="u-w", price=55.0, review_count=200)])
    cand = {"url": "u-w", "title": "监控款", "platform": "淘宝", "price": 55.0,
            "review_count": 200, "crawled_at": "2026-09-10T00:00:00"}
    enriched, _ = verify_candidates([cand])
    t = enriched[0]["trend"]
    assert t["snapshots"] == 2
    assert "source" not in t   # 竞品历史不标导入来源


def test_reporter_trend_lines_show_import_source():
    """报告趋势段透出「导入池历史批次」来源标签。"""
    from backend.selection_funnel.reporter import _trend_lines
    cand = {"url": "u1", "title": "导入款", "trend": {
        "snapshots": 2, "source": "import_pool",
        "price": {"first": 59.0, "last": 49.0, "pct": -0.1695, "direction": "down"}}}
    lines = _trend_lines([cand])
    assert any("导入池历史批次" in ln for ln in lines)
    assert any("59→49" in ln for ln in lines)
