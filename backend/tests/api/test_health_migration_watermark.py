from pathlib import Path

from backend.app.api.routes.health import _packaged_migration_max


def test_packaged_migration_max_only_counts_the_requested_database(tmp_path: Path) -> None:
    (tmp_path / "105_rag_tenant_isolation.sql").write_text("-- memory")
    (tmp_path / "106_approval_tenant_isolation.sql").write_text("-- business")

    maximum = _packaged_migration_max(
        tmp_path,
        {
            "105_rag_tenant_isolation.sql": "memory",
            "106_approval_tenant_isolation.sql": "business",
        },
        target="memory",
    )

    assert maximum == "105_rag_tenant_isolation.sql"


def test_packaged_migration_max_is_empty_without_target_migrations(tmp_path: Path) -> None:
    (tmp_path / "106_approval_tenant_isolation.sql").write_text("-- business")

    maximum = _packaged_migration_max(
        tmp_path,
        {"106_approval_tenant_isolation.sql": "business"},
        target="memory",
    )

    assert maximum == ""
