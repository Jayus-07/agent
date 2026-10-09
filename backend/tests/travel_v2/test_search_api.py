from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.travel.agents.planning_agent import PlanningAgent
from backend.travel.agents.research_agent import ResearchAgent
from backend.travel.services.live_search_service import LiveSearchError
from backend.travel.services import live_search_service
from backend.travel_v2.api.router import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "X-User-Id": "user-a",
        "X-Auth-Type": "jwt",
        "X-Tenant-Id": "tenant-a",
    }


def test_food_search_uses_research_agent_and_returns_live_merchant_fields(monkeypatch):
    monkeypatch.setattr(ResearchAgent, "search_food", lambda _self, city: {
        "merchants": [{
            "id": "food-1", "name": "湖滨餐厅", "address": "西湖区",
            "rating": 4.5, "open_status": "营业中", "lat": 30.2,
            "lng": 120.1, "source": "amap", "updated_at": "2026-10-09T08:00:00+08:00",
        }],
    })

    response = _client().post("/travel/v2/search/food", headers=_headers(), json={"city": "杭州"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["source"] == "amap"
    assert body["results"][0]["name"] == "湖滨餐厅"
    assert body["results"][0]["selection_id"]


def test_empty_hotel_search_is_distinct_from_tool_failure(monkeypatch):
    monkeypatch.setattr(ResearchAgent, "search_hotels", lambda _self, city: {"merchants": []})

    response = _client().post("/travel/v2/search/hotels", headers=_headers(), json={"city": "杭州"})

    assert response.status_code == 200
    assert response.json()["status"] == "no_results"
    assert response.json()["results"] == []


def test_train_search_reuses_planning_agent_and_attaches_only_returned_prices(monkeypatch):
    monkeypatch.setattr(PlanningAgent, "search_trains", lambda *_args, **_kwargs: {
        "from_station": "上海", "to_station": "杭州", "date": "2026-10-20",
        "source": "12306", "trains": [{
            "train_no": "G1", "start_time": "08:00", "arrive_time": "09:00",
            "duration": "1小时", "duration_min": 60, "seats": {"二等座": "有"},
            "source": "12306", "updated_at": "2026-10-09T08:00:00+08:00",
        }],
    })
    monkeypatch.setattr(PlanningAgent, "attach_train_prices", lambda _self, data, **_kwargs: {
        **data, "trains": [{**data["trains"][0], "prices": {"二等座": 73.0}}],
    })

    response = _client().post("/travel/v2/search/trains", headers=_headers(), json={
        "from_station": "上海", "to_station": "杭州", "travel_date": "2026-10-20",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["source"] == "12306"
    assert body["results"][0]["train_code"] == "G1"
    assert body["results"][0]["fares"] == {"二等座": 73.0}


def test_places_search_uses_existing_tencent_provider_without_inventing_hours_or_prices(monkeypatch):
    monkeypatch.setattr(live_search_service, "search_places", lambda **_kwargs: {
        "pois": [{
            "id": "poi-1", "name": "西湖博物馆", "lat": 30.24,
            "lng": 120.15, "address": "杭州市西湖区", "category": "博物馆",
        }],
    })

    response = _client().post("/travel/v2/search/places", headers=_headers(), json={
        "city": "杭州", "keyword": "博物馆",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "places"
    assert body["status"] == "success"
    assert body["source"] == "tencent:lbs"
    candidate = body["results"][0]
    assert candidate["place"]["place_id"] == "poi-1"
    assert candidate["place"]["facts"]["opening_hours"] is None
    assert candidate["place"]["facts"]["ticket_price_cny"] is None
    assert candidate["place"]["facts"]["verification"] == "unknown"


def test_places_search_provider_failure_is_not_reported_as_no_results(monkeypatch):
    def unavailable(**_kwargs):
        raise LiveSearchError("provider unavailable", category="provider_unavailable")

    monkeypatch.setattr(live_search_service, "search_places", unavailable)
    response = _client().post("/travel/v2/search/places", headers=_headers(), json={
        "city": "杭州", "keyword": "博物馆",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "provider_unavailable"
    assert response.json()["results"] == []
    assert response.json()["source"] == "tencent:lbs"


def test_search_requires_authenticated_travel_identity():
    response = _client().post("/travel/v2/search/food", json={"city": "杭州"})

    assert response.status_code == 401


def test_tool_failure_categories_remain_distinct_from_no_results(monkeypatch):
    def timeout(_self, _city):
        raise LiveSearchError("upstream timeout", category="network_timeout")

    monkeypatch.setattr(ResearchAgent, "search_food", timeout)
    response = _client().post("/travel/v2/search/food", headers=_headers(), json={"city": "杭州"})

    assert response.status_code == 200
    assert response.json()["status"] == "network_timeout"
    assert response.json()["results"] == []


def test_api_boundary_classifies_provider_and_business_failures(monkeypatch):
    monkeypatch.setattr(
        ResearchAgent, "search_hotels",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(
        PlanningAgent, "search_trains",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid station")),
    )

    hotel = _client().post("/travel/v2/search/hotels", headers=_headers(), json={"city": "杭州"})
    train = _client().post("/travel/v2/search/trains", headers=_headers(), json={
        "from_station": "A", "to_station": "B", "travel_date": "2026-10-20",
    })

    assert hotel.json()["status"] == "provider_unavailable"
    assert train.json()["status"] == "business_failure"
