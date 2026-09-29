"""orchestration/domain_registry.py — 域图注册表

全局单例，域图模块 import 时自注册。
builder.py / route_selector / system.py 通过此注册表动态发现域图。
"""
from __future__ import annotations

import functools

from backend.orchestration.domain_graph import DomainGraph
from backend.shared.logger import logger


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
            return adapter(state)

    return wrapper


class DomainGraphRegistry:
    """域图注册表 — 管理所有独立子图的接入"""

    def __init__(self):
        self._domains: dict[str, DomainGraph] = {}

    def register(self, domain: DomainGraph) -> None:
        if domain.name in self._domains:
            logger.warning("[DomainRegistry] 重复注册域图: %s，覆盖", domain.name)
        self._domains[domain.name] = domain
        logger.debug("[DomainRegistry] 注册域图: %s → %s", domain.name, domain.node_name)

    def get(self, route_mode: str) -> DomainGraph | None:
        return self._domains.get(route_mode)

    def get_all(self) -> dict[str, DomainGraph]:
        return dict(self._domains)

    def get_node_names(self) -> set[str]:
        return {d.node_name for d in self._domains.values()}


domain_graph_registry = DomainGraphRegistry()
