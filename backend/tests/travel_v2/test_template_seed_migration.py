# -*- coding: utf-8 -*-
"""129 号种子迁移的契约守护。

无 DB 依赖部分（始终执行）：
- 迁移内的 content JSON 必须通过 TripDocumentV2 严格校验（extra=forbid），
  防止种子漂移出前端/服务端都无法消费的文档结构；
- slug 集合与前端 demoOnly 演示模板保持一致（hangzhou-slow/suzhou-garden/xiamen-sea）；
- template_id 必须是按 slug 确定性生成的 uuid5，保证幂等重放与跨环境可引用。

门控部分（TRAVEL_V2_TEST_POSTGRES=1 的隔离实例执行）：
- 在临时库上重放迁移两次，验证幂等（行数不翻倍、内容不被覆盖）。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest

from backend.travel_v2.models.trip import TripDocumentV2

MIGRATION = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "129_travel_v2_template_seed.sql"

EXPECTED_SLUGS = ["hangzhou-slow", "suzhou-garden", "xiamen-sea"]

# slug 注释行 + 后续第一条 $json$ 块
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


def test_seed_migration_contains_three_valid_documents() -> None:
    rows = _rows()
    assert [row["slug"] for row in rows] == EXPECTED_SLUGS
    for row in rows:
        content = json.loads(row["content"])
        document = TripDocumentV2.model_validate(content)
        assert document.brief.day_count == len(document.days)
        assert document.health.status == "ok"


def test_seed_template_ids_are_deterministic_uuid5() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for row in _rows():
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, "https://agent.local/travel-v2/templates/" + row["slug"]))
        assert expected in sql, f"missing deterministic uuid for {row['slug']}"
    assert len(_UUID_RE.findall(sql)) == len(EXPECTED_SLUGS)


@pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires an explicitly selected isolated travel V2 PostgreSQL",
)
def test_seed_migration_is_idempotent_on_isolated_postgres() -> None:
    from backend.infra.db import get_memory_engine

    sql = MIGRATION.read_text(encoding="utf-8")
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute(sql)
            cursor.execute(
                "SELECT slug, status, published_at FROM travel_v2.templates"
                " WHERE slug = ANY(%s::text[])"
                " ORDER BY sort_weight DESC",
                (EXPECTED_SLUGS,),
            )
            rows = cursor.fetchall()
            assert [row[0] for row in rows] == EXPECTED_SLUGS
            assert all(row[1] == "published" and row[2] is not None for row in rows)
            cursor.execute(
                "SELECT count(*) FROM travel_v2.templates WHERE slug = ANY(%s::text[])",
                (EXPECTED_SLUGS,),
            )
            assert cursor.fetchone()[0] == len(EXPECTED_SLUGS)
        connection.commit()
    finally:
        connection.close()
