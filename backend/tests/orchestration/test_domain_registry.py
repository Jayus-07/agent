"""tests/orchestration/test_domain_registry.py — 域图注册表测试"""
from __future__ import annotations

from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import DomainGraphRegistry


def _dummy_adapter(state: dict) -> dict:
    return {"final_answer": "dummy"}


class TestDomainGraphRegistry:
    def test_register_and_get(self):
        registry = DomainGraphRegistry()
        domain = DomainGraph(
            name="test_domain",
            node_name="test_node",
            label="测试域",
            adapter=_dummy_adapter,
        )
        registry.register(domain)

        assert registry.get("test_domain") is domain
        assert registry.get("nonexistent") is None

    def test_get_all(self):
        registry = DomainGraphRegistry()
        d1 = DomainGraph(name="a", node_name="a_node", label="A", adapter=_dummy_adapter)
        d2 = DomainGraph(name="b", node_name="b_node", label="B", adapter=_dummy_adapter)
        registry.register(d1)
        registry.register(d2)

        all_domains = registry.get_all()
        assert len(all_domains) == 2
        assert all_domains["a"] is d1
        assert all_domains["b"] is d2

    def test_get_node_names(self):
        registry = DomainGraphRegistry()
        registry.register(DomainGraph(name="x", node_name="x_node", label="X", adapter=_dummy_adapter))
        registry.register(DomainGraph(name="y", node_name="y_node", label="Y", adapter=_dummy_adapter))

        assert registry.get_node_names() == {"x_node", "y_node"}

    def test_duplicate_register_overwrites(self):
        registry = DomainGraphRegistry()
        d1 = DomainGraph(name="dup", node_name="old_node", label="Old", adapter=_dummy_adapter)
        d2 = DomainGraph(name="dup", node_name="new_node", label="New", adapter=_dummy_adapter)
        registry.register(d1)
        registry.register(d2)

        assert registry.get("dup") is d2


class TestCSGraphRegistered:
    """验证 CS Graph 通过 backend.domains 自注册到全局 registry"""

    def test_cs_graph_in_global_registry(self):
        import backend.domains  # noqa: F401 — 触发自注册
        from backend.orchestration.domain_registry import domain_graph_registry

        domain = domain_graph_registry.get("customer_service")
        assert domain is not None
        assert domain.node_name == "cs_graph_node"
        assert domain.label == "客服图执行"
        assert callable(domain.adapter)


class TestRouteSelectorWithRegistry:
    """验证 route_selector 通过 registry 动态分发"""

    def test_customer_service_routes_via_registry(self):
        import backend.domains  # noqa: F401
        from backend.orchestration.graph.router_node import route_selector

        state = {"route_mode": "customer_service", "cs_context": {}}
        assert route_selector(state) == "cs_graph_node"

    def test_unknown_mode_falls_to_planner(self):
        from backend.orchestration.graph.router_node import route_selector

        assert route_selector({"route_mode": "unknown_domain"}) == "planner"

    def test_builtin_modes_unaffected(self):
        from backend.orchestration.graph.router_node import route_selector

        assert route_selector({"route_mode": "direct"}) == "skill_executor"
        assert route_selector({"route_mode": "workflow"}) == "workflow_executor"
        assert route_selector({"route_mode": "plan"}) == "planner"
        assert route_selector({}) == "planner"
