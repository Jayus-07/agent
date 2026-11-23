# -*- coding: utf-8 -*-
"""130 号扩展特色模板种子迁移的契约守护。"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from backend.travel_v2.models.trip import TripDocumentV2

MIGRATION = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "130_travel_v2_featured_templates.sql"

EXPECTED_SLUGS = ["beijing-heritage", "chengdu-chill"]

_ROW_RE = re.compile(
    r"-- ═══ (?P<title>[^（]+)（(?P<slug>[^）]+)） ═══.*?\$json\$(?P<content>.*?)\$json\$",
    re.S,
)
_UUID_RE = re.compile(r"'([0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})'")


def _rows() -> list[dict]:
    sql = MIGRATION.read_text(encoding="utf-8")
    rows = []
    for match in _ROW_RE.finditer(sql):
        rows.append(match.groupdict())
    return rows


def test_featured_seed_migration_contains_valid_documents() -> None:
    rows = _rows()
    assert [row["slug"] for row in rows] == EXPECTED_SLUGS
    for row in rows:
        content = json.loads(row["content"])
        document = TripDocumentV2.model_validate(content)
        assert document.brief.day_count == len(document.days)
        assert document.health.status == "ok"


def test_featured_seed_template_ids_are_deterministic_uuid5() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for row in _rows():
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://agent.local/travel-v2/templates/" + row["slug"]))
        assert expected in sql, f"missing deterministic uuid for {row['slug']}"
    assert len(_UUID_RE.findall(sql)) == len(EXPECTED_SLUGS)
