"""travel/commerce/register.py — Commerce 域图自注册（STOP K6）

与 travel/register.py 同模式：import 即注册，builder 自动发现布线
（router 条件边 + 直连 END），不改 builder.py。开关关闭时域图仍注册——
**命中与否由 prefilter 的 TRAVEL_COMMERCE_ENABLED 总闸决定**（与旅游域
「注册恒在、prefilter 把门」同策略：域图不感知开关，路由层单点判定）。
"""
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.travel.commerce.graph_node import travel_commerce_graph_node

domain_graph_registry.register(DomainGraph(
    name="travel_commerce",
    node_name="travel_commerce_graph_node",
    label="旅游商务查询（酒店/机票）",
    adapter=travel_commerce_graph_node,
    subflow="commerce",  # STOP E 语义展示：Travel Domain 的 commerce 子流，与 DomainRouter 归一口径一致
))
