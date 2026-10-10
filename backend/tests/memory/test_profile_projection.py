from types import SimpleNamespace

from backend.memory.keying import memory_scope_domain, normalize_memory_domain
from backend.memory.profile import build_profile_projection


def test_projection_groups_structured_values_by_key_without_summary_text():
    records = [
        SimpleNamespace(memory_key="travel.pace", structured_value="moderately_packed"),
        SimpleNamespace(memory_key="hotel.quiet", structured_value="true"),
        SimpleNamespace(memory_key="hotel.location", structured_value="near_metro"),
        SimpleNamespace(memory_key=None, structured_value=None),
        SimpleNamespace(memory_key="invalid", structured_value="ignored"),
    ]

    assert build_profile_projection(records) == {
        "travel": {"pace": "moderately_packed"},
        "hotel": {"quiet": True, "location": "near_metro"},
    }


def test_projection_does_not_overwrite_a_parent_value_with_a_nested_key():
    records = [
        SimpleNamespace(memory_key="user.travel", structured_value="legacy"),
        SimpleNamespace(memory_key="user.travel.pace", structured_value="relaxed"),
    ]

    assert build_profile_projection(records) == {"user": {"travel": "legacy"}}


def test_scope_and_domain_are_derived_from_code_allowlist():
    assert memory_scope_domain("response.language") == ("user_global", "general")
    assert memory_scope_domain("hotel.quiet") == ("user_domain", "travel")
    assert memory_scope_domain("customer_service.tone") == (
        "user_domain", "customer_service",
    )
    assert normalize_memory_domain("cs") == "customer_service"
    assert normalize_memory_domain("client_claimed_domain") is None
