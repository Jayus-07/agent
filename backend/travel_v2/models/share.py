from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from backend.travel_v2.models.trip import StrictModel


class CreateTripShareRequest(StrictModel):
    revision: int = Field(ge=1)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class CreatedTripShare(StrictModel):
    share_id: str
    trip_id: str
    revision: int
    expires_at: datetime
    created_at: datetime
    share_url: str


class PublicTripBrief(StrictModel):
    destination: str
    timezone: str
    start_date: date | None
    day_count: int


class PublicTripPlace(StrictModel):
    name: str
    address: str | None
    lat: float | None
    lng: float | None


class PublicTripItem(StrictModel):
    kind: Literal["place", "activity"]
    activity_type: (
        Literal[
            "meal",
            "rest",
            "shopping",
            "free_time",
            "custom",
        ]
        | None
    )
    title: str
    start_time: str | None
    duration_min: int
    place: PublicTripPlace | None = None


class PublicTripLeg(StrictModel):
    from_title: str
    to_title: str
    selected_mode: Literal["walk", "drive", "transit", "taxi"]
    duration_min: int | None
    distance_m: int | None


class PublicTripDay(StrictModel):
    date: date | None
    title: str
    items: list[PublicTripItem]
    legs: list[PublicTripLeg]


class PublicLodging(StrictModel):
    name: str
    address: str | None
    check_in: date
    check_out: date
    booking_status: Literal["not_booked"]


class PublicIntercityTrain(StrictModel):
    train_code: str
    travel_date: date
    departure_station: str
    arrival_station: str
    departure_time: str
    arrival_time: str
    duration_min: int
    booking_status: Literal["not_booked"]


class PublicTripArrangements(StrictModel):
    lodgings: list[PublicLodging]
    intercity_trains: list[PublicIntercityTrain]


class PublicTripShare(StrictModel):
    share_id: str
    revision: int
    expires_at: datetime
    title: str
    brief: PublicTripBrief
    days: list[PublicTripDay]
    arrangements: PublicTripArrangements
