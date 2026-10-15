from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_travel_v2_create_and_retirement_migrations_run_in_safe_order() -> None:
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    assert MIGRATION_TARGETS["084_travel_v2_schema.sql"] == "memory"
    assert MIGRATION_TARGETS["085_drop_legacy_travel_planning.sql"] == "memory"
    assert MIGRATION_TARGETS["086_travel_v2_arrangements.sql"] == "memory"
    assert MIGRATION_TARGETS["094_travel_v2_ai_edit_requests.sql"] == "memory"
    assert MIGRATION_TARGETS["097_llm_model_health_probe_source.sql"] == "memory"
    assert names.index("084_travel_v2_schema.sql") < names.index(
        "085_drop_legacy_travel_planning.sql"
    )
    assert names.index("085_drop_legacy_travel_planning.sql") < names.index(
        "086_travel_v2_arrangements.sql"
    )
    assert names.index("094_travel_v2_ai_edit_requests.sql") < names.index(
        "097_llm_model_health_probe_source.sql"
    )
    assert names[-1] == "097_llm_model_health_probe_source.sql"
    # 预留的 BE-4C/BE-4D 迁移一旦进入 main，来源标记迁移仍按序执行其后。
    for predecessor in (
        "095_travel_v2_ai_edit_pending_resolution.sql",
        "096_travel_v2_revision_shares.sql",
    ):
        if predecessor in names:
            assert names.index(predecessor) < names.index(
                "097_llm_model_health_probe_source.sql"
            )
