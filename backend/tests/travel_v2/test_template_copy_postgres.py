from __future__ import annotations

import os
import uuid

import pytest

from backend.infra.db import get_memory_engine
from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.models.template import TemplateCreateRequest, TemplateUpdateRequest
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.services.template_service import TemplateService
from backend.travel_v2.services.trip_edit_service import TripEditService
from backend.travel_v2.services.trip_service import TripService


pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires the explicitly selected local agent_memory PostgreSQL",
)


def test_published_template_copy_is_independent_from_future_trip_edits() -> None:
    suffix = uuid.uuid4().hex[:12]
    tenant_id = f"tenant-{suffix}"
    owner_id = f"owner-{suffix}"
    slug = f"test-template-{suffix}"
    template_service = TemplateService()
    created = template_service.create_template(
        TemplateCreateRequest(
            slug=slug, title="杭州慢游", destination="杭州",
            summary="模板隔离测试", tags=["城市漫游"],
            content=TripDocumentV2.model_validate(valid_document()),
        ),
        actor_id="platform-admin-test",
    )
    template_service.publish(created["template_id"])
    trip_id: str | None = None
    try:
        copied = template_service.copy_to_trip(
            slug, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=owner_id, idempotency_key=f"copy-{suffix}",
        )
        trip_id = copied["trip_id"]
        assert copied["revision"] == 1
        assert copied["source_template_id"] == created["template_id"]
        assert copied["source_template_version"] == 1

        edited_document = valid_document()
        edited_document["days"][0]["title"] = "个人改动"
        TripEditService().apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=owner_id, expected_revision=1,
            idempotency_key=f"edit-{suffix}", command_type="structured_edit",
            change_summary="修改个人副本", document=TripDocumentV2.model_validate(edited_document),
        )

        personal = TripService().get_trip(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
        changed_template = valid_document()
        changed_template["days"][0]["title"] = "平台模板新内容"
        updated_template = template_service.update_template(
            created["template_id"],
            TemplateUpdateRequest(
                expected_version=1, title="杭州慢游新版", destination="杭州",
                summary="模板更新测试", cover_image_url=None,
                tags=["城市漫游", "更新"], sort_weight=0,
                content=TripDocumentV2.model_validate(changed_template),
            ),
        )
        platform_template = template_service.get_published(slug)
        assert personal["document"]["days"][0]["title"] == "个人改动"
        assert updated_template["version"] == 2
        assert platform_template["title"] == "杭州慢游新版"
        assert platform_template["content"]["days"][0]["title"] == "平台模板新内容"
    finally:
        connection = get_memory_engine().raw_connection()
        try:
            if trip_id:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM travel_v2.edit_operations WHERE trip_id=%s::uuid", (trip_id,))
                    cursor.execute("DELETE FROM travel_v2.trip_revisions WHERE trip_id=%s::uuid", (trip_id,))
                    cursor.execute("DELETE FROM travel_v2.trips WHERE trip_id=%s::uuid", (trip_id,))
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM travel_v2.templates WHERE template_id=%s::uuid",
                    (created["template_id"],),
                )
            connection.commit()
        finally:
            connection.close()
