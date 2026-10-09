from __future__ import annotations

import os

import pytest

from backend.config.database import MEMORY_DB_CONFIG
from backend.infra.db import get_memory_engine


pytestmark = pytest.mark.skipif(
    os.getenv("TRAVEL_V2_TEST_POSTGRES") != "1",
    reason="requires the explicitly selected local agent_memory PostgreSQL",
)


def test_live_schema_contains_only_the_four_v2_core_tables_and_required_relations() -> None:
    assert MEMORY_DB_CONFIG["dbname"] == "agent_memory"
    assert MEMORY_DB_CONFIG["port"] == 5433
    connection = get_memory_engine().raw_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'travel_v2' AND table_type = 'BASE TABLE'
                    ORDER BY table_name"""
            )
            tables = {row[0] for row in cursor.fetchall()}
            assert tables == {"edit_operations", "templates", "trip_revisions", "trips"}

            cursor.execute(
                """SELECT conname, contype FROM pg_constraint
                    WHERE connamespace = 'travel_v2'::regnamespace"""
            )
            constraints = {name: kind for name, kind in cursor.fetchall()}
            assert constraints["fk_travel_v2_revision_owner"] == "f"
            assert constraints["fk_travel_v2_revision_parent"] == "f"
            assert constraints["fk_travel_v2_edit_owner"] == "f"
            assert constraints["fk_travel_v2_edit_revision"] == "f"
            assert constraints["uq_travel_v2_edit_idempotency"] == "u"
            assert constraints["chk_travel_v2_edit_revision"] == "c"

            cursor.execute(
                """SELECT indexname FROM pg_indexes
                    WHERE schemaname = 'travel_v2' ORDER BY indexname"""
            )
            indexes = {row[0] for row in cursor.fetchall()}
            assert {
                "idx_travel_v2_templates_published",
                "idx_travel_v2_trips_owner_recent",
                "idx_travel_v2_trips_destination",
                "idx_travel_v2_revisions_owner_recent",
                "idx_travel_v2_edit_operations_recent",
            } <= indexes
    finally:
        connection.close()
