from __future__ import annotations

from fastapi import APIRouter, Request

from backend.travel_v2.api.trips import _scope
from backend.travel_v2.models.search import (
    MerchantSearchRequest,
    PlaceSearchRequest,
    TrainSearchRequest,
)
from backend.travel_v2.services import search_service

router = APIRouter(tags=["旅游 V2 实时查询"])


@router.post("/travel/v2/search/food", summary="查询真实美食商户，不修改行程")
def search_food(payload: MerchantSearchRequest, request: Request):
    _scope(request)
    try:
        return search_service.search_food(payload.city)
    except Exception as exc:
        return search_service.failed_search("food", exc)


@router.post("/travel/v2/search/hotels", summary="查询真实酒店商户，不修改行程")
def search_hotels(payload: MerchantSearchRequest, request: Request):
    _scope(request)
    try:
        return search_service.search_hotels(payload.city)
    except Exception as exc:
        return search_service.failed_search("hotel", exc)


@router.post("/travel/v2/search/places", summary="查询真实地点候选，不修改行程")
def search_places(payload: PlaceSearchRequest, request: Request):
    _scope(request)
    try:
        return search_service.search_places(
            city=payload.city, keyword=payload.keyword,
        )
    except Exception as exc:
        return search_service.failed_search("places", exc)


@router.post("/travel/v2/search/trains", summary="查询真实动车车次，不修改行程")
def search_trains(payload: TrainSearchRequest, request: Request):
    _scope(request)
    try:
        return search_service.search_trains(
            from_station=payload.from_station,
            to_station=payload.to_station,
            travel_date=payload.travel_date.isoformat(),
        )
    except Exception as exc:
        return search_service.failed_search("train", exc)
