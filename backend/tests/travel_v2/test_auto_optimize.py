# -*- coding: utf-8 -*-
"""收编重构 U3 契约测试：自动优选（Auto Optimizer）。

§6 矩阵 Auto Optimizer 行：建议清单结构快照 + 只读语义（零写入）+
逐类降级披露。选入幂等由既有 selections 端点的测试承担（不在此重复）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

from backend.travel_v2.services import auto_optimize_service as svc


def _doc() -> dict:
    start = (date.today() + timedelta(days=30)).isoformat()
    return {
        "trip_id": "trip-1",
        "brief": {"destination": "厦门", "start_date": start, "day_count": 2},
        "days": [{"day_id": "d1", "items": []}, {"day_id": "d2", "items": []}],
    }


def test_suggestions_structure_snapshot(monkeypatch):
    from backend.travel.services import lodging_service
    from backend.travel.services import live_search_service

    monkeypatch.setattr(
        live_search_service, "search_food",
        lambda city: {
            "status": "success", "source": "amap",
            "disclosure": "商户评分来自高德",
            "merchants": [
                {"selection_id": "amap:a", "merchant_id": "B001", "name": "临家闽南菜",
                 "rating": 4.7, "avg_cost_cny": 80, "address": "思明区",
                 "lat": 24.48, "lng": 118.12, "source": "amap",
                 "queried_at": "2026-11-01T08:00:00+00:00"},
                {"selection_id": "amap:b", "name": "无名小店", "rating": None,
                 "avg_cost_cny": None, "address": ""},
                {"selection_id": "amap:c", "merchant_id": "B003", "name": "乌糖沙茶面",
                 "rating": 4.5, "avg_cost_cny": 30, "address": "思明区",
                 "lat": 24.47, "lng": 118.10, "source": "amap",
                 "queried_at": "2026-11-01T08:00:00+00:00"},
            ],
        })
    monkeypatch.setattr(
        lodging_service, "search_lodging",
        lambda *, destination, check_in, check_out: {
            "status": "success", "disclosure": "测试数据披露",
            "options": [
                {"selection_id": "ho:1", "merchant_id": "P1", "name": "海景酒店",
                 "nightly_price_cny": 388.0, "tier": "mid",
                 "availability": "available", "booking_status": "not_booked",
                 "source": "rollinggo:mcp", "observed_at": "2026-11-01T08:00:00+00:00",
                 "deep_link": "https://rollinggo.cn/pages/hotel/detail/index?id=1"},
                {"selection_id": "ho:2", "name": "青旅",
                 "nightly_price_cny": 120.0, "tier": "low",
                 "availability": "limited", "booking_status": "not_booked",
                 "source": "fake:commerce", "observed_at": "2026-11-01T08:00:00+00:00"},
            ],
        })

    out = svc.build_optimization_suggestions(_doc(), "t", "u")
    assert out["trip_id"] == "trip-1"
    assert out["destination"] == "厦门"
    # 择优：餐饮评分降序、住宿价格升序、各含备选与理由
    meals = out["suggestions"]["meals"]
    assert [m["name"] for m in meals] == ["临家闽南菜", "乌糖沙茶面", "无名小店"]
    assert meals[0]["reason"] == "评分最高"
    # F04 选入快照契约：建议行自带构 MerchantSnapshotV2 的全套字段（缺失不补）
    assert meals[0]["merchant_id"] == "B001"
    assert meals[0]["source"] == "amap"
    assert meals[0]["observed_at"] == "2026-11-01T08:00:00+00:00"
    assert (meals[0]["lat"] is None) == (meals[0]["lng"] is None)
    lodgings = out["suggestions"]["lodgings"]
    assert [l["name"] for l in lodgings] == ["青旅", "海景酒店"]
    assert all(l["booking_status"] == "not_booked" for l in lodgings)
    assert lodgings[1]["merchant_id"] == "P1"
    assert lodgings[1]["source"] == "rollinggo:mcp"
    # 跳转型预订：deep_link 原样透传（前端「去预订」按钮的数据源）
    assert lodgings[1]["deep_link"] == "https://rollinggo.cn/pages/hotel/detail/index?id=1"
    assert lodgings[1]["check_in"] is None or isinstance(lodgings[1]["check_in"], str)
    # 建议不是已选：apply 语义提示 + 零写入字段
    assert "selections" in out["apply_hint"]
    assert set(out["suggestions"].keys()) == {"meals", "lodgings"}
    json.dumps(out, ensure_ascii=False)  # 可序列化


def test_each_category_degrades_independently(monkeypatch):
    from backend.travel.services import lodging_service
    from backend.travel.services import live_search_service

    def _boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(live_search_service, "search_food", _boom)
    monkeypatch.setattr(lodging_service, "search_lodging", _boom)
    out = svc.build_optimization_suggestions(_doc(), "t", "u")
    assert out["suggestions"]["meals"] == []
    assert out["suggestions"]["lodgings"] == []
    assert any("餐饮" in d for d in out["disclosures"])
    assert any("住宿" in d for d in out["disclosures"])


def test_no_destination_or_dates_skips_not_fails():
    doc = {"trip_id": "trip-2", "brief": {"destination": ""},
           "days": []}
    out = svc.build_optimization_suggestions(doc, "t", "u")
    assert out["destination"] == ""
    assert out["suggestions"]["meals"] == []


# =============================================
# API 级回归（P5 运行态点测发现：端点曾把 trip 行当 document 传，
# destination 恒空 → 建议全空；真 PG 门控，锁定「传 document」语义）
# =============================================


def test_api_auto_optimize_passes_document_not_trip_row(monkeypatch):
    import os
    import uuid

    os.environ.setdefault("TRAVEL_V2_TEST_POSTGRES", "")
    if os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1":
        import pytest

        pytest.skip("requires TRAVEL_V2_TEST_POSTGRES=1")

    from backend.tests.travel_v2.test_trip_document import valid_document
    from backend.travel.services import lodging_service
    from backend.travel.services import live_search_service
    from backend.travel_v2.api.router import router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        live_search_service, "search_food",
        lambda city: {"status": "success", "source": "amap",
                      "merchants": [{"selection_id": "amap:x", "name": "测试餐厅",
                                     "rating": 4.5, "source": "amap"}]},
        raising=False)
    monkeypatch.setattr(
        lodging_service, "search_lodging",
        lambda *, destination, check_in, check_out: {
            "status": "success", "options": [], "disclosure": ""},
        raising=False)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    owner = f"owner-{uuid.uuid4().hex[:12]}"
    tenant = f"tenant-{uuid.uuid4().hex[:12]}"
    headers = {"X-User-Id": owner, "X-Auth-Type": "jwt",
               "X-Tenant-Id": tenant}
    created = client.post(
        "/travel/v2/trips",
        json={"title": "优选回归", "document": valid_document(),
              "conversation_id": f"travel-{uuid.uuid4().hex}"},
        headers={**headers, "Idempotency-Key": f"opt-api-{uuid.uuid4().hex}"},
    )
    assert created.status_code == 201, created.text
    trip = created.json()

    response = client.post(
        f"/travel/v2/trips/{trip['trip_id']}/auto-optimize",
        json={}, headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    # 修复断言：destination 取自 document.brief（曾恒 ""）
    assert payload["destination"] == "杭州"
    assert payload["trip_id"] == trip["trip_id"]
    assert payload["suggestions"]["meals"], "有餐饮数据源时建议不得为空"

