"""backend/travel/providers_support/commerce — Hotel/Flight 契约位数据源实现

收编自原商务域图包（2026-11-01 收编重构）：原域图壳
（register/graph_builder/graph_node/graph_state/service/health/reporter/
telemetry）已删除，实现层保留为 **Lodging/Intercity 子智能体的数据源**
（travel.hotel.search / travel.flight.search 的实现与 Booking 复核的
确定性重搜，见 offer_search.py）。

生产只读闭环（原 K0 定稿，语义不变）：Hotel/Flight 搜索 → 归一化 →
Availability 语义 → Price Snapshot → 确定性排序 → 安全 Deep Link。
LLM 零参与事实链；Booking Create 属预订事务（backend/travel/booking/），
本包不触碰。

冻结边界（原 STOP I/J，继续生效）：本包不 import travel 域图内部
（graph_builder/validator/reporter/supervisor），不写 Itinerary/Poi/
TravelBrief；与行程域唯一交点是城市表复用 poi_seed（只读）。
"""
