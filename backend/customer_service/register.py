"""customer_service/register.py — CS Graph 域图自注册

import 此模块即将 CS Graph 注册到 domain_graph_registry。
由 backend/domains/__init__.py 统一触发。
"""
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.cs_graph_node import cs_graph_node

domain_graph_registry.register(DomainGraph(
    name="customer_service",
    node_name="cs_graph_node",
    label="客服图执行",
    adapter=cs_graph_node,
))
