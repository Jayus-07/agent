"""tests/selection_funnel/test_selection_funnel_graph.py — 漏斗域图端到端

覆盖（加域规范验收口径）：正常漏斗 / 必填槽位缺失追问 / 候选淘空 /
同一输入两次结果一致 / 预过滤判定与开关 / 域注册。
"""
from backend.config.selection_funnel import SELECTION_FUNNEL_MIN_MARGIN
from backend.selection_funnel.graph_state import new_selection_funnel_graph_input
from backend.tests.selection_funnel.conftest import make_snap

HAPPY_MSG = "帮我给宠物零食做智能选品，目标毛利率20%"


def _happy_snaps() -> list[dict]:
    return [
        make_snap(url="u-a", title="宠物零食冻干鸡肉 500g", price=129.0,
                  rating=4.6, review_count=200, highlights="冻干,大容量",
                  snapshot_id=1),
        make_snap(url="u-b", title="宠物零食磨牙棒", price=99.0,
                  rating=3.2, review_count=500, highlights="磨牙",
                  snapshot_id=2),
        make_snap(url="u-c", title="宠物零食小鱼干", price=149.0,
                  rating=4.5, review_count=5, highlights="小鱼干",
                  snapshot_id=3),
        make_snap(url="u-d", title="宠物零食鸡肉干 300g", price=29.0,
                  rating=4.7, review_count=300, highlights="鸡肉干",
                  snapshot_id=4),
        make_snap(url="u-e", title="宠物零食冻干鸭肉", price=99.0,
                  rating=4.4, review_count=150, highlights="冻干,鸭肉",
                  snapshot_id=5),
    ]


def _stage(logs, stage):
    return next(log for log in logs if log["stage"] == stage)


def test_graph_need_info_asks_category(funnel_graph):
    final = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message="帮我做一次智能选品"))
    assert final["status"] == "need_info"
    assert final["brief_missing"] == ["category"]
    assert "品类" in final["final_answer"]
    assert final["finished"] is True


def test_graph_empty_pool_honest(funnel_graph, patch_stores):
    patch_stores(_happy_snaps())
    final = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message="帮我给蓝牙耳机做智能选品"))
    assert final["status"] == "empty_pool"
    assert final["candidates"] == []
    assert "全部淘汰" in final["final_answer"]
    pool_log = _stage(final["stage_logs"], "pool")
    assert pool_log["kept"] == 0
    assert all(r["rule"] == "category_mismatch" for r in pool_log["reasons"])
    assert any("没有命中类目" in n for n in final["notes"])


def test_graph_happy_path_full_funnel(funnel_graph, patch_stores):
    patch_stores(_happy_snaps())
    final = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message=HAPPY_MSG))
    assert final["status"] == "ok"

    pool_log = _stage(final["stage_logs"], "pool")
    screen_log = _stage(final["stage_logs"], "screen")
    econ_log = _stage(final["stage_logs"], "econ")
    rank_log = _stage(final["stage_logs"], "rank")
    assert pool_log["kept"] == 5
    assert screen_log["kept"] == 3 and screen_log["dropped"] == 2  # u-b 评分 3.2 / u-c 评价 5
    assert econ_log["kept"] == 2 and econ_log["dropped"] == 1      # u-d 毛利不足

    ranked = final["candidates"]
    assert len(ranked) <= 5
    assert {"u-a", "u-e"} <= {c["url"] for c in ranked}
    assert ranked[0]["rank"] == 1
    assert ranked[0]["reason"]

    answer = final["final_answer"]
    assert "漏斗计数" in answer and "推荐 Top-N" in answer
    assert "淘汰明细" in answer
    assert "min_rating" in answer and "min_margin" in answer
    # 建议段给出测款动作
    assert "测款" in answer


def test_graph_deterministic(funnel_graph, patch_stores):
    patch_stores(_happy_snaps())
    a1 = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message=HAPPY_MSG))
    a2 = funnel_graph.invoke(
        new_selection_funnel_graph_input(user_message=HAPPY_MSG))
    assert a1["final_answer"] == a2["final_answer"]
    assert a1["candidates"] == a2["candidates"]


def test_pool_respects_platform_and_price_band(funnel_graph, patch_stores):
    pdd_in = make_snap(url="u-f", title="宠物零食冻干鹌鹑蛋", platform="拼多多",
                       price=149.0, rating=4.5, review_count=80, snapshot_id=6)
    pdd_cheap = make_snap(url="u-g", title="宠物零食鸡肉粒 100g", platform="拼多多",
                          price=59.0, rating=4.4, review_count=60, snapshot_id=7)
    patch_stores(_happy_snaps() + [pdd_in, pdd_cheap])
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给宠物零食做智能选品 拼多多 100-150元"))
    pool_log = _stage(final["stage_logs"], "pool")
    rules = {r["rule"] for r in pool_log["reasons"]}
    assert "platform_mismatch" in rules      # 全部淘宝商品
    assert "price_below_band" in rules       # u-g 59 元低于价格带
    assert pool_log["kept"] == 1             # 只有 u-f 落在拼多多 × 100-150 元


def test_margin_from_message_overrides_default(funnel_graph, patch_stores):
    """消息里给的毛利率 10% 应覆盖默认 30% —— 否则低价品全被误杀。"""
    patch_stores(_happy_snaps())
    final = funnel_graph.invoke(new_selection_funnel_graph_input(
        user_message="帮我给宠物零食做智能选品，目标毛利率10%"))
    econ_log = _stage(final["stage_logs"], "econ")
    # u-d 价格 29：毛利率 ≈17.2%，默认 30% 会被淘汰，10% 阈值下存活
    assert econ_log["kept"] == 3
    assert SELECTION_FUNNEL_MIN_MARGIN == 0.30  # 默认值未被污染


def test_prefilter_semantics():
    from backend.orchestration.graph.selection_funnel_prefilter import (
        is_selection_funnel_request,
    )
    assert is_selection_funnel_request("帮我做智能选品") is True
    assert is_selection_funnel_request("宠物零食选品报告") is True
    assert is_selection_funnel_request("看看这个类目有什么值得推荐的") is True
    # 决策类措辞让给 selection_decision workflow
    assert is_selection_funnel_request("宠物零食选品决策") is False
    assert is_selection_funnel_request("评估蓝牙耳机品类值不值得做") is False
    assert is_selection_funnel_request("这个品类能不能上") is False
    assert is_selection_funnel_request("最近30天销售金额") is False


def test_prefilter_gated_by_enabled_flag(monkeypatch):
    from backend.orchestration.graph import selection_funnel_prefilter as pf
    monkeypatch.setattr(
        "backend.config.selection_funnel.SELECTION_FUNNEL_ENABLED", True)
    hit = pf.try_selection_funnel_prefilter("帮我做智能选品", {"session_id": "s1"})
    assert hit is not None
    assert hit["route_mode"] == "selection_funnel"
    monkeypatch.setattr(
        "backend.config.selection_funnel.SELECTION_FUNNEL_ENABLED", False)
    assert pf.try_selection_funnel_prefilter(
        "帮我做智能选品", {"session_id": "s1"}) is None


def test_domain_registered():
    import backend.selection_funnel.register  # noqa: F401
    from backend.orchestration.domain_registry import domain_graph_registry
    domain = domain_graph_registry.get("selection_funnel")
    assert domain is not None
    assert domain.node_name == "selection_funnel_graph_node"
    assert "selection_funnel" in domain_graph_registry.get_all()
