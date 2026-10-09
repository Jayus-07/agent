from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any, Literal

from pydantic import Field

from backend.travel_v2.models.trip import StrictModel

TravelSearchStatus = Literal[
    "success", "no_results", "business_failure", "network_timeout",
    "provider_unavailable", "disabled",
]


class MerchantSearchRequest(StrictModel):
    city: str = Field(min_length=1, max_length=160)


class PlaceSearchRequest(StrictModel):
    city: str = Field(min_length=1, max_length=160)
    keyword: str = Field(min_length=1, max_length=160)


class TrainSearchRequest(StrictModel):
    from_station: str = Field(min_length=1, max_length=160)
    to_station: str = Field(min_length=1, max_length=160)
    travel_date: Date


class TravelSearchResponse(StrictModel):
    kind: Literal["food", "hotel", "train", "places"]
    status: TravelSearchStatus
    source: str
    queried_at: datetime
    message: str
    results: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    disclosure: str = ""
