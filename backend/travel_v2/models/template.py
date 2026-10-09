from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from backend.travel_v2.models.trip import StrictModel, TripDocumentV2


class TemplateCreateRequest(StrictModel):
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=4000)
    cover_image_url: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    sort_weight: int = 0
    content: TripDocumentV2


class TemplateUpdateRequest(StrictModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=4000)
    cover_image_url: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    sort_weight: int = 0
    content: TripDocumentV2


class TemplateRecord(StrictModel):
    template_id: UUID
    slug: str
    title: str
    destination: str
    summary: str
    cover_image_url: str | None
    tags: list[str]
    version: int
    status: Literal["draft", "published", "archived"]
    sort_weight: int
    content: TripDocumentV2
    created_by: str
    published_at: str | None
    created_at: str
    updated_at: str
