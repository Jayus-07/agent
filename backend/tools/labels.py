"""tools/labels.py — Tool 中文名标签（单一事实源，G2）

Tool 的 name（@tool 函数名）是机器身份：契约 lock 键、Prometheus 指标
label、trace 下钻键、MCP 出口名全用它，永不改；本表提供「人看的名字」。

- 生成器（scripts/gen_tool_contract_lock.py）把 display_name 派生进
  tool_contracts.lock.json（不参与 content_hash——中文标签不影响模型
  看到的契约），管理端 /tools 页与 /api/admin/tools/errors 消费。
- 守护：tests/tool_runtime/test_tool_stats_alignment.py 断言本表键集
  与 tool_registry 注册表完全一致（多键/少键都 fail-fast）。
- 新增 Tool 必须同步在此登记中文名，否则 lock 生成出空串。
"""
from __future__ import annotations

TOOL_DISPLAY_NAMES: dict[str, str] = {
    # ── 通用 ──
    "calculate_tool": "数学计算",
    # ── SQL / 数据 ──
    "execute_sql_tool": "SQL 只读查询",
    "sql_query_tool": "自然语言查库",
    "export_csv_tool": "CSV 导出",
    "generate_report_tool": "报告生成",
    "data_collection_tool": "数据采集入库",
    # ── RAG / 记忆 ──
    "search_knowledge_tool": "知识库检索",
    "memory_search_tool": "记忆检索",
    "memory_store_tool": "记忆写入",
    # ── 竞品 ──
    "competitor_analyze_tool": "竞品分析",
    "competitor_history_tool": "竞品价格历史",
    "competitor_watch_tool": "竞品巡检",
    "competitor_watchlist_tool": "竞品监控清单管理",
    # ── 地图（腾讯 LBS）──
    "map_geocode_tool": "地理编码",
    "map_reverse_geocode_tool": "逆地理编码",
    "map_ip_location_tool": "IP 定位",
    "map_district_tool": "行政区划查询",
    "map_place_search_tool": "地点搜索",
    "map_place_suggest_tool": "地点输入联想",
    "map_lookup_tool": "地图聚合查询",
    "map_merchant_search_tool": "商家搜索",
    "map_distance_matrix_tool": "多点距离矩阵",
    "map_route_tool": "路线规划",
    "map_navigation_tool": "导航调起链接",
    "map_static_map_tool": "静态地图",
    "map_street_view_tool": "街景查询",
    "map_coord_convert_tool": "坐标转换",
    "map_weather_tool": "天气查询",
    # ── 旅游 ──
    "travel_poi_search_tool": "景点搜索",
    "travel_train_search_tool": "火车票余票查询",
    "travel_train_price_tool": "火车票票价查询",
    # ── 搜索（知乎官方 MCP）──
    "zhihu_search_tool": "知乎搜索",
    "global_search_tool": "全网搜索",
    # ── 邮件 ──
    "send_email_tool": "邮件发送",
    "read_email_tool": "邮件读取",
    "search_email_tool": "邮件搜索",
    "watch_email_tool": "邮件监听",
    # ── Web ──
    "web_search_tool": "网页搜索",
    "web_crawl_tool": "网页抓取",
}


def get_display_name(tool_name: str) -> str:
    """查中文名；未登记返回空串（lock 生成与管理端按空值降级显示原名）。"""
    return TOOL_DISPLAY_NAMES.get(tool_name, "")
