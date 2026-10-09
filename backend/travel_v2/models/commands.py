from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from backend.travel_v2.models.trip import (
    IntercityTrainArrangementV2,
    LodgingArrangementV2,
    MerchantSnapshotV2,
    PlaceSnapshotV2,
    StrictModel,
    TripDocumentV2,
)


class CreateTripRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    document: TripDocumentV2


class ReplaceTripDocumentCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    command_type: Literal[
        "replace_document", "structured_edit", "ai_edit", "restore_revision",
    ]
    change_summary: str = Field(default="", max_length=4000)
    document: TripDocumentV2


class RestoreRevisionCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    target_revision: int = Field(ge=1)


class AddMealSelectionCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    day_id: str = Field(min_length=1, max_length=160)
    meal_period: Literal["lunch", "dinner"]
    selection_id: str = Field(min_length=1, max_length=200)
    merchant: MerchantSnapshotV2


class SelectLodgingCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    lodging: LodgingArrangementV2


class SelectIntercityTrainCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    train: IntercityTrainArrangementV2


class AddActivityEdit(StrictModel):
    op: Literal["add_activity"]
    day_id: str = Field(min_length=1, max_length=160)
    activity_type: Literal["meal", "rest", "shopping", "free_time", "custom"]
    title: str = Field(min_length=1, max_length=200)
    start_time: str | None = None
    duration_min: int = Field(default=60, ge=0, le=24 * 60)
    note: str = Field(default="", max_length=2000)
    position: int | None = Field(default=None, ge=0, le=100)


class AddPlaceEdit(StrictModel):
    op: Literal["add_place"]
    day_id: str = Field(min_length=1, max_length=160)
    selection_id: str = Field(min_length=1, max_length=200)
    place: PlaceSnapshotV2
    start_time: str | None = None
    duration_min: int = Field(default=90, ge=0, le=24 * 60)
    position: int | None = Field(default=None, ge=0, le=100)


class ReplacePlaceEdit(StrictModel):
    op: Literal["replace_place"]
    item_id: str = Field(min_length=1, max_length=160)
    selection_id: str = Field(min_length=1, max_length=200)
    place: PlaceSnapshotV2


class UpdateTripItemEdit(StrictModel):
    op: Literal["update_item"]
    item_id: str = Field(min_length=1, max_length=160)
    start_time: str | None = None
    duration_min: int | None = Field(default=None, ge=0, le=24 * 60)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def requires_a_change(self) -> "UpdateTripItemEdit":
        if not self.model_fields_set.intersection({"start_time", "duration_min", "note"}):
            raise ValueError("至少需要修改时间、停留时长或说明")
        return self


class RemoveTripItemEdit(StrictModel):
    op: Literal["remove_item"]
    item_id: str = Field(min_length=1, max_length=160)


class MoveTripItemEdit(StrictModel):
    op: Literal["move_item"]
    item_id: str = Field(min_length=1, max_length=160)
    target_day_id: str = Field(min_length=1, max_length=160)
    position: int | None = Field(default=None, ge=0, le=100)


class ReorderTripDayEdit(StrictModel):
    op: Literal["reorder_day"]
    day_id: str = Field(min_length=1, max_length=160)
    item_ids: list[str] = Field(max_length=100)


class AddTripDayEdit(StrictModel):
    op: Literal["add_day"]
    title: str | None = Field(default=None, min_length=1, max_length=200)


class RemoveTripDayEdit(StrictModel):
    op: Literal["remove_day"]
    day_id: str = Field(min_length=1, max_length=160)


class SetTripLegModeEdit(StrictModel):
    op: Literal["set_leg_mode"]
    from_item_id: str = Field(min_length=1, max_length=160)
    to_item_id: str = Field(min_length=1, max_length=160)
    selected_mode: Literal["walk", "drive", "transit", "taxi"]


TripEditOperation = Annotated[
    Union[
        AddActivityEdit,
        AddPlaceEdit,
        ReplacePlaceEdit,
        UpdateTripItemEdit,
        RemoveTripItemEdit,
        MoveTripItemEdit,
        ReorderTripDayEdit,
        AddTripDayEdit,
        RemoveTripDayEdit,
        SetTripLegModeEdit,
    ],
    Field(discriminator="op"),
]


class StructuredTripEditCommand(StrictModel):
    expected_revision: int = Field(ge=1)
    change_summary: str = Field(min_length=1, max_length=4000)
    operation: TripEditOperation
