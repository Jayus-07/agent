"""travel/register.py — 旅游域图自注册

import 此模块即把旅游域图注册到 domain_graph_registry，
由 backend/domains/__init__.py 统一触发。

Main Graph 的 builder 会自动为其布线（节点 + router 条件边 + 直连 END），
无需改动 builder.py —— 与客服域接入方式完全一致。

同时在此处完成旅游域的外部数据源接线：真实路线数据源（腾讯位置服务）
必须在任何排程发生之前注入 estimate_leg，否则第一轮排程就会用本地估算，
行程单上的 source 会与实际不符。
"""
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.orchestration.graph.travel_graph_node import travel_graph_node
from backend.tools.travel.live_map import install_live_map

domain_graph_registry.register(DomainGraph(
    name="travel",
    node_name="travel_graph_node",
    label="旅游规划图执行",
    adapter=travel_graph_node,
))

# 幂等：未配置 Key 或开关关闭时保持本地估算，不影响域可用性
install_live_map()
