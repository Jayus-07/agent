"""客服确认版本迁移必须走 PostgreSQL memory 唯一迁移登记表。"""
from pathlib import Path

from scripts.init_db import MIGRATION_TARGETS


def test_versioned_confirmation_migration_is_registered_for_memory_database():
    migration = "082_cs_confirmation_versioned_claim.sql"
    migration_path = Path(__file__).resolve().parents[1] / "sql" / "migrations" / migration

    assert migration_path.is_file()
    assert MIGRATION_TARGETS.get(migration) == "memory"
