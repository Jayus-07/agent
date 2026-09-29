"""tests/orchestration/test_domain_semantic_consistency.py — Domain 语义一致性守护（STOP E）

锁两层口径不漂移：
1. DomainRouter 决策层把 route_mode 归一为 (顶级域, subflow)——travel_commerce /
   travel_booking 归一为 domain=travel 的子流，不是独立业务域；
2. DomainGraphRegistry 展示元数据（DomainGraph.subflow）表达同一语义。

route_mode / 注册键本身是永久保留的内部调度标识（改名会静默落入 planner 兜底），
本测试只锁「语义一致」，不锁、也不应锁任何调度行为变更。
"""
from __future__ import annotations

import backend.domains  # noqa: F401  # import 即触发五个域图自注册
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.router.domain_router import (
    _PREFILTER_DOMAIN_MAP,
    DomainRouter,
)

# 三个顶级业务域（STOP E 口径）；子流域图 → (顶级域, subflow) 的期望归一。
_TOP_LEVEL_DOMAINS = {"customer_service", "travel", "selection_funnel"}
_SUBFLOW_GRAPHS = {
    "travel_commerce": ("travel", "commerce"),
    "travel_booking": ("travel", "booking"),
}


def test_every_registered_graph_is_projectable_by_router():
    """注册表里每个物理域图，Router 决策层都必须能归一——不许出现 Router 不认识的注册键。"""
    for name in domain_graph_registry.get_all():
        assert name in _PREFILTER_DOMAIN_MAP, (
            f"域图 {name} 未被 DomainRouter 归一：决策层与注册表口径漂移"
        )


def test_top_level_graphs_project_to_themselves():
    """顶级域图：归一的顶级域必须是自身（无跨域投影），且不带 subflow 展示元数据。"""
    for name in _TOP_LEVEL_DOMAINS:
        graph = domain_graph_registry.get(name)
        assert graph is not None, f"顶级域 {name} 未注册"
        assert graph.subflow is None, f"顶级域 {name} 不应声明 subflow"
        assert _PREFILTER_DOMAIN_MAP[name][0] == name


def test_subflow_graphs_consistent_between_router_and_registry():
    """子流域图：registry.subflow 与 Router 归一的 (顶级域, subflow) 完全一致，且顶级域真实注册。"""
    for name, (domain, subflow) in _SUBFLOW_GRAPHS.items():
        graph = domain_graph_registry.get(name)
        assert graph is not None, f"子流域图 {name} 未注册"
        assert graph.subflow == subflow, (
            f"{name} 注册元数据 subflow={graph.subflow!r}，与 DomainRouter 归一 {subflow!r} 不一致"
        )
        assert _PREFILTER_DOMAIN_MAP[name] == (domain, subflow)
        assert domain_graph_registry.get(domain) is not None, (
            f"{name} 声称属于顶级域 {domain}，但后者未注册"
        )


def test_domain_router_decision_matches_registry_metadata():
    """端到端归一：DomainRouter 对 prefilter 结果的真实输出与注册表元数据语义一致（验收样例）。"""
    for name, (domain, subflow) in _SUBFLOW_GRAPHS.items():
        decision = DomainRouter._from_prefilter({"route_mode": name})
        assert decision is not None
        assert decision["domain"] == domain == "travel"
        assert decision["subflow"] == subflow
        assert decision["subflow"] == domain_graph_registry.get(name).subflow
