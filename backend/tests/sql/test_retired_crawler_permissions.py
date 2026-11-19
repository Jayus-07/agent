"""已退役 crawler 域的数据库只读权限收口契约。"""

from pathlib import Path

from scripts.ensure_dbs import BUSINESS_MIGRATIONS
from scripts.init_db import MIGRATION_TARGETS, discover_migrations


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "migrations" / "152_retire_crawler_read_access.sql"


def test_crawler_read_revocation_is_registered_after_role_and_schema_migrations():
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert MIGRATION.exists()
    assert MIGRATION_TARGETS[MIGRATION.name] == "business"
    assert unregistered == []
    assert names.index("004_readonly_role.sql") < names.index(MIGRATION.name)
    assert names.index("148_readonly_default_privileges_completion.sql") < names.index(
        MIGRATION.name
    )
    assert names.index("150_retail_erp_pilot_schema.sql") < names.index(MIGRATION.name)
    assert MIGRATION.name in BUSINESS_MIGRATIONS
    assert BUSINESS_MIGRATIONS.index("004_readonly_role.sql") < (
        BUSINESS_MIGRATIONS.index("150_retail_erp_pilot_schema.sql")
    )
    assert BUSINESS_MIGRATIONS.index("150_retail_erp_pilot_schema.sql") < (
        BUSINESS_MIGRATIONS.index(MIGRATION.name)
    )


def test_crawler_migration_revokes_access_without_deleting_tables_or_data():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "revoke select on all tables in schema crawler from agent_readonly" in sql
    assert "revoke usage on schema crawler from agent_readonly" in sql
    assert "alter default privileges in schema crawler" in sql
    assert "revoke all on tables from agent_readonly" in sql
    assert "drop table" not in sql
    assert "delete from" not in sql
    assert "truncate" not in sql
