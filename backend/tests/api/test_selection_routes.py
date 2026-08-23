"""selection 路由契约测试 — 最小 FastAPI app + TestClient + mock recommender"""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes.selection import router
from backend.selection.store import reset_selection_store

_REC_PAYLOAD = {
    "items": [{
        "url": "https://a.com", "title": "A", "platform": "taobao",
        "latest_price": 99.0, "currency": "CNY", "rating": 4.8,
        "review_count": 100,
        "score": {"total": 80.0, "breakdown": {}, "notes": []},
        "llm_reason": "理由", "llm_risks": "",
        "latest_crawled_at": "2026-08-23T08:00:00",
        "scored_at": "2026-08-23T10:00:00",
    }],
    "total": 1, "generated_at": "2026-08-23T10:00:00",
}


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestRecommendations:
    def test_returns_items(self):
        with patch("backend.app.api.routes.selection.recommend", return_value=_REC_PAYLOAD):
            resp = _client().get("/selection/recommendations")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["score"]["total"] == 80.0
        assert "generated_at" in body

    def test_invalid_limit_rejected(self):
        resp = _client().get("/selection/recommendations?limit=0")
        assert resp.status_code == 422


class TestScore:
    def test_score_found(self):
        with patch("backend.app.api.routes.selection.score_url",
                   return_value={"url": "https://a.com", "score": {"total": 75.0}}):
            resp = _client().post("/selection/score", json={"url": "https://a.com"})
        assert resp.status_code == 200
        assert resp.json()["score"]["total"] == 75.0

    def test_score_404_when_no_snapshot(self):
        with patch("backend.app.api.routes.selection.score_url", return_value=None):
            resp = _client().post("/selection/score", json={"url": "https://none.com"})
        assert resp.status_code == 404


class TestOthers:
    def test_batch_scores(self):
        with patch("backend.app.api.routes.selection.batch_scores",
                   return_value={"scores": {}, "generated_at": "t"}):
            resp = _client().get("/selection/scores/batch?urls=https%3A%2F%2Fa.com")
        assert resp.status_code == 200

    def test_compare_requires_two_urls(self):
        resp = _client().get("/selection/compare?urls=https%3A%2F%2Fa.com")
        assert resp.status_code == 422

    def test_weights_put_rejects_unknown_key(self):
        resp = _client().put("/selection/weights", json={"weights": {"foo": 0.5}})
        assert resp.status_code == 422

    def test_weights_put_rejects_empty_dict(self):
        resp = _client().put("/selection/weights", json={"weights": {}})
        assert resp.status_code == 422

    def test_weights_get(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SELECTION_DB_PATH", str(tmp_path / "w.db"))
        reset_selection_store()
        try:
            resp = _client().get("/selection/weights")
            assert resp.status_code == 200
            weights = resp.json()["weights"]
            assert weights == {
                "reputation": 0.25,
                "heat": 0.25,
                "price": 0.20,
                "differentiation": 0.15,
                "stability": 0.15,
            }
        finally:
            reset_selection_store()


class TestAliasEndpoint:
    def test_competitor_recommendations_passthrough(self):
        # 别名端点函数内懒 import，patch 目标为源模块
        from backend.app.api.routes.competitor import router as competitor_router

        app = FastAPI()
        app.include_router(competitor_router)
        client = TestClient(app)
        with patch("backend.selection.recommender.recommend",
                   return_value=_REC_PAYLOAD) as mock_rec:
            resp = client.get("/competitor/recommendations?platform=taobao")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["url"] == "https://a.com"
        mock_rec.assert_called_once_with(limit=10, platform="taobao", min_score=0.0)
