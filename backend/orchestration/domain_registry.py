"""orchestration/domain_registry.py — 域图注册表

全局单例，域图模块 import 时自注册。
builder.py / route_selector / system.py 通过此注册表动态发现域图。
"""
from __future__ import annotations

from backend.orchestration.domain_graph import DomainGraph
from backend.shared.logger import logger


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
