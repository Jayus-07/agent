"""tests/selection_funnel/test_pool_sources.py — 多源建池行为

覆盖：导入源优先级 / watchlist 兜底 / 去重（url 与 title+platform 双口径）/
未知源提示 / 空池提示导入通道 / 导入数据端到端走完整漏斗（图级验收）。
"""
from __future__ import annotations

import pytest

from backend.tests.selection_funnel.conftest import make_snap
from backend.selection_funnel.stages import pool_builder
from backend.selection_funnel.stages.pool_builder import build_pool


def _seed_import(rows: list[dict]):
    from backend.selection_funnel import import_pool
    import_pool.get_import_store().add_batch(rows, category="")


IMPORT_ROW = {
    "title": "导入冻干鸡肉 500g", "platform": "淘宝", "price": 149.0,
    "rating": 4.8, "review_count": 12000, "category": "宠物零食",
    "url": "", "promo_text": "", "highlights": "",
}


# ── 多源合并 ──────────────────────────────────────────────────────────
def test_import_priority_over_watchlist(patch_stores):
    """同 url 双源 → 导入源在前，保留导入版数据。"""
    _seed_import([IMPORT_ROW | {"url": "u-imp", "price": 149.0}])
    patch_stores([make_snap(url="u-imp", price=59.0, title="监控版冻干鸡肉")])
    pool, notes, _, _ = build_pool("宠物零食")
    assert len(pool) == 1
    assert pool[0]["price"] == 149.0


def test_watchlist_fallback_when_import_empty(patch_stores):
    """导入池空 → watchlist 兜底，note 提示导入通道可用。"""
    patch_stores([make_snap()])
    pool, notes, _, _ = build_pool("宠物零食")
    assert len(pool) == 1 and pool[0]["title"].startswith("宠物零食")
    assert any("导入候选池为空" in n for n in notes)


def test_dedup_no_url_by_title_platform(patch_stores):
    """无 url 候选按 (title, platform) 去重，且保留最新批次（2026-09-18 语义修正）。"""
    _seed_import([IMPORT_ROW, IMPORT_ROW | {"price": 155.0}])
    patch_stores([])
    pool, _notes, reasons, _sources = build_pool("宠物零食")
    assert len(pool) == 1
    assert any(r["rule"] == "duplicate" for r in reasons)
    assert pool[0]["price"] == 155.0, "同款多批次应保留最新价格（列表序靠后）"


def test_dedup_by_url_keeps_latest(patch_stores):
    """有 url 同款跨批次去重：留最新价；不同 url 不误伤。"""
    _seed_import([
        IMPORT_ROW | {"url": "u-1", "price": 100.0},
        IMPORT_ROW | {"url": "u-1", "price": 80.0},
        IMPORT_ROW | {"url": "u-2", "price": 60.0},
    ])
    patch_stores([])
    pool, _notes, reasons, _sources = build_pool("宠物零食")
    assert len(pool) == 2
    by_url = {c["url"]: c for c in pool}
    assert by_url["u-1"]["price"] == 80.0, "同款留最新批次"
    assert by_url["u-2"]["price"] == 60.0
    assert len([r for r in reasons if r["rule"] == "duplicate"]) == 1


def test_unknown_source_skipped_with_note(monkeypatch):
    from backend.config import selection_funnel as cfg
    monkeypatch.setattr(cfg, "SELECTION_FUNNEL_POOL_SOURCES", ("import", "nope"))
    pool, notes, _, _ = build_pool("宠物零食")
    assert pool == []
    assert any("未知数据源「nope」" in n for n in notes)


def test_empty_pool_note_mentions_import_channel(patch_stores):
    patch_stores([])
    pool, notes, _, _ = build_pool("宠物零食")
    assert pool == []
    assert any("导入池" in n and "监控" in n for n in notes)


def test_single_source_failure_degrades(patch_stores, monkeypatch):
    """导入源读取炸 → 跳过该源，watchlist 仍可建池，不炸漏斗。"""
    from backend.selection_funnel import import_pool

    def _boom():
        raise RuntimeError("db locked")
    monkeypatch.setattr(import_pool, "get_import_store", _boom)
    patch_stores([make_snap()])
    pool, notes, _, _ = build_pool("宠物零食")
    assert len(pool) == 1
    assert any("导入候选池读取失败" in n for n in notes)


# ── 图级端到端：导入海选数据走完整漏斗 ────────────────────────────────
_E2E_TSV = (
    "商品标题\t价格\t评分\t评价人数\t平台\t类目\t促销\n"
    "宠物零食冻干鸡肉 500g\t149\t4.8\t1.5万\t淘宝\t宠物零食\t满300减30\n"
    "宠物零食冻干鸭肉 500g\t169\t4.6\t8000\t淘宝\t宠物零食\t第二件半价\n"
    "宠物零食冻干鹌鹑 200g\t199\t4.9\t2万\t拼多多\t宠物零食\t直播间专属价\n"
    "宠物零食鸡肉绕薯条 300g\t139\t4.5\t3000\t拼多多\t宠物零食\t新客立减10\n"
    "宠物零食洁齿骨 250g\t259\t4.7\t6000\t淘宝\t宠物零食\t买二送一\n"
    "宠物零食冻干鸡肉粒 100g\t129\t4.4\t500\t淘宝\t宠物零食\t\n"
    "宠物零食冻干多春鱼 300g\t189\t4.3\t1200\t拼多多\t宠物零食\t限时直降20\n"
    "宠物零食羊奶酪粒 200g\t219\t4.2\t900\t拼多多\t宠物零食\t收藏加购送试吃\n"
)


def test_graph_end_to_end_with_import_pool(patch_stores, funnel_graph):
    """导入 8 行海选数据 → 完整漏斗出 Top-5 报告（本轮拍板的核心验收）。

    2026-09-17 口径升级：经济测算含 3% 退款损耗后，纯估计成本（45% 售价）
    的低价款不再达 30% 毛利线 —— 需求给出成本约束（成本60元以内），
    129/139 元两款在 econ 层被如实淘汰，漏斗保留真实淘汰记录。
    """
    from backend.selection_funnel.import_pool import import_table
    _batch, n, _notes = import_table(_E2E_TSV)
    assert n == 8
    patch_stores([])  # 监控池为空——纯导入源驱动

    from backend.selection_funnel.graph_state import new_selection_funnel_graph_input
    state = new_selection_funnel_graph_input("给宠物零食做一次智能选品，成本60元以内")
    result = funnel_graph.invoke(state, config={"recursion_limit": 25})

    assert result["status"] == "ok"
    ranked = result.get("candidates") or []
    assert 1 <= len(ranked) <= 5
    assert result["final_answer"]
    # 池层日志：8 进 0 出局，逐层有淘汰记录
    pool_log = next(l for l in result["stage_logs"] if l["stage"] == "pool")
    assert pool_log["kept"] == 8 and pool_log["dropped"] == 0
    # econ 层：退款损耗口径下 129/139 元两款被淘汰（成本 60 元固定口径可复算）
    econ_log = next(l for l in result["stage_logs"] if l["stage"] == "econ")
    assert econ_log["dropped"] == 2 and econ_log["kept"] == 6
    # 排序非增（同分按 margin/url 决胜，此处只验总分单调）
    totals = [(c.get("score") or {}).get("total") or 0.0 for c in ranked]
    assert all(totals[i] >= totals[i + 1] for i in range(len(totals) - 1))
    assert "宠物零食" in result["final_answer"]
