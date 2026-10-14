from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from backend.travel_v2.models.commands import TripEditOperation
from backend.travel_v2.models.trip import StrictModel


class AITripEditRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    user_message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)


class AITripEditCandidate(StrictModel):
    outcome: Literal["candidate", "clarify", "unsupported"]
    operation: TripEditOperation | None = None
    change_summary: str = Field(default="", max_length=4000)
    clarification: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def outcome_fields_are_consistent(self) -> "AITripEditCandidate":
        if self.outcome == "candidate":
            if self.operation is None or not self.change_summary.strip():
                raise ValueError("candidate 需要一个操作和变更摘要")
            if self.clarification:
                raise ValueError("candidate 不能包含澄清问题")
        elif self.operation is not None or self.change_summary:
            raise ValueError("非 candidate 结果不能包含修改操作")
        if self.outcome == "clarify" and not self.clarification.strip():
            raise ValueError("clarify 结果必须包含澄清问题")
        return self
