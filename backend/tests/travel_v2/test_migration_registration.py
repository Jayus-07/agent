from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_travel_v2_create_and_retirement_migrations_run_in_safe_order() -> None:
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    assert MIGRATION_TARGETS["084_travel_v2_schema.sql"] == "memory"
    assert MIGRATION_TARGETS["085_drop_legacy_travel_planning.sql"] == "memory"
    assert MIGRATION_TARGETS["086_travel_v2_arrangements.sql"] == "memory"
    assert MIGRATION_TARGETS["094_travel_v2_ai_edit_requests.sql"] == "memory"
    assert MIGRATION_TARGETS["096_travel_v2_revision_shares.sql"] == "memory"
    assert names.index("084_travel_v2_schema.sql") < names.index(
        "085_drop_legacy_travel_planning.sql"
    )
    assert names.index("085_drop_legacy_travel_planning.sql") < names.index(
        "086_travel_v2_arrangements.sql"
    )
    assert names.index("094_travel_v2_ai_edit_requests.sql") < names.index(
        "096_travel_v2_revision_shares.sql"
    )
    assert names[-1] == "096_travel_v2_revision_shares.sql"
    pending_resolution = "095_travel_v2_ai_edit_pending_resolution.sql"
    if pending_resolution in names:
        assert names.index("094_travel_v2_ai_edit_requests.sql") < names.index(
            pending_resolution
        ) < names.index("096_travel_v2_revision_shares.sql")
