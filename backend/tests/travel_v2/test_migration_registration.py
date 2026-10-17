from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_travel_v2_and_model_health_migrations_run_in_safe_order() -> None:
    ordered, unregistered = discover_migrations()
    names = [name for name, _target in ordered]

    assert unregistered == []
    sequence = [
        "084_travel_v2_schema.sql",
        "085_drop_legacy_travel_planning.sql",
        "086_travel_v2_arrangements.sql",
        "094_travel_v2_ai_edit_requests.sql",
        "095_travel_v2_ai_edit_pending_resolution.sql",
        "096_travel_v2_revision_shares.sql",
        "097_llm_model_health_probe_source.sql",
        "101_travel_v2_trip_conversations.sql",
    ]
    for migration in sequence:
        assert MIGRATION_TARGETS[migration] == "memory"
    positions = [names.index(migration) for migration in sequence]
    assert positions == sorted(positions)
