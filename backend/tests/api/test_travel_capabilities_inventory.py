# -*- coding: utf-8 -*-
"""capability-governance G5：旅游域意图目录（域清单协议适配器）。

纯静态清单 + 身份闸，不连 DB：
- 形状与协议一致（domain/range/total/stats_available/intents[].stats 三元组）
- 意图全集 = TravelIntent 枚举实况，描述与 INTENT_DESCRIPTIONS 同源
- 统计诚实降级：stats_available=False、数值 0/None（不伪造）
- require_identity 闸：未认证 401
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import travel_inventory

AUTH_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "g5-inv-user",
    "X-Tenant-Id": "tenant-g5",
}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(travel_inventory.router)
    return TestClient(app, raise_server_exceptions=False)


def test_unauthenticated_rejected(client):
    r = client.get("/travel/capabilities/inventory")
    assert r.status_code == 401


def test_protocol_shape_and_full_catalog(client):
    from backend.travel.core.intent import INTENT_DESCRIPTIONS, TravelIntent

    r = client.get("/travel/capabilities/inventory?range=d7",
                   headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["domain"] == "travel"
    assert body["range"] == "d7"
    assert body["total"] == len(TravelIntent)
    names = [i["intent"] for i in body["intents"]]
    assert set(names) == {i.value for i in TravelIntent}
    for entry in body["intents"]:
        assert entry["description"] == INTENT_DESCRIPTIONS[entry["intent"]]
        assert entry["description"], "描述不得为空"


def test_stats_degraded_honestly(client):
    """统计列待 travel-v2 数据面：显式标注 + 全 0/None，不伪造数值。"""
    r = client.get("/travel/capabilities/inventory", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["stats_available"] is False
    assert body["stats_note"]
    for entry in body["intents"]:
        assert entry["stats"] == {"calls": 0, "handoff_rate": None,
                                  "unresolved": 0}
    # 非法 range 回退默认窗口（静态清单无窗口语义，协议窗口枚举校验）
    r2 = client.get("/travel/capabilities/inventory?range=d365",
                    headers=AUTH_HEADERS)
    assert r2.status_code == 200
    assert r2.json()["range"] == "d7"
