"""企业扫码登录迁移登记契约。"""

import re
from pathlib import Path

from scripts.ensure_dbs import MEMORY_MIGRATIONS
from scripts.init_db import MIGRATION_TARGETS, discover_migrations


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "sql" / "migrations" / "151_auth_sso.sql"

# 151 中出现的 auth.* 表（引用）与它自己 CREATE TABLE 的表（自建）
_AUTH_TABLE_RE = re.compile(r"\bauth\.([a-z_]+)")
_AUTH_CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?auth\.([a-z_]+)")


def test_sso_migration_is_registered_for_memory_database():
    """两种初始化入口都必须应用 SSO 表迁移。"""

    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert MIGRATION.exists()
    assert MIGRATION_TARGETS[MIGRATION.name] == "memory"
    assert MIGRATION.name in names
    assert unregistered == []
    assert MIGRATION.name in MEMORY_MIGRATIONS


def test_sso_migration_has_prerequisite_ordered_in_memory_repair_list():
    """存量库补齐清单必须先建 auth.users（008）再跑引用它的 151。

    回归背景：ensure_dbs.py 的 MEMORY_MIGRATIONS 曾漏登记 008，
    旧库副本上 151 的 `CREATE INDEX ... ON auth.users` 因表不存在而失败，
    且该错误不在幂等跳过清单里，补齐流程持续 rc=1。
    """

    assert "008_local_auth.sql" in MEMORY_MIGRATIONS
    assert MEMORY_MIGRATIONS.index("008_local_auth.sql") < MEMORY_MIGRATIONS.index(
        MIGRATION.name
    )


def test_sso_migration_references_only_prerequisite_tables_known_to_repair_list():
    """151 引用但未自建的 auth.* 表必须由清单内更早的迁移创建。

    目前 151 引用 auth.users（008 创建）；auth.external_identities 与
    auth.sso_provider_configs 由 151 自建，不在此列。若未来新增对其他
    auth.* 表的引用而清单没有对应前置，此测试失败并提示补登记。
    """

    sql = MIGRATION.read_text(encoding="utf-8")
    referenced = set(_AUTH_TABLE_RE.findall(sql))
    created_here = set(_AUTH_CREATE_RE.findall(sql))
    creators = {"users": "008_local_auth.sql"}
    for table in sorted(referenced - created_here):
        creator = creators.get(table)
        assert creator, f"auth.{table} 的前置迁移未登记到测试 creators 映射"
        assert creator in MEMORY_MIGRATIONS
        assert MEMORY_MIGRATIONS.index(creator) < MEMORY_MIGRATIONS.index(
            MIGRATION.name
        )


def test_sso_identity_mapping_is_tenant_scoped_and_has_no_phone_link():
    """外部身份必须在同租户内显式绑定到本地账号。"""

    sql = MIGRATION.read_text(encoding="utf-8")

    assert "FOREIGN KEY (local_user_id, tenant_id)" in sql
    assert "UNIQUE (tenant_id, provider, external_org_id, external_subject_id)" in sql
    assert "UNIQUE (tenant_id, provider, local_user_id)" in sql
    assert "phone" not in sql.lower()
