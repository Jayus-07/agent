"""super_admin 数据库迁移契约测试。"""

from __future__ import annotations

from pathlib import Path

from scripts.init_db import MIGRATION_TARGETS


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "migrations" / "054_auth_super_admin.sql"
PHONE_MIGRATION = ROOT / "sql" / "migrations" / "081_auth_user_phone.sql"


def test_super_admin_migration_is_registered_for_memory_database():
    """漏登记会使 init_db 的 fail-fast 阻断发布或漏执行迁移。"""

    assert MIGRATION.exists()
    assert MIGRATION_TARGETS[MIGRATION.name] == "memory"


def test_user_phone_migration_is_registered_for_memory_database():
    """新增认证字段必须登记，否则 init_db 会在发布时 fail-fast。"""

    assert PHONE_MIGRATION.exists()
    assert MIGRATION_TARGETS[PHONE_MIGRATION.name] == "memory"


def test_super_admin_migration_widens_role_and_preserves_role_constraint():
    """若未扩至 11 字符或未替换 CHECK，bootstrap 将无法写入角色。"""

    sql = MIGRATION.read_text(encoding="utf-8")

    assert "VARCHAR(11)" in sql
    assert "DROP CONSTRAINT IF EXISTS ck_users_role" in sql
    assert "'super_admin'" in sql
