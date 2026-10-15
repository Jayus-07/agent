from __future__ import annotations

import pytest

from backend.tests.travel_v2.test_trip_document import valid_document
from backend.travel_v2.adapters.ics_export import trip_document_to_ics
from backend.travel_v2.services.share_service import project_public_share


def test_public_share_projection_keeps_only_explicit_public_fields() -> None:
    document = valid_document()
    document["days"][0]["items"][0]["note"] = "家庭住址和私人备注"
    document["days"][0]["items"][0]["place"]["facts"]["source"] = "provider-secret"
    share = {
        "share_id": "2b0a7730-b891-4e9e-8a3a-3c12cedd91b9",
        "revision": 1,
        "expires_at": "2026-11-19T00:00:00+00:00",
        "snapshot": document,
    }

    public = project_public_share(share)
    serialized = str(public)

    assert set(public) == {
        "share_id",
        "revision",
        "expires_at",
        "title",
        "brief",
        "days",
        "arrangements",
    }
    assert public["title"] == "杭州"
    assert public["brief"] == {
        "destination": "杭州",
        "timezone": "Asia/Shanghai",
        "start_date": "2026-10-20",
        "day_count": 1,
    }
    assert "家庭住址" not in serialized
    assert "provider-secret" not in serialized
    assert "origin" not in serialized
    assert "travelers" not in serialized
    assert "budget" not in serialized
    assert "tenant_id" not in serialized
    assert "owner_id" not in serialized
    assert "trace" not in serialized.lower()


def test_v2_ics_uses_revision_timezone_and_cross_midnight_duration() -> None:
    document = valid_document()
    document["brief"]["timezone"] = "Europe/Berlin"
    document["days"][0]["items"][0].update(
        {
            "start_time": "23:30",
            "duration_min": 90,
            "note": "不导出的私人备注",
        }
    )

    ics = trip_document_to_ics(document)

    assert "TZID:Europe/Berlin" in ics
    assert "DTSTART;TZID=Europe/Berlin:20261020T233000" in ics
    assert "DTEND;TZID=Europe/Berlin:20261021T010000" in ics
    assert "SUMMARY:西湖" in ics
    assert "不导出的私人备注" not in ics
    assert "DTSTAMP:" in ics and "Z" in ics
    assert ics.index("END:VTIMEZONE") < ics.index("BEGIN:VEVENT")


def test_v2_ics_exports_unscheduled_items_as_local_all_day_events() -> None:
    document = valid_document()
    document["days"][0]["items"][0]["start_time"] = None

    ics = trip_document_to_ics(document)

    assert "DTSTART;VALUE=DATE:20261020" in ics
    assert "DTEND;VALUE=DATE:20261021" in ics
    assert "DTSTART;TZID=" not in ics


def test_v2_ics_rejects_nonexistent_local_time_during_dst_transition() -> None:
    document = valid_document()
    document["brief"].update(
        {
            "timezone": "Europe/Berlin",
            "start_date": "2026-03-29",
        }
    )
    document["days"][0]["date"] = "2026-03-29"
    document["days"][0]["items"][0]["start_time"] = "02:30"

    with pytest.raises(ValueError, match="不存在的本地开始时间"):
        trip_document_to_ics(document)


def test_ics_content_disposition_keeps_chinese_filename_header_safe() -> None:
    from backend.app.api.routes.travel import ics_content_disposition

    header = ics_content_disposition("杭州")

    header.encode("latin-1")
    assert "filename*=UTF-8''%E6%9D%AD%E5%B7%9E.ics" in header
