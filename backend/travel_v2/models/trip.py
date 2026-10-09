from __future__ import annotations

from datetime import date as Date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TravelersV2(StrictModel):
    adults: int = Field(ge=1, le=50)
    children: int = Field(default=0, ge=0, le=50)


class PlaceSelectionV2(StrictModel):
    place_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    preference: Literal["must_visit", "interested", "excluded"]


class BudgetV2(StrictModel):
    amount: float | None = Field(default=None, ge=0)
    currency: Literal["CNY"] = "CNY"


class TripBriefV2(StrictModel):
    origin: str = Field(default="", max_length=160)
    destination: str = Field(min_length=1, max_length=160)
    timezone: str = Field(min_length=1, max_length=80)
    start_date: Date | None = None
    day_count: int = Field(ge=1, le=60)
    travelers: TravelersV2
    budget: BudgetV2 = Field(default_factory=BudgetV2)
    pace: Literal["relaxed", "balanced", "intense"] = "balanced"
    interests: list[str] = Field(default_factory=list, max_length=64)
    requirements: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def timezone_must_be_known(self) -> "TripBriefV2":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self


class PlaceFactsV2(StrictModel):
    opening_hours: Any | None = None
    ticket_price_cny: float | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    open_status: str | None = Field(default=None, max_length=80)
    open_time_today: str | None = Field(default=None, max_length=200)
    avg_cost_cny: float | None = Field(default=None, ge=0)
    verification: Literal["verified", "estimated", "unknown"]
    source: str = Field(min_length=1, max_length=160)
    observed_at: datetime | None = None


class PlaceSnapshotV2(StrictModel):
    place_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = Field(default=None, max_length=500)
    facts: PlaceFactsV2

    @model_validator(mode="after")
    def coordinate_pair_is_complete(self) -> "PlaceSnapshotV2":
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must both be known or both be null")
        return self


class MerchantSnapshotV2(StrictModel):
    """来自商户查询的事实快照；不包含房态或预订承诺。"""

    merchant_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    rating: float | None = Field(default=None, ge=0, le=5)
    open_status: str | None = Field(default=None, max_length=80)
    open_time_today: str | None = Field(default=None, max_length=200)
    avg_cost_cny: float | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=160)
    observed_at: datetime

    @model_validator(mode="after")
    def coordinate_pair_is_complete(self) -> "MerchantSnapshotV2":
        if (self.lat is None) != (self.lng is None):
            raise ValueError("merchant lat and lng must both be known or both be null")
        return self


class LodgingArrangementV2(StrictModel):
    selection_id: str = Field(min_length=1, max_length=200)
    merchant: MerchantSnapshotV2
    check_in: Date
    check_out: Date
    source: str = Field(min_length=1, max_length=160)
    queried_at: datetime
    verification: Literal["verified", "estimated", "unknown"] = "unknown"
    booking_status: Literal["not_booked"] = "not_booked"
    nightly_price_cny: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def dates_and_price_are_truthful(self) -> "LodgingArrangementV2":
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.nightly_price_cny is None and self.verification == "verified":
            raise ValueError("lodging price cannot be marked verified when nightly price is missing")
        return self


class IntercityTrainArrangementV2(StrictModel):
    selection_id: str = Field(min_length=1, max_length=200)
    train_code: str = Field(min_length=1, max_length=32)
    travel_date: Date
    departure_station: str = Field(min_length=1, max_length=160)
    arrival_station: str = Field(min_length=1, max_length=160)
    departure_time: str
    arrival_time: str
    duration_min: int = Field(ge=1, le=48 * 60)
    tickets: dict[str, str | int] = Field(default_factory=dict)
    fares: dict[str, float | str] = Field(default_factory=dict)
    ticket_source: str = Field(min_length=1, max_length=160)
    fare_source: str | None = Field(default=None, max_length=160)
    queried_at: datetime
    verification: Literal["verified", "delayed", "unknown"] = "unknown"
    booking_status: Literal["not_booked"] = "not_booked"

    @model_validator(mode="after")
    def times_are_local_clock_values(self) -> "IntercityTrainArrangementV2":
        try:
            time.fromisoformat(self.departure_time)
            time.fromisoformat(self.arrival_time)
        except ValueError as exc:
            raise ValueError("train times must be ISO local times") from exc
        if len(self.departure_time) not in (5, 8) or len(self.arrival_time) not in (5, 8):
            raise ValueError("train times must use HH:MM or HH:MM:SS")
        return self


class TripArrangementsV2(StrictModel):
    lodgings: list[LodgingArrangementV2] = Field(default_factory=list, max_length=60)
    intercity_trains: list[IntercityTrainArrangementV2] = Field(default_factory=list, max_length=120)


class TripItemV2(StrictModel):
    item_id: str = Field(min_length=1, max_length=160)
    kind: Literal["place", "activity"]
    activity_type: Literal[
        "meal", "rest", "shopping", "free_time", "custom",
    ] | None = None
    title: str = Field(min_length=1, max_length=200)
    start_time: str | None = None
    duration_min: int = Field(ge=0, le=24 * 60)
    fixed_start: bool = False
    place: PlaceSnapshotV2 | None = None
    must_visit: bool = False
    locked: bool = False
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def item_kind_matches_location(self) -> "TripItemV2":
        if self.kind == "place" and self.place is None:
            raise ValueError("place item requires a place snapshot")
        if self.kind == "activity" and self.activity_type is None:
            raise ValueError("activity item requires activity_type")
        if self.start_time is not None:
            try:
                time.fromisoformat(self.start_time)
            except ValueError as exc:
                raise ValueError("start_time must be an ISO local time") from exc
            if len(self.start_time) not in (5, 8):
                raise ValueError("start_time must use HH:MM or HH:MM:SS")
        return self


class TripLegV2(StrictModel):
    leg_id: str = Field(min_length=1, max_length=160)
    from_item_id: str = Field(min_length=1, max_length=160)
    to_item_id: str = Field(min_length=1, max_length=160)
    selected_mode: Literal["walk", "drive", "transit", "taxi"]
    duration_min: int | None = Field(default=None, ge=0)
    distance_m: int | None = Field(default=None, ge=0)
    cost_cny: float | None = Field(default=None, ge=0)
    reliability: Literal["verified", "estimated", "unavailable"]
    source: str | None = Field(default=None, max_length=160)
    observed_at: datetime | None = None

    @model_validator(mode="after")
    def endpoints_must_differ(self) -> "TripLegV2":
        if self.from_item_id == self.to_item_id:
            raise ValueError("leg endpoints must differ")
        if self.reliability == "unavailable" and any(
            value is not None
            for value in (self.duration_min, self.distance_m, self.cost_cny)
        ):
            raise ValueError("unavailable leg cannot contain estimated route values")
        return self


class TripDayV2(StrictModel):
    day_id: str = Field(min_length=1, max_length=160)
    date: Date | None = None
    title: str = Field(min_length=1, max_length=200)
    items: list[TripItemV2] = Field(default_factory=list, max_length=100)
    legs: list[TripLegV2] = Field(default_factory=list, max_length=100)


class TripTotalsV2(StrictModel):
    currency: Literal["CNY"] = "CNY"
    estimated_cost: float | None = Field(default=None, ge=0)
    transit_min: int = Field(default=0, ge=0)
    distance_m: int | None = Field(default=None, ge=0)


class TripHealthIssueV2(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    severity: Literal["info", "warning", "error"]
    day_id: str | None = None
    item_id: str | None = None
    message: str = Field(min_length=1, max_length=1000)


class TripHealthV2(StrictModel):
    status: Literal["ok", "needs_attention", "blocked"]
    issues: list[TripHealthIssueV2] = Field(default_factory=list, max_length=500)


class TripDocumentV2(StrictModel):
    schema_version: Literal[2] = 2
    brief: TripBriefV2
    selections: list[PlaceSelectionV2] = Field(default_factory=list, max_length=500)
    days: list[TripDayV2] = Field(min_length=1, max_length=60)
    arrangements: TripArrangementsV2 = Field(default_factory=TripArrangementsV2)
    totals: TripTotalsV2 = Field(default_factory=TripTotalsV2)
    health: TripHealthV2 = Field(default_factory=lambda: TripHealthV2(status="ok"))

    @model_validator(mode="after")
    def validate_trip_references_and_calendar(self) -> "TripDocumentV2":
        if self.brief.day_count != len(self.days):
            raise ValueError("brief.day_count must equal days.length")

        day_ids: set[str] = set()
        item_ids: set[str] = set()
        leg_ids: set[str] = set()
        all_ids: set[str] = set()
        for index, day in enumerate(self.days):
            for current_id, seen, label in (
                (day.day_id, day_ids, "day_id"),
                (day.day_id, all_ids, "id"),
            ):
                if current_id in seen:
                    raise ValueError(f"duplicate {label}: {current_id}")
                seen.add(current_id)
            if self.brief.start_date is not None:
                expected_date = self.brief.start_date + timedelta(days=index)
                if day.date != expected_date:
                    raise ValueError("day dates must follow brief.start_date in trip timezone")
            current_items: set[str] = set()
            for item in day.items:
                if item.item_id in item_ids or item.item_id in all_ids:
                    raise ValueError(f"duplicate item_id: {item.item_id}")
                item_ids.add(item.item_id)
                all_ids.add(item.item_id)
                current_items.add(item.item_id)
            for leg in day.legs:
                if leg.leg_id in leg_ids or leg.leg_id in all_ids:
                    raise ValueError(f"duplicate leg_id: {leg.leg_id}")
                leg_ids.add(leg.leg_id)
                all_ids.add(leg.leg_id)
                if (leg.from_item_id not in current_items
                        or leg.to_item_id not in current_items):
                    raise ValueError("Leg item reference must point to items in the same day")
                endpoints = {
                    item.item_id: item for item in day.items
                    if item.item_id in (leg.from_item_id, leg.to_item_id)
                }
                missing_coordinates = any(
                    endpoint.kind == "activity"
                    and (endpoint.place is None or endpoint.place.lat is None)
                    for endpoint in endpoints.values()
                )
                if missing_coordinates and (
                    leg.reliability != "unavailable"
                    or any(value is not None for value in (
                        leg.duration_min, leg.distance_m, leg.cost_cny,
                    ))
                ):
                    raise ValueError("coordinate-free activities cannot have invented transit data")

        for issue in self.health.issues:
            if issue.day_id is not None and issue.day_id not in day_ids:
                raise ValueError(f"health issue references unknown day_id: {issue.day_id}")
            if issue.item_id is not None and issue.item_id not in item_ids:
                raise ValueError(f"health issue references unknown item_id: {issue.item_id}")
        selection_ids: set[str] = set()
        for arrangement in [
            *self.arrangements.lodgings,
            *self.arrangements.intercity_trains,
        ]:
            if arrangement.selection_id in selection_ids:
                raise ValueError(
                    f"duplicate arrangement selection_id: {arrangement.selection_id}"
                )
            selection_ids.add(arrangement.selection_id)
        if self.brief.start_date is not None:
            trip_end = self.brief.start_date + timedelta(days=self.brief.day_count)
            for lodging in self.arrangements.lodgings:
                if lodging.check_in < self.brief.start_date or lodging.check_out > trip_end:
                    raise ValueError("lodging dates must fall within the trip dates")
            for train in self.arrangements.intercity_trains:
                if not self.brief.start_date <= train.travel_date < trip_end:
                    raise ValueError("train travel_date must fall within the trip dates")
        self._validate_intercity_schedule()
        return self

    def _validate_intercity_schedule(self) -> None:
        if not self.days or not self.arrangements.intercity_trains:
            return

        def includes_station(station: str, city: str) -> bool:
            normalized_station = station.replace("市", "").replace("省", "")
            normalized_city = city.replace("市", "").replace("省", "")
            return bool(normalized_city and normalized_city in normalized_station)

        first_day = self.days[0]
        last_day = self.days[-1]
        for train in self.arrangements.intercity_trains:
            departure = time.fromisoformat(train.departure_time)
            departure_min = departure.hour * 60 + departure.minute
            arrival_min = departure_min + train.duration_min

            is_outbound = (
                includes_station(train.departure_station, self.brief.origin)
                and includes_station(train.arrival_station, self.brief.destination)
                and first_day.date == train.travel_date
            )
            if is_outbound:
                earliest_visit = arrival_min + 60
                for item in first_day.items:
                    if item.start_time is None:
                        continue
                    item_start = time.fromisoformat(item.start_time)
                    item_start_min = item_start.hour * 60 + item_start.minute
                    if item_start_min < earliest_visit:
                        raise ValueError("首日行程与动车到达后的换乘预留时间冲突")

            is_return = (
                includes_station(train.departure_station, self.brief.destination)
                and includes_station(train.arrival_station, self.brief.origin)
                and last_day.date == train.travel_date
            )
            if is_return:
                latest_visit_end = departure_min - 60
                for item in last_day.items:
                    if item.start_time is None:
                        continue
                    item_start = time.fromisoformat(item.start_time)
                    item_end_min = item_start.hour * 60 + item_start.minute + item.duration_min
                    if item_end_min > latest_visit_end:
                        raise ValueError("末日行程与动车出发前的进站时间冲突")
