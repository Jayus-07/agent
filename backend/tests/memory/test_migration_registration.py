from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_memory_profile_and_extraction_migrations_are_registered_in_order():
    ordered, unregistered = discover_migrations()
    assert unregistered == []

    names = [name for name, _target in ordered]
    profile = "088_memory_profile_scope_verification.sql"
    outbox = "089_memory_extraction_outbox.sql"

    assert MIGRATION_TARGETS[profile] == "memory"
    assert MIGRATION_TARGETS[outbox] == "memory"
    assert names.index(profile) < names.index(outbox)