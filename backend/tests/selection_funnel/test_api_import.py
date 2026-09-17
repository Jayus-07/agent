"""tests/selection_funnel/test_api_import.py — 导入通道 API 冒烟

TestClient 只挂本域 router（不拉全 app），配合 conftest 的
isolated_import_store 内存替身，单测绝不打真实 PG（agent_business）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.app.api.routes.selection_funnel import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


TSV = "商品标题\t价格\t评分\t评价人数\t平台\t类目\n冻干鸡肉 500g\t149\t4.8\t1.5万\t淘宝\t宠物零食\n"


def test_import_text_and_list(client):
    resp = client.post("/selection-funnel/import/text", json={
        "content": TSV, "category": "", "platform": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1 and body["batch_id"].startswith("imp-")

    listed = client.get("/selection-funnel/import/candidates",
                        params={"category": "宠物零食"}).json()
    assert listed["count"] == 1
    assert listed["items"][0]["title"] == "冻干鸡肉 500g"


def test_import_text_bad_payload_400(client):
    resp = client.post("/selection-funnel/import/text", json={"content": "这不是表格"})
    assert resp.status_code == 400
    assert "导入失败" in resp.json()["detail"]


def test_clear_batch_404(client):
    assert client.delete("/selection-funnel/import/batch/imp-none").status_code == 404
