"""Refund request migration is owned and ordered by the memory database."""
from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_active_refund_order_index_migration_is_registered_after_case_schema():
    migration = "098_cs_refund_order_unique.sql"
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    assert MIGRATION_TARGETS[migration] == "memory"
    assert names.index("058_cs_case.sql") < names.index(migration)
