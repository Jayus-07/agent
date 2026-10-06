"""STOP F：新增域的注册面与 Router 派生视图守护。"""

from __future__ import annotations

from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import DomainGraphRegistry
from backend.orchestration.runtime_types import RuntimeType


def _noop_adapter(state: dict) -> dict:
    return state


def test_fake_domain_only_needs_registry_descriptor_for_metadata_views():
    registry = DomainGraphRegistry()
    registry.register(DomainGraph(
        name="fake_domain",
        node_name="fake_domain_node",
        label="Fake Domain",
        adapter=_noop_adapter,
        runtime_id="fake-runtime",
        runtime_type=RuntimeType.WORKFLOW,
        entry_modes=("execute", "guide"),
        entry_mode_key="FAKE_GLOBAL_ENTRY_MODE",
    ))

    assert registry.route_mode_to_domain_family()["fake_domain"] == "fake_domain"
    assert registry.route_mode_to_runtime_target()["fake_domain"].id == "fake-runtime"
    assert registry.route_mode_to_entry_mode()["fake_domain"] == ("execute", "guide")
    assert registry.route_mode_to_entry_mode_key()["fake_domain"] == (
        "FAKE_GLOBAL_ENTRY_MODE"
    )
