"""orchestration/domain_graph.py — 域图协议定义

DomainGraph 是独立子图（如 CS Graph）接入 Main Graph 的标准契约。
每个域图通过 DomainGraphRegistry 自注册，Main Graph 自动发现并布线。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DomainGraph:
    """域图注册描述符

    Attributes:
        name: 路由模式标识，与 router_node 输出的 route_mode 对应
        node_name: Main Graph 中的 LangGraph 节点名
        label: 可视化标签（前端展示用）
        adapter: 适配器函数 (state: dict) -> dict，负责状态转换 + 子图调用 + 结果映射
    """

    name: str
    node_name: str
    label: str
    adapter: Callable[[dict], dict]
