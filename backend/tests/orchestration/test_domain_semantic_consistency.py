"""tests/orchestration/test_domain_semantic_consistency.py — 域归属单一事实源守护

口径：**注册表（DomainGraphRegistry）是域归属的唯一事实源**。

同一份「谁属于谁 / 命中后活跃域记谁 / 执行选哪张图」曾在四处各自手写，任一处
漏改都是**静默失败**——D2「订酒店订到一半忘了」正是漏了回写层一处（G1）。
自 2026-09-30 起，三处消费方全部改为注册表派生（零手写），本文件也从
「锁三处口径一致」升级为「锁三处口径**确由注册表生成**」：

1. 决策层 ``DomainRouter._PREFILTER_DOMAIN_MAP``：route_mode → (顶级域, subflow)
2. 回写层 ``prefilter_chain._ROUTE_MODE_DOMAIN``：route_mode → active_domain
3. 执行层 ``execution_mode._DEFAULT_DOMAIN_GRAPH_MODES`` + 子流选图

三层语义**刻意不同**，不是漂移：
  - 回写层记**物理域图名**（续轮要精确重入子图，记 travel 会重入规划图）；
  - 决策层记**顶级域**（供能力路由/展示，travel_booking → travel）；
  - 执行层把两者合起来选图（子流查注册表归属，不再 ``f"travel_{subflow}"`` 拼接）。

唯一保留的手写项是 ``_NON_GRAPH_ROUTE_MODES`` 里的 ``general_chat``：有路由模式
但**没有域图**（寒暄直答），注册表装不下；它不得进回写层，否则一句「你好」会
把进行中的跨轮任务上下文冲掉。

注册表上三个语义**必须分清**（混用即漂移）：
  - ``domain``：归属——谁属于谁（唯一事实源，2026-09-30 新增）；
  - ``subflow``：**独立生命周期子流**标签（STOP E §6.2 冻结口径，只给
    commerce/booking；顶级域图必须为空）；
  - ``decision_subflow``：顶级域**自身的活动标签**（travel→planning），
    仅用于决策层 subflow 展示位，``None`` 时回退 ``subflow``。

route_mode / 注册键本身是永久保留的内部调度标识（改名会静默落入 planner 兜底），
本测试只锁「归属口径」，不锁、也不应锁任何调度行为变更。
"""
from __future__ import annotations

import backend.domains  # noqa: F401  # import 即触发五个域图自注册
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import (
    DerivedDomainMap,
    DomainGraphRegistry,
    domain_graph_registry,
)
from backend.orchestration.graph.routing.prefilter_chain import _ROUTE_MODE_DOMAIN
from backend.orchestration.router.domain_router import (
    _NON_GRAPH_ROUTE_MODES,
    _PREFILTER_DOMAIN_MAP,
    DomainRouter,
)
from backend.orchestration.router.execution_mode import (
    _DEFAULT_DOMAIN_GRAPH_MODES,
    ExecutionModeResolver,
)

# 三个顶级业务域（STOP E 口径）；子流域图 → (顶级域, subflow) 的期望归一。
_TOP_LEVEL_DOMAINS = {"customer_service", "travel", "selection_funnel"}
_SUBFLOW_GRAPHS = {
    "travel_commerce": ("travel", "commerce"),
    "travel_booking": ("travel", "booking"),
}
# 决策层 subflow 展示位的**契约值**（trace/管理端展示口径，改这里=改对外展示）。
_EXPECTED_DECISION_SUBFLOW = {
    "customer_service": None,
    "travel": "planning",
    "selection_funnel": "funnel",
    "travel_booking": "booking",
    "travel_commerce": "commerce",
    "general_chat": None,
}


def _noop_adapter(state: dict) -> dict:  # pragma: no cover - 仅需「可调用」
    return state


def _graph_modes() -> dict[str, str]:
    """回写/执行层共用的域图模式表（与 prefilter_chain._with_router_decisions 同构）。"""
    return {name: name for name in domain_graph_registry.get_all()}


def _capability_stub(domain: str = "") -> dict:
    """域内 capability 决策占位（本例只校验域图目标，不需要真实候选）。"""
    return {
        "domain": domain, "capability": None, "candidates": [],
        "confidence": 0.0, "source": "prefilter", "reasoning": "consistency-guard",
    }


# ── 一、注册表 ↔ 三层口径的语义一致（原 4 例）──────────────────────────────


def test_every_registered_graph_is_projectable_by_router():
    """注册表里每个物理域图，Router 决策层都必须能归一——不许出现 Router 不认识的注册键。"""
    for name in domain_graph_registry.get_all():
        assert name in _PREFILTER_DOMAIN_MAP, (
            f"域图 {name} 未被 DomainRouter 归一：决策层与注册表口径漂移"
        )


def test_top_level_graphs_project_to_themselves():
    """顶级域图：归一的顶级域必须是自身；归属判据是 ``domain is None``。

    同时钉住 STOP E §6.2 的**冻结口径**：顶级域图的 ``subflow`` 必须为空——
    ``subflow`` 只表示「独立生命周期的子流」（commerce/booking），不承载顶级域
    自身的活动标签（那由 ``decision_subflow`` 表达）。若有人把 travel 的
    planning 标签塞进 ``subflow``，本例如红。
    """
    for name in _TOP_LEVEL_DOMAINS:
        graph = domain_graph_registry.get(name)
        assert graph is not None, f"顶级域 {name} 未注册"
        assert graph.domain is None, (
            f"顶级域 {name} 不应声明归属域（domain={graph.domain!r}）"
        )
        assert graph.subflow is None, (
            f"顶级域 {name} 的 subflow 应为空（STOP E §6.2 冻结口径）："
            f"独立生命周期子流才写 subflow={graph.subflow!r}；"
            f"顶级域自身的活动标签请写 decision_subflow"
        )
        assert _PREFILTER_DOMAIN_MAP[name][0] == name


def test_decision_subflow_slot_is_derived_from_graph_metadata():
    """决策层的 subflow 展示位取自注册表声明，并锁定其**契约值**（非重算式）。

    travel/selection_funnel 是顶级域但各有活动标签（planning/funnel），由
    ``decision_subflow`` 承载；booking/commerce 是子流，回退 ``subflow``。

    期望值刻意写成**字面量**：若改写成 ``graph.decision_subflow or graph.subflow``
    去比对 ``_PREFILTER_DOMAIN_MAP``，两边同源，改了注册表会一起变——恒真断言，
    什么都测不出（本用例初版即踩此坑，反向验证时暴露，已修正）。
    """
    for name, expected in _EXPECTED_DECISION_SUBFLOW.items():
        actual = _PREFILTER_DOMAIN_MAP[name][1]
        assert actual == expected, (
            f"{name} 决策层 subflow 展示位 = {actual!r}，期望 {expected!r}："
            f"展示标签漂移（trace/前端归属说明会跟着错）"
        )
    # 顶级域的活动标签必须在 decision_subflow 上，而不是塞进冻结的 subflow
    assert domain_graph_registry.get("travel").decision_subflow == "planning"
    assert domain_graph_registry.get("selection_funnel").decision_subflow == "funnel"


def test_subflow_graphs_consistent_between_router_and_registry():
    """子流域图：registry 的 (domain, subflow) 与 Router 归一的完全一致，且顶级域真实注册。"""
    for name, (domain, subflow) in _SUBFLOW_GRAPHS.items():
        graph = domain_graph_registry.get(name)
        assert graph is not None, f"子流域图 {name} 未注册"
        assert graph.domain == domain, (
            f"{name} 归属域为 {graph.domain!r}，与 DomainRouter 归一 {domain!r} 不一致"
        )
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


# ── 二、回写层守护（Phase 5 / D2 新增）────────────────────────────────────
# 这一处在修复前无任何守护，且正是 D2 两跳断片的根因所在。


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


# ── 三、派生守护（2026-09-30：口径改为注册表生成，共 5 例）─────────────────
# 前三例锁「三处确是活视图、不是照抄的手写字典」，后两例锁「派生对新域同样成立」。


def test_layer_tables_are_live_registry_views():
    """三处口径必须是注册表**活视图**（结构守护）。

    若有人把某处换回字面量 dict，本例如红——手写就等于给「漏改」重新留了口子，
    而那正是 D2/G1 的成因。
    """
    for name, table in (
        ("_ROUTE_MODE_DOMAIN", _ROUTE_MODE_DOMAIN),
        ("_PREFILTER_DOMAIN_MAP", _PREFILTER_DOMAIN_MAP),
        ("_DEFAULT_DOMAIN_GRAPH_MODES", _DEFAULT_DOMAIN_GRAPH_MODES),
    ):
        assert isinstance(table, DerivedDomainMap), (
            f"{name} 不是注册表派生视图（疑被改回手写字典）："
            f"手写即可能再漏一处，退化成 D2/G1 类静默故障"
        )


def test_derived_tables_equal_registry_derivations():
    """模块级口径 === 注册表派生结果（接线守护：忘换源 / 混入手写项会在此红）。"""
    assert dict(_ROUTE_MODE_DOMAIN) == domain_graph_registry.route_mode_to_active_domain()

    assert dict(_DEFAULT_DOMAIN_GRAPH_MODES) == domain_graph_registry.route_mode_to_graph_mode()

    expected = dict(domain_graph_registry.route_mode_to_domain_decision())
    expected.update(_NON_GRAPH_ROUTE_MODES)
    assert dict(_PREFILTER_DOMAIN_MAP) == expected, (
        "决策层口径与注册表派生不一致：除 general_chat 伪模式外不应有任何手写项"
    )
    assert set(_NON_GRAPH_ROUTE_MODES) == {"general_chat"}, (
        "决策层手写残留只允许 general_chat（无域图的伪模式）；"
        "新增其它手写项说明域归属又分叉了"
    )


def test_derivations_generalize_to_a_new_domain():
    """派生函数对「还不存在的域」同样成立——证明是**生成**，不是照抄现值。

    用自建注册表实例（不污染全局单例）断言：新增顶级域 + 新增子流域，
    三层派生结果自动正确，无需任何手工登记。
    """
    registry = DomainGraphRegistry()
    registry.register(DomainGraph(
        name="newtop", node_name="newtop_node", label="新顶级域", adapter=_noop_adapter))
    registry.register(DomainGraph(
        name="newsub", node_name="newsub_node", label="新子流", adapter=_noop_adapter,
        domain="newtop", subflow="sub"))

    assert registry.route_mode_to_active_domain() == {
        "newtop": "newtop", "newsub": "newsub",
    }
    assert registry.route_mode_to_domain_decision() == {
        "newtop": ("newtop", None), "newsub": ("newtop", "sub"),
    }
    # 子流图不作为顶级域入口单列
    assert registry.route_mode_to_graph_mode() == {"newtop": "newtop"}
    assert registry.find_subflow_graph("newtop", "sub") == "newsub"
    assert registry.find_subflow_graph("newsub", "sub") is None  # 归属域不匹配


def test_registering_new_domain_propagates_to_all_layers():
    """新增域图注册进全局单例 → 三层口径**自动**收录，零手工改动。

    这是「派生」的直接证据：执行层刻意只传顶级域表（不含探针），子流仍被正确
    选中，说明选图走的是注册表归属查询，而不是手写映射或字符串拼接。
    探针用后立即还原，不污染其它用例。
    """
    probe_top = DomainGraph(
        name="_probe_top", node_name="_probe_top_node",
        label="探针顶级域", adapter=_noop_adapter)
    probe_sub = DomainGraph(
        name="_probe_sub", node_name="_probe_sub_node",
        label="探针子流", adapter=_noop_adapter,
        domain="_probe_top", subflow="probe")

    snapshot = domain_graph_registry.get_all()
    try:
        domain_graph_registry.register(probe_top)
        domain_graph_registry.register(probe_sub)

        assert _ROUTE_MODE_DOMAIN["_probe_top"] == "_probe_top"
        assert _ROUTE_MODE_DOMAIN["_probe_sub"] == "_probe_sub"
        assert _PREFILTER_DOMAIN_MAP["_probe_top"] == ("_probe_top", None)
        assert _PREFILTER_DOMAIN_MAP["_probe_sub"] == ("_probe_top", "probe")

        resolver = ExecutionModeResolver(domain_graph_modes={"travel": "travel"})
        decision = DomainRouter._from_prefilter({"route_mode": "_probe_sub"})
        assert decision is not None
        resolved = resolver.resolve(
            decision, _capability_stub(decision["domain"]), {"route_mode": "_probe_sub"})
        assert resolved.mode == "domain_graph", (
            f"新增子流域图执行层未落 domain_graph（mode={resolved.mode}）"
        )
        assert resolved.target == "_probe_sub", (
            f"新增子流域图解析为 {resolved.target!r}：说明选图仍在靠手写/拼接，"
            f"而非注册表归属"
        )
    finally:
        domain_graph_registry._domains.clear()
        domain_graph_registry._domains.update(snapshot)


# ── 四、执行层往返与选图（原 2 例，语义随派生更新）────────────────────────


def test_route_mode_round_trips_to_its_own_graph():
    """端到端往返：route_mode → 决策层归一 → 执行层解析，必须回到同一个物理域图，
    且与回写层记录的 active_domain 逐字一致。

    这是三层口径的**行为等价**校验（非仅字面相等）。
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
    """子流选图在**最简构造**（不传 domain_graph_modes）下仍须解析到自身。

    执行层默认表由 ``route_mode_to_graph_mode()`` 派生，**只含顶级域图**——
    所以子流能解析到自身，只可能来自注册表归属查询（``find_subflow_graph``），
    不可能来自默认表。本例锁死这条路径，避免「以为默认表里有，就把归属查询删了」。
    """
    resolver = ExecutionModeResolver()  # 走派生默认表：刻意不传 domain_graph_modes
    for name in _SUBFLOW_GRAPHS:
        decision = DomainRouter._from_prefilter({"route_mode": name})
        assert decision is not None
        resolved = resolver.resolve(
            decision, _capability_stub(decision["domain"]), {"route_mode": name})
        assert resolved.mode == "domain_graph", (
            f"{name} 最简构造下未落到 domain_graph（mode={resolved.mode}）："
            f"注册表归属查询可能已被删除"
        )
        assert resolved.target == name, (
            f"{name} 最简构造下解析为 {resolved.target!r}："
            f"子流会被静默重定向到顶级域图"
        )


def test_subflow_lookup_is_ownership_driven():
    """执行层选图只看注册表归属：非子流标签 / 错域 / 空值都必须查不到。

    关键回归：travel 自身的活动标签是 ``planning``，它**不是**子流图——
    旧实现 ``f"travel_{subflow}"`` 只在 subflow ∈ {booking, commerce} 时才拼接，
    靠硬编码集合兜住；派生后由归属事实保证，本例钉住该性质。
    """
    assert domain_graph_registry.find_subflow_graph("travel", "booking") == "travel_booking"
    assert domain_graph_registry.find_subflow_graph("travel", "commerce") == "travel_commerce"
    assert domain_graph_registry.find_subflow_graph("travel", "planning") is None
    assert domain_graph_registry.find_subflow_graph("customer_service", None) is None
    assert domain_graph_registry.find_subflow_graph("travel", "") is None
    assert domain_graph_registry.find_subflow_graph("unknown", "booking") is None
