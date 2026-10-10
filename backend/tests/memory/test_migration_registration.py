from scripts.init_db import MIGRATION_TARGETS, discover_migrations


def test_memory_profile_and_extraction_migrations_are_registered_in_order():
    ordered, unregistered = discover_migrations()
    assert unregistered == []

    names = [name for name, _target in ordered]
    profile = "088_memory_profile_scope_verification.sql"
    outbox = "089_memory_extraction_outbox.sql"
    turn = "090_memory_turn_idempotency.sql"
    langchain_messages = "091_chat_message_langchain_metadata.sql"
    session_tenant = "092_chat_session_tenant_scope.sql"

    assert MIGRATION_TARGETS[profile] == "memory"
    assert MIGRATION_TARGETS[outbox] == "memory"
    assert MIGRATION_TARGETS[turn] == "memory"
    assert MIGRATION_TARGETS[langchain_messages] == "memory"
    assert MIGRATION_TARGETS[session_tenant] == "memory"
    assert names.index(profile) < names.index(outbox)
    assert names.index(outbox) < names.index(turn)
    assert names.index(turn) < names.index(langchain_messages)
    assert names.index(langchain_messages) < names.index(session_tenant)
