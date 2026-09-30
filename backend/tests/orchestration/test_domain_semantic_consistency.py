"""tests/orchestration/test_domain_semantic_consistency.py — Domain 语义一致性守护（STOP E / Phase 5 D2）

锁「注册表 / 决策 / 回写」三处口径不漂移：
1. 决策层（DomainRouter）：route_mode 归一为 (顶级域, subflow)——travel_commerce /
   travel_booking 归一为 domain=travel 的子流，不是独立业务域；
2. 展示层（DomainGraphRegistry）：DomainGraph.subflow 表达同一语义；
3. 回写层（_ROUTE_MODE_DOMAIN）：prefilter 命中后 active_domain 记**物理域图名**
   （与决策层记顶级域**刻意不同**——续轮需精确重入子图，不是重入顶级域）。

第 3 处在 Phase 5 之前无任何守护，正是 D2「订酒店订到一半忘了」的根因（G1）：
漏登记 travel_booking / travel_commerce → prefilter 命中取 domain=None →
mark_domain_turn 早退 → active_domain 永不写 → 下一轮纯槽位值回答无人认领。
末尾两例延伸到执行层：一例做「route_mode → 决策 → 执行」往返校验，另一例锁
最简构造下子流仍解析到自身——令 execution_mode 内 ``f"travel_{subflow}"``
那第四处手写无法与其余两处悄悄分叉。

route_mode / 注册键本身是永久保留的内部调度标识（改名会静默落入 planner 兜底），
本测试只锁「语义一致」，不锁、也不应锁任何调度行为变更。
"""
from __future__ import annotations

import backend.domains  # noqa: F401  # import 即触发五个域图自注册
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.routing.prefilter_chain import _ROUTE_MODE_DOMAIN
from backend.orchestration.router.domain_router import (
    _PREFILTER_DOMAIN_MAP,
    DomainRouter,
)
from backend.orchestration.router.execution_mode import ExecutionModeResolver

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


# ── 回写层守护（第三处口径，Phase 5 / D2 新增，共 6 例）────────────────────
# 这一处在修复前无任何守护，且正是 D2 两跳断片的根因所在；下面六例把它与
# 已受守护的决策层/展示层锁死，并延伸到执行层做行为等价校验。


def _graph_modes() -> dict[str, str]:
    """回写/执行层共用的域图模式表（与 prefilter_chain._with_router_decisions 同构）。"""
    return {name: name for name in domain_graph_registry.get_all()}


def _capability_stub(domain: str = "") -> dict:
    """域内 capability 决策占位（本例只校验域图目标，不需要真实候选）。"""
    return {
        "domain": domain, "capability": None, "candidates": [],
        "confidence": 0.0, "source": "prefilter", "reasoning": "consistency-guard",
    }


def test_every_registered_graph_is_write_back_registered():
    """注册表每个物理域图都必须在 _ROUTE_MODE_DOMAIN 有登记（G1 回归守护）。

    漏登记 = prefilter 命中后回写取 domain=None → mark_domain_turn 早退 →
    active_domain 永不写 → 下一轮纯槽位值回答（「10月3日」）无人认领 → 断片。
    本例如在，新增第四个域时漏登记会**直接红**，而不是等用户发现「它又忘了」。
    """
    missing = [
        name for name in domain_graph_registry.get_all()
        if name not in _ROUTE_MODE_DOMAIN
    ]
    assert not missing, (
        f"域图 {missing} 未在 _ROUTE_MODE_DOMAIN 登记：prefilter 命中后无法回写"
        f"活跃域，跨轮续填/延续会断片（D2/G1 类问题）"
    )


def test_write_back_targets_are_registered_graphs():
    """回写值的可重入性：写进 active_domain 的目标必须是已注册域图。"""
    for route_mode, active_domain in _ROUTE_MODE_DOMAIN.items():
        assert domain_graph_registry.get(active_domain) is not None, (
            f"_ROUTE_MODE_DOMAIN[{route_mode!r}] = {active_domain!r} 不是已注册域图："
            f"回写的活跃域不可被续轮重新进入"
        )


def test_write_back_keys_are_known_to_decision_layer():
    """回写层的键必须都能被决策层归一；伪模式（寒暄）不得落活跃域。

    ``general_chat`` 只存在于决策层（供直答分流），刻意不进回写层——否则
    一句「你好」会把进行中的跨轮任务上下文冲掉。
    """
    for route_mode in _ROUTE_MODE_DOMAIN:
        assert route_mode in _PREFILTER_DOMAIN_MAP, (
            f"_ROUTE_MODE_DOMAIN 含 {route_mode!r}，但 _PREFILTER_DOMAIN_MAP 未归一："
            f"回写了决策层不认识的路由模式"
        )
    assert "general_chat" not in _ROUTE_MODE_DOMAIN, (
        "寒暄直答不得登记活跃域：会冲掉进行中的跨轮任务上下文"
    )


def test_write_back_records_physical_graph_not_top_level():
    """回写层记**物理域图名**，与决策层记**顶级域**刻意不同——两处语义不同不是
    漂移，是分工：决策层供能力路由/展示，回写层供续轮精确重入子图。

    若回写层改记顶级域，BookingPendingResolver / 执行层拿到 travel 会落回
    规划图，两跳再次断片。
    """
    for name in domain_graph_registry.get_all():
        actual = _ROUTE_MODE_DOMAIN.get(name)
        assert actual == name, (
            f"回写层 {name!r} → {actual!r}：应记物理域图自身（缺登记见上一例）"
        )
    for name, (domain, _subflow) in _SUBFLOW_GRAPHS.items():
        assert _ROUTE_MODE_DOMAIN[name] != domain, (
            f"{name} 回写层不应记顶级域 {domain!r}：续轮会重入规划图而非子图"
        )


def test_route_mode_round_trips_to_its_own_graph():
    """端到端往返：route_mode → 决策层归一 → 执行层解析，必须回到同一个物理域图，
    且与回写层记录的 active_domain 逐字一致。

    这是三处口径的**行为等价**校验（非仅字面相等），顺带把 execution_mode 中
    ``f"travel_{subflow}"`` 那第四处手写拼接收进防线——它与 _PREFILTER_DOMAIN_MAP
    或 _ROUTE_MODE_DOMAIN 一旦分叉，续轮就会重入错图（静默失败）。
    """
    resolver = ExecutionModeResolver(domain_graph_modes=_graph_modes())
    for name in domain_graph_registry.get_all():
        decision = DomainRouter._from_prefilter({"route_mode": name})
        assert decision is not None, f"route_mode={name!r} 决策层未归一"
        resolved = resolver.resolve(
            decision, _capability_stub(decision["domain"]), {"route_mode": name})
        assert resolved.mode == "domain_graph", (
            f"{name!r} 执行层未落到 domain_graph（得到 mode={resolved.mode}）"
        )
        assert resolved.target == name, (
            f"往返不一致：route_mode={name!r} 经决策+执行层解析为 {resolved.target!r}"
        )
        assert _ROUTE_MODE_DOMAIN[name] == resolved.target, (
            f"回写层记 {_ROUTE_MODE_DOMAIN[name]!r}，执行层派 {resolved.target!r}："
            f"续轮会重入错图"
        )


def test_subflow_resolution_holds_without_explicit_graph_modes():
    """旅行子流在**最简构造**（不传 domain_graph_modes）下仍须解析到自身。

    execution_mode 内 ``f"travel_{subflow}"`` 拼接分支因此是**承重代码**：
    默认域图表 ``_DEFAULT_DOMAIN_GRAPH_MODES`` 只列了三个顶级域，若删掉
    拼接分支，未显式传表的调用会把 travel_booking 解析成 travel → 静默落回
    规划图（非响亮失败）。本例锁住该行为，避免「以为表里有、就把分支删了」。
    """
    resolver = ExecutionModeResolver()  # 走默认表：刻意不传 domain_graph_modes
    for name in _SUBFLOW_GRAPHS:
        decision = DomainRouter._from_prefilter({"route_mode": name})
        assert decision is not None
        resolved = resolver.resolve(
            decision, _capability_stub(decision["domain"]), {"route_mode": name})
        assert resolved.mode == "domain_graph", (
            f"{name} 最简构造下未落到 domain_graph（mode={resolved.mode}）："
            f"拼接分支可能已被删除"
        )
        assert resolved.target == name, (
            f"{name} 最简构造下解析为 {resolved.target!r}："
            f"子流会被静默重定向到顶级域图"
        )
