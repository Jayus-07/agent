"""企业扫码登录迁移登记契约。"""

from pathlib import Path

from scripts.ensure_dbs import MEMORY_MIGRATIONS
from scripts.init_db import MIGRATION_TARGETS, discover_migrations


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "migrations" / "151_auth_sso.sql"


def test_sso_migration_is_registered_for_memory_database():
    """两种初始化入口都必须应用 SSO 表迁移。"""

    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert MIGRATION.exists()
    assert MIGRATION_TARGETS[MIGRATION.name] == "memory"
    assert MIGRATION.name in names
    assert unregistered == []
    assert MIGRATION.name in MEMORY_MIGRATIONS


def test_sso_identity_mapping_is_tenant_scoped_and_has_no_phone_link():
    """外部身份必须在同租户内显式绑定到本地账号。"""

    sql = MIGRATION.read_text(encoding="utf-8")

    assert "FOREIGN KEY (local_user_id, tenant_id)" in sql
    assert "UNIQUE (tenant_id, provider, external_org_id, external_subject_id)" in sql
    assert "UNIQUE (tenant_id, provider, local_user_id)" in sql
    assert "phone" not in sql.lower()
