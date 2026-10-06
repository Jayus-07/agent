"""orchestration/domain_registry.py — 域图注册表 + 域归属唯一派生源

全局单例，域图模块 import 时自注册。
builder.py / route_selector / system.py 通过此注册表动态发现域图。

自 2026-09-30 起，本模块同时是**域归属关系的唯一事实源**：决策层归一、
回写层登记活跃域、执行层选图、只读管理端展示（``GET /agents``）四处口径
全部由 ``DomainGraphRegistry`` 上的 ``route_mode_to_*`` / ``find_subflow_graph``
/ ``route_mode_attribution`` 派生，不再各自手写（守护见
tests/orchestration/test_domain_semantic_consistency.py）。
"""
from __future__ import annotations

import functools
from collections.abc import Callable, Mapping, Iterator
from typing import Any

from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.runtime_types import RuntimeTarget
from backend.shared.logger import logger


# ── 派生视图：把「域归属」收敛到注册表唯一事实源（2026-09-30）──────────────
# 背景：同一份归属关系（谁属于谁、命中后活跃域记谁、执行选哪张图）曾在四处
# 各自手写：决策层 _PREFILTER_DOMAIN_MAP、回写层 _ROUTE_MODE_DOMAIN、执行层
# _DEFAULT_DOMAIN_GRAPH_MODES 与 f"travel_{subflow}" 字符串拼接。任一处漏改
# 都是**静默失败**——D2「订酒店订到一半忘了」正是漏了回写层一处（G1）。
# 现改为：注册表是唯一事实源，其余各处（含只读管理端展示）全部由下方派生方法
# 生成，零手写。
#
# 为何是「活视图」而非快照：注册发生在 backend.domains 被 import 时，早于
# 多数模块的 import；若在模块级 `{... for g in registry.get_all()}` 直接取值，
# 取到的是**空表**（正是我们要消灭的静默故障）。活视图每次读取现算，与注册
# 时序无关。
class DerivedDomainMap(Mapping):
    """注册表派生的只读映射视图：每次读取现算，永不为「过期快照」。"""

    __slots__ = ("_derive", "_label")

    def __init__(self, derive: Callable[[], dict[Any, Any]], label: str = "") -> None:
        self._derive = derive
        self._label = label or "DerivedDomainMap"

    def _current(self) -> dict[Any, Any]:
        return self._derive()

    def __getitem__(self, key: Any) -> Any:
        try:
            return self._current()[key]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[Any]:
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())

    def __repr__(self) -> str:
        return f"{self._label}({self._current()!r})"


_LOAD_TRIGGERED = False


def _ensure_domains_loaded(registry: "DomainGraphRegistry") -> None:
    """派生视图的「源加载」保险（仅对**全局单例**生效）。

    派生视图唯一失效模式是「注册尚未发生」→ 表静默为空，与 G1
    「漏登记 → 活跃域永不写」是同一类静默故障。此处用**既有幂等显式入口**
    （builder 亦用同一入口）补触发一次，宁可多一次空转，也不要一张安静的空表。

    仅作用于 ``domain_graph_registry`` 单例：自注册模块只会注册进单例，
    对自建实例（测试里 new 出来的注册表）触发加载没有意义——显式判定避免
    「读一个空实例顺手把全局注册触发一遍」这种看不懂的副作用。
    """
    global _LOAD_TRIGGERED
    if registry._domains or _LOAD_TRIGGERED:
        return
    if registry is not domain_graph_registry:
        return
    _LOAD_TRIGGERED = True
    try:
        from backend.domains import register_all_domains

        register_all_domains()
    except Exception as exc:  # 加载失败不静默：告警后放行，由上层软失败兜底
        logger.warning("[DomainRegistry] 域图注册补触发失败，派生表可能为空: %s", exc)


def with_domain_attribution(domain_name: str, adapter):
    """域图适配器统一包裹 LLM 用量归因（M5 / 台账 D5）。

    在 builder 布线处包裹（adapter 的唯一消费点）而非 register()——注册表
    保持「存调用方原对象」的既有语义（identity 有测试冻结）。未来新增域图
    经 builder 自动获得 agent_domain 归因；域图子流为串行执行，ContextVar
    可传播到子图内部全部 LLM 调用；异常路径不吞（原样上抛）。
    """

    @functools.wraps(adapter)
    def wrapper(state: dict) -> dict:
        from backend.observability.llm_context import llm_attribution_scope

        with llm_attribution_scope(agent_domain=domain_name):
            try:
                from backend.orchestration.router.router_trace import (
                    record_runtime_attribution,
                )
                record_runtime_attribution(agent_domain=domain_name)
            except Exception:
                logger.debug("域图 Runtime Trace 归因写入失败", exc_info=True)
            return adapter(state)

    return wrapper


class DomainGraphRegistry:
    """域图注册表 — 管理所有独立子图的接入"""

    def __init__(self):
        self._domains: dict[str, DomainGraph] = {}

    def register(self, domain: DomainGraph) -> None:
        """注册 Runtime 描述符，并在启动期拦截归属冲突。"""

        other_domains = {
            name: item
            for name, item in self._domains.items()
            if name != domain.name
        }
        if not domain.runtime_id:
            raise ValueError(f"域 {domain.name!r} 必须声明 runtime_id")
        if domain.domain == domain.name:
            raise ValueError(f"域 {domain.name!r} 的父域不能自指")
        if domain.domain and domain.domain not in self._domains:
            raise ValueError(
                f"域 {domain.name!r} 的父域 {domain.domain!r} 尚未注册"
            )
        if any(
            item.runtime_id == domain.runtime_id
            for item in other_domains.values()
        ):
            raise ValueError(
                f"runtime_id {domain.runtime_id!r} 已被其他域占用"
            )
        if len(set(domain.aliases)) != len(domain.aliases):
            raise ValueError(f"域 {domain.name!r} 的 aliases 不能重复")
        if domain.name in domain.aliases:
            raise ValueError(f"域 {domain.name!r} 的 alias 不能与自身同名")
        reserved_aliases = {
            alias
            for item in other_domains.values()
            for alias in (item.name, *item.aliases)
        }
        conflicts = sorted(set(domain.aliases) & reserved_aliases)
        if conflicts:
            raise ValueError(
                f"域 {domain.name!r} 的 alias 冲突: {', '.join(conflicts)}"
            )
        if domain.name in self._domains:
            logger.warning("[DomainRegistry] 重复注册域图: %s，覆盖", domain.name)
        self._domains[domain.name] = domain
        logger.debug("[DomainRegistry] 注册域图: %s → %s", domain.name, domain.node_name)

    def get(self, route_mode: str) -> DomainGraph | None:
        return self._domains.get(route_mode)

    def get_all(self) -> dict[str, DomainGraph]:
        return dict(self._domains)

    def resolve_alias(self, route_mode: str) -> str | None:
        """把 canonical name 或 alias 解析为物理域图注册键。"""

        for graph in self._snapshot().values():
            if route_mode == graph.name or route_mode in graph.aliases:
                return graph.name
        return None

    def route_mode_to_runtime_target(self) -> dict[str, RuntimeTarget]:
        """派生 route_mode/alias → RuntimeTarget 活视图。"""

        result: dict[str, RuntimeTarget] = {}
        for graph in self._snapshot().values():
            target = RuntimeTarget(
                type=graph.runtime_type,
                id=graph.runtime_id or graph.name,
                subflow=graph.decision_subflow or graph.subflow,
            )
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = target
        return result

    def route_mode_to_family(self) -> dict[str, Any]:
        """派生 route_mode/alias → RuntimeType 活视图。"""

        result: dict[str, Any] = {}
        for graph in self._snapshot().values():
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = graph.runtime_type
        return result

    def route_mode_to_entry_mode(self) -> dict[str, tuple[str, ...]]:
        """派生 route_mode/alias → 允许的入口交互模式活视图。"""

        result: dict[str, tuple[str, ...]] = {}
        for graph in self._snapshot().values():
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = graph.entry_modes
        return result

    def route_mode_to_entry_mode_key(self) -> dict[str, str | None]:
        """派生 route_mode/alias → 配置中心入口模式键活视图。"""

        result: dict[str, str | None] = {}
        for graph in self._snapshot().values():
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = graph.entry_mode_key
        return result

    def route_mode_to_domain_family(self) -> dict[str, str]:
        """派生 route_mode/alias → 顶级域族活视图。

        子流图（如 travel_booking）和顶级图共享父域族；普通新域默认以
        自身注册名作为域族。入口模式与 handoff 目标均从此视图派生。
        """

        result: dict[str, str] = {}
        for graph in self._snapshot().values():
            family = graph.domain or graph.name
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = family
        return result

    def get_node_names(self) -> set[str]:
        return {d.node_name for d in self._domains.values()}

    # ── 域归属的唯一派生源（三处消费方共用，禁止再各写一份）──────────────

    def _snapshot(self) -> dict[str, DomainGraph]:
        _ensure_domains_loaded(self)
        return dict(self._domains)

    def route_mode_to_active_domain(self) -> dict[str, str]:
        """【回写层】route_mode → ConversationContext.active_domain。

        命中后活跃域即该域图自身，故为恒等映射；「登记哪些键」= 注册表有哪些
        域图。原为手写字典（prefilter_chain._ROUTE_MODE_DOMAIN），D2 的 G1
        就是漏登记两个交易域所致。
        """
        result: dict[str, str] = {}
        for graph in self._snapshot().values():
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = graph.name
        return result

    def route_mode_to_domain_decision(self) -> dict[str, tuple[str, str | None]]:
        """【决策层】route_mode → (顶级域, subflow 展示位)。

        顶级域 = graph.domain 或（顶级域图时）自身；subflow 展示位优先取
        ``decision_subflow``（顶级域自身的活动标签，如 travel→planning），
        否则回退 ``subflow``（独立生命周期子流，如 booking/commerce）。
        原为手写字典（domain_router._PREFILTER_DOMAIN_MAP 的域图部分）。
        """
        result: dict[str, tuple[str, str | None]] = {}
        for graph in self._snapshot().values():
            decision = (
                graph.domain or graph.name,
                graph.decision_subflow or graph.subflow,
            )
            for route_mode in (graph.name, *graph.aliases):
                result[route_mode] = decision
        return result

    def route_mode_to_graph_mode(self) -> dict[str, str]:
        """【执行层默认表】顶级域图名 → 自身。

        只含 domain is None 的图：子流图不作为顶级域入口单列。
        原为手写字典（execution_mode._DEFAULT_DOMAIN_GRAPH_MODES）。
        """
        return {
            graph.name: graph.name
            for graph in self._snapshot().values()
            if graph.domain is None
        }

    def route_mode_attribution(
        self, route_mode: str
    ) -> tuple[str | None, str | None, str | None]:
        """【展示层】route_mode → (顶级域 route_mode, 顶级域展示标签, subflow)。

        顶级域图自身返回 ``(None, None, None)``——「本图即顶级域」不是子流。
        顶级域展示标签取**父图自身的 ``label``**（与父图在管理端卡片上的标题
        同源），不另造业务名：前端原先手写的「Travel Domain」在后端并不存在，
        是展示层自己发明的名字，且已与父图 label 漂移（实证见 STOP E §9）。

        供只读管理端接口 ``GET /agents`` 透传归属，取代前端手写映射
        ``ROUTE_MODE_DOMAIN_META``（第五处手写副本，2026-09-30 消除）。
        """
        graphs = self._snapshot()
        graph = graphs.get(route_mode)
        if graph is None or not graph.domain:
            return (None, None, None)
        parent = graphs.get(graph.domain)
        return (graph.domain, parent.label if parent else None, graph.subflow)

    def find_subflow_graph(self, domain: str, subflow: str | None) -> str | None:
        """【执行层选图】给定 (顶级域, subflow) → 物理域图名；无则 None。

        取代原 ``f"travel_{subflow}"`` 字符串拼接——拼接把「谁是 travel 的子流」
        编码进命名约定，注册表新增子流时还要记得同步改拼接，漏改即**静默重入
        顶级域图**（不报错、办错事）。
        """
        if not subflow:
            return None
        for graph in self._snapshot().values():
            if graph.domain == domain and graph.subflow == subflow:
                return graph.name
        return None


domain_graph_registry = DomainGraphRegistry()
