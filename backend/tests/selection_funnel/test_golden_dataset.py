"""tests/selection_funnel/test_golden_dataset.py — 选品漏斗业务回归（golden dataset）

2026-09-17 P0（优化方案评审第四节）：固定候选快照下，筛选计数、Top-N 顺序、
淘汰原因必须稳定——规则/阈值/数据源任何改动都可量化回归影响。

三类目 × 数据形态：正常 / 全淘空 / 缺成本(估计) / 缺评价数(销量代判) /
异常价格 / 重复 url / 无 url 同款 / 价格带倒挂 / 毛利率越界 / 未知平台。
计数字段全部手工可复算（economics 口径见 config 默认值）。
"""
from backend.selection_funnel.economics import calc_unit_economics
from backend.selection_funnel.graph_state import new_selection_funnel_graph_input
from backend.selection_funnel.stages.economist import econ_candidates
from backend.selection_funnel.stages.pool_builder import build_pool
from backend.selection_funnel.stages.screener import screen_candidates
from backend.tests.selection_funnel.conftest import make_snap

PET_MSG = "帮我给宠物零食做智能选品，目标毛利率20%"


def _golden_pet_snaps() -> list[dict]:
    """宠物零食 8 形态：手工复算基准（见各断言注释）。"""
    return [
        # g-a 强候选：贡献利润率 27.6% ≥ 20% 存活
        make_snap(url="g-a", title="宠物零食冻干鸡肉 500g", price=129.0,
                  original_price=169.0, rating=4.8, review_count=12000,
                  highlights="冻干,大容量", snapshot_id=1),
        # g-b 评分 3.2 < 4.0 → screen 淘汰
        make_snap(url="g-b", title="宠物零食磨牙棒", price=99.0,
                  rating=3.2, review_count=500, highlights="磨牙", snapshot_id=2),
        # g-c 评价 5 < 10 → screen 淘汰
        make_snap(url="g-c", title="宠物零食小鱼干", price=149.0,
                  rating=4.5, review_count=5, highlights="小鱼干", snapshot_id=3),
        # g-d 低价款：估计成本口径贡献利润率 14.3% < 20% → econ 淘汰
        #（fee=round(29×0.055,2)=1.59 银行家舍入，net=4.14，margin=0.1428）
        make_snap(url="g-d", title="宠物零食鸡肉干 300g", price=29.0,
                  rating=4.7, review_count=300, highlights="鸡肉干", snapshot_id=4),
        # g-e 与 g-a 同 url → pool 去重
        make_snap(url="g-a", title="宠物零食冻干鸡肉 500g", price=129.0,
                  rating=4.8, review_count=12000, highlights="冻干,大容量",
                  snapshot_id=5),
        # g-f 缺售价 → econ 无利润数据保留并披露
        make_snap(url="g-f", title="宠物零食试吃装", price=None,
                  rating=4.5, review_count=200, highlights="试吃装", snapshot_id=6),
        # g-g 缺评价数有销量 → screen 以销量代判热度线
        make_snap(url="g-g", title="宠物零食鸡肉粒 100g", price=69.0,
                  original_price=89.0, rating=4.3, review_count=None,
                  sales=8000, highlights="鸡肉粒,低盐", snapshot_id=7),
        # g-h 异常价格 0 → econ 无利润数据保留并披露
        make_snap(url="g-h", title="宠物零食清仓特价品", price=0.0,
                  rating=4.6, review_count=150, highlights="清仓特价",
                  snapshot_id=8),
    ]


def _stage(logs, stage):
    return next(log for log in logs if log["stage"] == stage)


# ── 纯函数层：口径与边界形态 ──────────────────────────────

def test_golden_econ_dual_margin_fields():
    """贡献利润率门控 + 商品毛利率展示，双口径同源可复算。"""
    econ = calc_unit_economics(price=100.0, unit_cost=40.0)
    assert econ["margin"] == round(100 - 40 - 5.5 - 5 - 15 - 3, 4) / 100
    assert econ["gross_margin"] == 0.6
    est = calc_unit_economics(price=100.0, unit_cost=None)
    assert est["unit_cost_estimated"] is True
    assert est["gross_margin"] == 0.55
    broken = calc_unit_economics(price=0.0, unit_cost=None)
    assert broken["margin"] is None and broken["gross_margin"] is None


def test_golden_pool_dedup_url_then_title_fallback(patch_stores):
    """去重降级：url 优先；无 url 时 (title, platform)——同款不同规格误合并为已知局限。"""
    patch_stores([
        make_snap(url="", title="宠物零食冻干鸡肉 500g", price=59.0, snapshot_id=1),
        make_snap(url="", title="宠物零食冻干鸡肉 500g", price=65.0, snapshot_id=2),
        make_snap(url="u-x", title="宠物零食冻干鸡肉 250g", price=35.0, snapshot_id=3),
    ])
    pool, _notes, reasons = build_pool(category="宠物零食")
    assert len(pool) == 2
    assert [r["rule"] for r in reasons].count("duplicate") == 1


def test_golden_screen_sales_proxy_disclosed():
    """缺评价数有销量：以销量代判热度线，口径披露不静默。"""
    cands = [{"url": "u-1", "title": "宠物零食冻干鸡肉", "price": 59.0,
              "rating": 4.6, "review_count": None, "sales": 8000}]
    kept, reasons, notes = screen_candidates(cands, "宠物零食", 4.0, 10)
    assert len(kept) == 1 and reasons == []
    assert any("按销量" in n for n in notes)
    assert kept[0]["screening"]["warnings"]


def test_golden_econ_abnormal_price_kept_disclosed():
    """异常价格（0 / 缺失）：不淘汰、不算利润，保留并披露。"""
    cands = [{"url": "u-1", "title": "a", "price": 0.0},
             {"url": "u-2", "title": "b", "price": None}]
    kept, reasons, notes = econ_candidates(
        cands, "宠物零食", min_margin=0.30, unit_cost=None,
        fee_rate=0.055, logistics_fee=5.0, ads_ratio=0.15,
        refund_ratio=0.03, default_cost_ratio=0.45)
    assert len(kept) == 2 and reasons == []
    assert all(c["economics"]["margin"] is None for c in kept)
    assert len(notes) == 2


# ── 入口校验：错误条件进追问，不产误导性空池 ──────────────────

def test_golden_brief_inverted_price_band_need_info(funnel_graph, patch_stores):
    patch_stores(_golden_pet_snaps())
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给宠物零食做智能选品 80-50元"))
    assert final["status"] == "need_info"
    assert "下限大于上限" in final["final_answer"]


def test_golden_brief_margin_out_of_range_need_info(funnel_graph, patch_stores):
    patch_stores(_golden_pet_snaps())
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给宠物零食做智能选品，目标毛利率150%"))
    assert final["status"] == "need_info"
    assert "0-100%" in final["final_answer"]


def test_golden_brief_unknown_platform_warns_and_runs(funnel_graph, patch_stores):
    """未知平台：软提示随漏斗披露，仍按原样过滤（此处 Honest 空池）。"""
    patch_stores(_golden_pet_snaps())
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我做智能选品",
        funnel_context={"category": "宠物零食", "platform": "小红书"}))
    assert final["status"] == "empty_pool"
    assert any("不在常见清单" in n for n in final["notes"])


# ── 图级 golden：固定快照 → 固定计数 / 顺序 / 淘汰原因 ───────────

def test_golden_pet_snacks_full_funnel(funnel_graph, patch_stores):
    patch_stores(_golden_pet_snaps())
    final = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message=PET_MSG))
    assert final["status"] == "ok"

    # 手工复算：pool 8→7（g-e 重复 url）；screen 7→5（g-b 评分 / g-c 评价数）；
    # econ 5→4（g-d 贡献利润率 14.3%<20%）；rank 全取（4 ≤ Top-5）
    assert _stage(final["stage_logs"], "pool")["kept"] == 7
    screen_log = _stage(final["stage_logs"], "screen")
    assert screen_log["kept"] == 5
    assert {r["rule"] for r in screen_log["reasons"]} == {"min_rating", "min_reviews"}
    econ_log = _stage(final["stage_logs"], "econ")
    assert econ_log["kept"] == 4 and econ_log["dropped"] == 1

    # 排序确定性（手工复算分值序：强候选 > 异常价 > 缺售价 > 缺评价）
    assert [c["url"] for c in final["candidates"]] == ["g-a", "g-h", "g-f", "g-g"]

    answer = final["final_answer"]
    assert "贡献利润率" in answer and "商品毛利率" in answer
    assert "贡献利润率 14.3% < 目标 20%" in answer
    assert "运行配置" in answer
    assert "unit_cost_estimated" not in answer  # 估计标记走 warnings 而非字段名

    snap = final["config_snapshot"]
    assert snap["min_margin"] == 0.2
    assert "import" in snap["pool_sources"] and "watchlist" in snap["pool_sources"]
    assert snap["top_n"] == 5


def test_golden_dress_category_econ_gate(funnel_graph, patch_stores):
    """服饰类目：默认 30% 贡献利润率线，两件被淘一件贴线存活。"""
    patch_stores([
        make_snap(url="f-a", title="连衣裙碎花夏款", price=199.0,
                  original_price=299.0, rating=4.7, review_count=3000,
                  category="连衣裙", snapshot_id=11),
        make_snap(url="f-b", title="连衣裙基础款", price=89.0,
                  rating=4.5, review_count=800, category="连衣裙", snapshot_id=12),
        make_snap(url="f-c", title="连衣裙高端款", price=409.0,
                  original_price=499.0, rating=4.8, review_count=5000,
                  category="连衣裙", snapshot_id=13),
    ])
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给连衣裙做智能选品"))
    assert final["status"] == "ok"
    assert _stage(final["stage_logs"], "pool")["kept"] == 3
    assert _stage(final["stage_logs"], "screen")["kept"] == 3
    econ_log = _stage(final["stage_logs"], "econ")
    assert econ_log["kept"] == 1 and econ_log["dropped"] == 2
    assert [c["url"] for c in final["candidates"]] == ["f-c"]
    top = final["candidates"][0]
    assert top["economics"]["gross_margin"] == round((409.0 - 184.05) / 409.0, 4)
    assert any("贡献利润率" in r["value"] for r in econ_log["reasons"])


def test_golden_home_empty_pool_with_config(funnel_graph, patch_stores):
    """全淘空：诚实披露所在层 + 运行配置随报告（放宽哪个阈值一目了然）。"""
    patch_stores(_golden_pet_snaps())
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给收纳盒做智能选品"))
    assert final["status"] == "empty_pool"
    assert final["candidates"] == []
    answer = final["final_answer"]
    assert "全部淘汰" in answer and "没有命中类目" in answer
    assert "运行配置" in answer and "贡献利润率线" in answer


def test_golden_determinism(funnel_graph, patch_stores):
    patch_stores(_golden_pet_snaps())
    a1 = funnel_graph.invoke(new_selection_funnel_graph_input(user_message=PET_MSG))
    a2 = funnel_graph.invoke(new_selection_funnel_graph_input(user_message=PET_MSG))
    assert a1["final_answer"] == a2["final_answer"]
    assert a1["candidates"] == a2["candidates"]
    assert a1["config_snapshot"] == a2["config_snapshot"]


# ── 终态四分：系统异常 ≠ 淘空，trace 可区分 ───────────────────

def test_adapter_failed_status_stamps_trace(monkeypatch):
    from backend.observability import tracer
    from backend.orchestration.graph import selection_funnel_graph_node as mod

    class _BoomGraph:
        def invoke(self, *_a, **_k):
            raise RuntimeError("boom")

    class _FakeTrace:
        def __init__(self):
            self.tags = {}
            self.metadata = {}

    fake = _FakeTrace()
    monkeypatch.setattr(mod, "get_selection_funnel_graph", lambda: _BoomGraph())
    monkeypatch.setattr(tracer.trace_collector, "current", lambda: fake)
    out = mod.selection_funnel_graph_node(
        {"question": "帮我做智能选品", "session_id": "s1"})
    assert out["final_answer"] == mod._FALLBACK_ANSWER
    assert fake.tags["funnel_status"] == "failed"


def test_adapter_failed_without_trace_still_falls_back(monkeypatch):
    from backend.orchestration.graph import selection_funnel_graph_node as mod

    class _BoomGraph:
        def invoke(self, *_a, **_k):
            raise RuntimeError("boom")

    monkeypatch.setattr(mod, "get_selection_funnel_graph", lambda: _BoomGraph())
    out = mod.selection_funnel_graph_node(
        {"question": "帮我做智能选品", "session_id": "s1"})
    assert out["final_answer"] == mod._FALLBACK_ANSWER
