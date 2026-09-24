"""backend/travel/commerce — Travel Commerce & Inventory（STOP K）

生产只读闭环（K0 定稿）：Hotel/Flight 搜索 → 归一化 → Availability 语义 →
Price Snapshot → 确定性排序 → 安全 Deep Link。LLM 零参与事实链；
Booking Create 属未来 Transaction STOP（providers/travel/booking.py 预留，
本包不触碰）。

冻结边界（STOP I/J）：本包不 import travel 域图内部（graph_builder/validator/
reporter/supervisor），不写 Itinerary/Poi/TravelBrief；与行程域唯一交点是
城市表复用 poi_seed（只读）。
"""
