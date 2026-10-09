"""旅游行程账本迁移必须登记到元数据库。"""
from pathlib import Path

from scripts.init_db import MIGRATION_TARGETS


def test_travel_plan_tenant_scope_migration_is_registered_for_memory_database():
    migration = "082_travel_plan_tenant_scope.sql"
    migration_path = Path(__file__).resolve().parents[1] / "sql" / "migrations" / migration

    assert migration_path.is_file()
    assert MIGRATION_TARGETS.get(migration) == "memory"
