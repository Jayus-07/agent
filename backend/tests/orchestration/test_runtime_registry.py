"""STOP C：Runtime Registry 的契约与注册治理。"""

from __future__ import annotations

import pytest

import backend.domains  # noqa: F401  # 触发真实域图注册
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import (
    DomainGraphRegistry,
    domain_graph_registry,
)
from backend.orchestration.runtime_types import RuntimeType


def _noop_adapter(state: dict) -> dict:
    return state


def test_real_domains_register_runtime_descriptors():
    cs = domain_graph_registry.get("customer_service")
    travel = domain_graph_registry.get("travel")
    selection = domain_graph_registry.get("selection_funnel")

    assert cs is not None and cs.runtime_type is RuntimeType.AGENT
    assert travel is not None and travel.runtime_type is RuntimeType.WORKFLOW
    assert selection is not None and selection.runtime_type is RuntimeType.WORKFLOW

    assert cs.runtime_id == "customer_service"
    assert travel.runtime_id == "travel"
    assert selection.runtime_id == "selection_funnel"
    assert cs.supports_checkpoint is True
    assert cs.supports_interrupt is False
    assert travel.supports_checkpoint is True
    assert travel.supports_interrupt is True
    assert selection.supports_checkpoint is False
    assert selection.supports_interrupt is False


def test_subflow_runtime_descriptors_are_registered():
    commerce = domain_graph_registry.get("travel_commerce")
    booking = domain_graph_registry.get("travel_booking")

    assert commerce is not None
    assert booking is not None
    assert commerce.domain == booking.domain == "travel"
    assert commerce.subflow == "commerce"
    assert booking.subflow == "booking"
    assert commerce.runtime_type is RuntimeType.WORKFLOW
    assert booking.runtime_type is RuntimeType.WORKFLOW


def test_registry_derives_runtime_views_for_real_and_new_domains():
    target_view = domain_graph_registry.route_mode_to_runtime_target()
    family_view = domain_graph_registry.route_mode_to_family()
    entry_view = domain_graph_registry.route_mode_to_entry_mode()

    assert target_view["customer_service"].type is RuntimeType.AGENT
    assert target_view["travel"].id == "travel"
    assert target_view["travel"].subflow == "planning"
    assert target_view["travel_booking"].subflow == "booking"
    assert family_view["selection_funnel"] is RuntimeType.WORKFLOW
    assert entry_view["travel"] == ("execute", "guide")

    registry = DomainGraphRegistry()
    registry.register(DomainGraph(
        name="fake_top",
        node_name="fake_top_node",
        label="Fake Top",
        adapter=_noop_adapter,
        runtime_id="fake-runtime",
        runtime_type=RuntimeType.AGENT,
        aliases=("fake",),
        entry_modes=("execute", "guide"),
    ))
    registry.register(DomainGraph(
        name="fake_sub",
        node_name="fake_sub_node",
        label="Fake Sub",
        adapter=_noop_adapter,
        domain="fake_top",
        subflow="subflow",
        runtime_id="fake-sub-runtime",
    ))

    assert registry.resolve_alias("fake") == "fake_top"
    assert registry.resolve_alias("fake_top") == "fake_top"
    assert registry.resolve_alias("missing") is None
    assert registry.route_mode_to_runtime_target()["fake"].id == "fake-runtime"
    assert registry.route_mode_to_runtime_target()["fake_sub"].subflow == "subflow"
    assert registry.route_mode_to_family()["fake"] is RuntimeType.AGENT
    assert registry.route_mode_to_entry_mode()["fake"] == ("execute", "guide")


def test_registry_rejects_runtime_id_and_alias_collisions():
    registry = DomainGraphRegistry()
    registry.register(DomainGraph(
        name="first",
        node_name="first_node",
        label="First",
        adapter=_noop_adapter,
        runtime_id="shared-runtime",
        aliases=("first-alias",),
    ))

    with pytest.raises(ValueError, match="runtime_id"):
        registry.register(DomainGraph(
            name="second",
            node_name="second_node",
            label="Second",
            adapter=_noop_adapter,
            runtime_id="shared-runtime",
        ))

    with pytest.raises(ValueError, match="alias"):
        registry.register(DomainGraph(
            name="second",
            node_name="second_node",
            label="Second",
            adapter=_noop_adapter,
            aliases=("first-alias",),
        ))

    with pytest.raises(ValueError, match="alias"):
        registry.register(DomainGraph(
            name="second",
            node_name="second_node",
            label="Second",
            adapter=_noop_adapter,
            aliases=("first",),
        ))


def test_registry_rejects_invalid_subflow_ownership():
    registry = DomainGraphRegistry()

    with pytest.raises(ValueError, match="父域"):
        registry.register(DomainGraph(
            name="orphan",
            node_name="orphan_node",
            label="Orphan",
            adapter=_noop_adapter,
            domain="missing_parent",
            subflow="orphan",
        ))

    registry.register(DomainGraph(
        name="parent",
        node_name="parent_node",
        label="Parent",
        adapter=_noop_adapter,
    ))
    with pytest.raises(ValueError, match="自指"):
        registry.register(DomainGraph(
            name="self_ref",
            node_name="self_ref_node",
            label="Self",
            adapter=_noop_adapter,
            domain="self_ref",
            subflow="self",
        ))
