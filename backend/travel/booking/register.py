"""travel/booking/register.py — Booking 域图自注册（STOP L9）

与 travel/commerce/register.py 同模式：import 即注册，builder 自动布线。
命中与否由 booking prefilter 的 TRAVEL_BOOKING_ENABLED 总闸决定。
"""
from backend.orchestration.domain_graph import DomainGraph
from backend.orchestration.domain_registry import domain_graph_registry
from backend.travel.booking.graph_node import travel_booking_graph_node

domain_graph_registry.register(DomainGraph(
    name="travel_booking",
    node_name="travel_booking_graph_node",
    label="旅游预订（Booking Transaction）",
    adapter=travel_booking_graph_node,
    subflow="booking",  # STOP E 语义展示：Travel Domain 的 booking 子流，与 DomainRouter 归一口径一致
))
