from __future__ import annotations

from uuid import UUID, uuid4

from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.template import TemplateCreateRequest, TemplateUpdateRequest
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.revision_repository import RevisionRepository
from backend.travel_v2.repositories.template_repository import TemplateRepository
from backend.travel_v2.repositories.trip_repository import TripRepository
from backend.travel_v2.services.trip_service import (
    IdempotencyConflict,
    TripNotFound,
    _document_dict,
    _trip_id_for_key,
)


class TemplateNotFound(LookupError):
    """没有公开的模板或模板标识无效。"""


class TemplateConflict(ValueError):
    """模板 slug 已存在或模板无法转换为公开状态。"""


class TemplateVersionConflict(RuntimeError):
    """模板在编辑期间被另一管理员更新。"""


class TemplateService:
    def __init__(
        self, *, connect=memory_connection,
        template_repository: TemplateRepository | None = None,
        trip_repository: TripRepository | None = None,
        revision_repository: RevisionRepository | None = None,
    ):
        self._connect = connect
        self._templates = template_repository or TemplateRepository()
        self._trips = trip_repository or TripRepository()
        self._revisions = revision_repository or RevisionRepository()

    def list_published(self, *, destination: str | None = None,
                       limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                return self._templates.list_published(
                    cursor, destination=destination, limit=limit,
                )

    def get_published(self, key: str) -> dict:
        try:
            UUID(key)
            by_slug = False
        except (ValueError, TypeError):
            by_slug = True
        with self._connect() as connection:
            with connection.cursor() as cursor:
                template = self._templates.get_published(
                    cursor, key, by_slug=by_slug,
                )
        if not template:
            raise TemplateNotFound("模板不存在")
        return template

    def create_template(self, request: TemplateCreateRequest | dict,
                        *, actor_id: str) -> dict:
        payload = (
            request if isinstance(request, TemplateCreateRequest)
            else TemplateCreateRequest.model_validate(request)
        )
        if not actor_id or len(actor_id) > 128:
            raise ValueError("actor_id is required")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                created = self._templates.insert(
                    cursor, template_id=uuid4(), slug=payload.slug,
                    title=payload.title, destination=payload.destination,
                    summary=payload.summary,
                    cover_image_url=payload.cover_image_url,
                    tags=payload.tags, sort_weight=payload.sort_weight,
                    content=payload.content.model_dump(mode="json"),
                    created_by=actor_id,
                )
                if not created:
                    raise TemplateConflict("template slug already exists")
        return created

    def publish(self, template_id: str | UUID) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                template = self._templates.publish(cursor, template_id)
                if not template:
                    raise TemplateNotFound("模板不存在或已归档")
        return template

    def update_template(
        self, template_id: str | UUID,
        request: TemplateUpdateRequest | dict,
    ) -> dict:
        payload = (
            request if isinstance(request, TemplateUpdateRequest)
            else TemplateUpdateRequest.model_validate(request)
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                current = self._templates.get_any(cursor, template_id)
                if not current or current["status"] == "archived":
                    raise TemplateNotFound("模板不存在")
                if current["version"] != payload.expected_version:
                    raise TemplateVersionConflict("模板已更新，请刷新后重试")
                updated = self._templates.update(
                    cursor, template_id, expected_version=payload.expected_version,
                    title=payload.title, destination=payload.destination,
                    summary=payload.summary,
                    cover_image_url=payload.cover_image_url,
                    tags=payload.tags, sort_weight=payload.sort_weight,
                    content=payload.content.model_dump(mode="json"),
                )
                if not updated:
                    raise TemplateVersionConflict("模板已更新，请刷新后重试")
        return updated

    def copy_to_trip(
        self, key: str, *, tenant_id: str, owner_id: str, actor_id: str,
        idempotency_key: str,
    ) -> dict:
        if not tenant_id or not owner_id or not actor_id:
            raise ValueError("tenant_id, owner_id and actor_id are required")
        idem = str(idempotency_key or "").strip()
        if not idem or len(idem) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        try:
            UUID(key)
            by_slug = False
        except (ValueError, TypeError):
            by_slug = True

        with self._connect() as connection:
            with connection.cursor() as cursor:
                template = self._templates.get_published(cursor, key, by_slug=by_slug)
                if not template:
                    raise TemplateNotFound("模板不存在")
                trip_id = _trip_id_for_key(tenant_id, owner_id, idem)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (str(trip_id),),
                )
                existing = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )
                snapshot = _document_dict(
                    TripDocumentV2.model_validate(template["content"])
                )
                if existing:
                    same_request = (
                        existing["source_template_id"] == template["template_id"]
                        and existing["source_template_version"] == template["version"]
                        and existing["document"] == snapshot
                    )
                    if not same_request:
                        raise IdempotencyConflict(
                            "Idempotency-Key 已用于不同的模板副本"
                        )
                    return {**existing, "replayed": True}

                trip = self._trips.insert(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id,
                    title=template["title"], document=snapshot,
                    source_template_id=template["template_id"],
                    source_template_version=template["version"],
                )
                self._revisions.insert(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, revision=1, parent_revision=None,
                    snapshot=snapshot, change_type="template_copy",
                    change_summary=f"复制平台模板：{template['title']}",
                    actor_id=actor_id,
                )
        return {**trip, "replayed": False}
