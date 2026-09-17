"""tests/selection_funnel/test_market_data.py — 赛道数据上传通道单测

覆盖：关键词榜解析/画像（top+机会词）/差评解析/星级清洗/痛点桶聚类/
标题包含匹配/screener 销量代热度/报告段渲染/kind 分发 API。
"""
from __future__ import annotations

import pytest

from backend.selection_funnel.market_data import (
    import_keywords,
    import_reviews,
    market_snapshot,
    pain_points_for,
)
from backend.selection_funnel.stages.screener import screen_candidates

KW_TSV = (
    "关键词\t搜索人气\t竞争度\n"
    "冻干鸡肉\t8800\t12000\n"
    "宠物冻干\t5200\t800\n"
    "鸡肉干\t3100\t9000\n"
    "狗狗零食\t1500\t400\n"
)
RV_TSV = (
    "商品标题\t评论内容\t星级\n"
    "宠物零食冻干鸡肉 500g\t味道太大，狗不爱吃\t2\n"
    "宠物零食冻干鸡肉 500g\t包装破损，快递压坏了\t1\n"
    "宠物零食冻干鸡肉 500g\t克重不足，分量偏小\t3\n"
    "宠物零食冻干鸡肉 500g\t狗狗很爱吃，回购\t5\n"
)


def test_import_keywords_and_snapshot():
    batch, n, notes = import_keywords(KW_TSV, category="宠物零食")
    assert n == 4 and batch.startswith("kw-")
    snap = market_snapshot("宠物零食")
    assert snap["total"] == 4
    assert snap["top"][0]["keyword"] == "冻干鸡肉"          # 人气降序
    opps = [k["keyword"] for k in snap["opportunities"]]
    assert "狗狗零食" in opps                                # 人气第4但竞争最低
    assert "冻干鸡肉" not in opps[:2]                        # 人气高但竞争也高


def test_import_keywords_missing_column_fails():
    batch, n, notes = import_keywords("人气,竞争\n100,50\n", category="")
    assert batch == "" and n == 0 and "导入失败" in notes[0]


def test_import_reviews_and_pain_points():
    _b, n, _ = import_reviews(RV_TSV, category="宠物零食")
    assert n == 4
    rows = pain_points_for(["宠物零食冻干鸡肉 500g"], "宠物零食")
    assert len(rows) == 1
    r = rows[0]
    assert r["negative"] == 3 and r["review_total"] == 4    # 5星不计差评
    pains = dict(r["pains"])
    assert pains.get("气味口感") == 1 and pains.get("物流包装") == 1


def test_pain_points_title_loose_match():
    """评论商品标题是候选标题的子串也能匹配（包含双向）。"""
    import_reviews("商品标题\t评论内容\n冻干鸡肉\t物流很慢\n", category="")
    rows = pain_points_for(["宠物零食冻干鸡肉 500g 大包装"], "")
    assert len(rows) == 1 and rows[0]["pains"][0][0] == "物流包装"


def test_pain_points_empty_when_no_reviews():
    assert pain_points_for(["不存在的商品"], "宠物零食") == []


# ── screener：销量代热度线 ────────────────────────────────────────────
def test_screener_sales_fallback_keeps():
    """榜单数据只有销量没评价数：销量过线 → 保留 + 披露口径。"""
    kept, _reasons, notes = screen_candidates(
        [{"title": "榜一品", "url": "u1", "price": 149.0, "rating": 4.8,
          "review_count": None, "sales": 3000}],
        "宠物零食", min_rating=4.0, min_reviews=10)
    assert len(kept) == 1
    assert any("按销量 3000 代判" in n for n in notes)


def test_screener_sales_fallback_drops():
    kept, reasons, _ = screen_candidates(
        [{"title": "冷门品", "url": "u2", "price": 149.0, "rating": 4.8,
          "review_count": None, "sales": 5}],
        "宠物零食", min_rating=4.0, min_reviews=10)
    assert not kept and reasons[0]["rule"] == "min_reviews"


# ── 报告段 ────────────────────────────────────────────────────────────
def test_report_market_and_pains_sections():
    from backend.selection_funnel.reporter import render_report

    class _B:
        category = "宠物零食"; platform = "拼多多"
        price_min = None; price_max = None; target_margin = None

    import_keywords(KW_TSV, category="宠物零食")
    import_reviews(RV_TSV, category="宠物零食")

    from backend.selection_funnel.reporter import _market_lines, _pain_lines
    market = _market_lines("宠物零食")
    pains = _pain_lines([{"title": "宠物零食冻干鸡肉 500g"}], "宠物零食")
    report = render_report(_B(), [], [], [], market=market, pains=pains)
    assert "### 赛道画像（关键词榜）" in report and "冻干鸡肉" in report
    assert "### 痛点机会（差评实证）" in report and "气味口感" in report


def test_report_sections_absent_without_data():
    from backend.selection_funnel.reporter import _market_lines, _pain_lines

    class _B:
        pass

    assert _market_lines("不存在的类目") == []
    assert _pain_lines([{"title": "没有评论的品"}], "不存在的类目") == []


# ── API kind 分发 ─────────────────────────────────────────────────────
@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.routes.selection_funnel import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_text_import_keywords_kind(client):
    resp = client.post("/selection-funnel/import/text", json={
        "content": KW_TSV, "kind": "keywords", "category": "宠物零食"})
    assert resp.status_code == 200 and resp.json()["count"] == 4
    snap = client.get("/selection-funnel/market/snapshot",
                      params={"category": "宠物零食"}).json()
    assert snap["count"] == 4 and snap["top"][0]["keyword"] == "冻干鸡肉"


def test_api_text_import_reviews_kind(client):
    resp = client.post("/selection-funnel/import/text", json={
        "content": RV_TSV, "kind": "reviews", "category": "宠物零食"})
    assert resp.status_code == 200 and resp.json()["count"] == 4
    pp = client.get("/selection-funnel/reviews/pain-points",
                    params={"category": "宠物零食",
                            "titles": "宠物零食冻干鸡肉 500g"}).json()
    assert pp["count"] == 1 and pp["items"][0]["negative"] == 3


def test_api_text_import_unknown_kind_400(client):
    resp = client.post("/selection-funnel/import/text", json={
        "content": "x", "kind": "nope"})
    assert resp.status_code == 400


def test_market_store_clear_batch_keywords_and_reviews(isolated_market_store):
    kw_batch, n_kw, _ = import_keywords(KW_TSV, category="宠物零食")
    rv_batch, n_rv, _ = import_reviews(RV_TSV, category="宠物零食")
    assert n_kw == 4 and n_rv >= 4
    store = isolated_market_store
    assert len(store.keywords("宠物零食")) == 4
    assert store.clear_batch(kw_batch) == 4
    assert store.keywords("宠物零食") == []
    assert len(store.reviews("宠物零食")) == n_rv  # 差评不受关键词批次清除影响
    assert store.clear_batch(rv_batch) == n_rv
    assert store.clear_batch("kw-none") == 0  # 不存在批次返回 0


def test_api_delete_keyword_batch(client):
    kw_batch, _, _ = import_keywords(KW_TSV, category="宠物零食")
    resp = client.delete(f"/selection-funnel/import/batch/{kw_batch}")
    assert resp.status_code == 200 and resp.json()["removed"] == 4
    assert client.delete(
        f"/selection-funnel/import/batch/{kw_batch}").status_code == 404
