"""tools/labels.py — Tool 展示元数据（单一事实源，G2）

Tool 的 name（@tool 函数名）是机器身份：契约 lock 键、Prometheus 指标
label、trace 下钻键、MCP 出口名全用它，永不改；本表提供「人看的名字」
与「数据源归属」两类展示元数据。

- 生成器（scripts/gen_tool_contract_lock.py）把两者派生进
  tool_contracts.lock.json（均不参与 content_hash——展示元数据不影响
  模型看到的契约），管理端 /tools 页与 /api/admin/tools/errors 消费。
- 守护：tests/tool_runtime/test_tool_stats_alignment.py 断言两张表键集
  与 tool_registry 注册表完全一致（多键/少键都 fail-fast）。
- 新增 Tool 必须同步在此登记，否则 lock 生成出空串 / null。
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
    "memory_forget_tool": "记忆删除",
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


# 数据源归属（2026-10-02）：治理页识别「这个 Tool 的数据从哪来、上游是谁」。
# type 三档：
#   mcp      — 外部 MCP server 数据源（必须带 upstream_tool 与 switch_env，
#              上游不可达时的降级语义见各 Tool 模块 docstring）
#   rest     — 外部网络 API（HTTP / SMTP/IMAP，必须带 provider）
#   internal — 平台内部数据（本库 / RAG 索引 / 内存计算 / 内部管道）
# 可选 quota（额度声明，三种成熟度对应三种诚实口径）：
#   period        — day=按日 | period=按期 | qps=速率限制
#   limit         — 静态声明额度值（外部平台知识，如知乎 5000/期）
#   limit_env     — 上限由 env 软预算配置（如 TRAVEL_PROVIDER_*_DAILY_BUDGET）
#   usage_provider— 用量读数走 providers.travel.live.quota.current_usage(provider)
#   usage_counter — 用量读数走月键 tool_quota:{usage_counter}:{YYYYMM}
#                   （field = upstream_tool，只计真实上游调用）
#   note          — 诚实口径说明（作用域/被动性），详情页原文透出
# 未登记由守护测试 fail-fast 提醒补齐；lock 生成出 null，管理端显示降级。

# 额度声明共享常量（provider 级事实，多个 Tool 共用同一来源）
_ZHIHU_QUOTA = {
    "period": "period",
    "limit": 5000,
    "usage_counter": "zhihu_mcp",
    "note": "开放平台免费额度按期计（搜索类各 5000 次/期），超额需购买资源包；"
            "月键只计真实上游调用，缓存命中不计",
}
_TENCENT_LBS_QUOTA = {
    "period": "day",
    "limit_env": "TRAVEL_PROVIDER_TENCENT_DAILY_BUDGET",
    "usage_provider": "tencent:lbs",
    "note": "腾讯LBS 日软预算闸门只装在旅游域 live 适配器上，本工具直连 HTTP 层"
            "不经闸门（用量仅供参考）；个人 Key 约 5 QPS",
}
_AMAP_QUOTA = {
    "period": "day",
    "note": "上游当日限量（额度按 Key 档位），超限返回错误码 10004/10009；"
            "平台不计数，触发即知",
}

TOOL_DATA_SOURCES: dict[str, dict[str, str]] = {
    # ── MCP（外部 MCP server 数据源）──
    "travel_train_search_tool": {
        "type": "mcp", "provider": "12306",
        "upstream_tool": "query-tickets", "switch_env": "TRAIN_MCP_ENABLED",
    },
    "travel_train_price_tool": {
        "type": "mcp", "provider": "12306",
        "upstream_tool": "query-ticket-price", "switch_env": "TRAIN_MCP_ENABLED",
    },
    "zhihu_search_tool": {
        "type": "mcp", "provider": "知乎",
        "upstream_tool": "zhihu_search", "switch_env": "ZHIHU_MCP_ENABLED",
        "quota": _ZHIHU_QUOTA,
    },
    "global_search_tool": {
        "type": "mcp", "provider": "知乎",
        "upstream_tool": "global_search", "switch_env": "ZHIHU_MCP_ENABLED",
        "quota": _ZHIHU_QUOTA,
    },
    # ── REST（外部网络 API）──
    "map_geocode_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_reverse_geocode_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_ip_location_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_district_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_place_search_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_place_suggest_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_lookup_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_distance_matrix_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_route_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_navigation_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_static_map_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_street_view_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_coord_convert_tool": {"type": "rest", "provider": "腾讯LBS", "quota": _TENCENT_LBS_QUOTA},
    "map_weather_tool": {"type": "rest", "provider": "腾讯LBS+和风", "quota": _TENCENT_LBS_QUOTA},
    "map_merchant_search_tool": {"type": "rest", "provider": "高德", "quota": _AMAP_QUOTA},
    "send_email_tool": {"type": "rest", "provider": "SMTP/Agently"},
    "read_email_tool": {"type": "rest", "provider": "IMAP/Agently"},
    "search_email_tool": {"type": "rest", "provider": "IMAP/Agently"},
    "watch_email_tool": {"type": "rest", "provider": "IMAP/Agently"},
    "web_search_tool": {"type": "rest", "provider": "外部搜索"},
    "web_crawl_tool": {"type": "rest", "provider": "目标站点"},
    # ── internal（平台内部数据）──
    "calculate_tool": {"type": "internal"},
    "sql_query_tool": {"type": "internal"},
    "execute_sql_tool": {"type": "internal"},
    "export_csv_tool": {"type": "internal"},
    "generate_report_tool": {"type": "internal"},
    "data_collection_tool": {"type": "internal"},
    "search_knowledge_tool": {"type": "internal"},
    "memory_search_tool": {"type": "internal"},
    "memory_store_tool": {"type": "internal"},
    "memory_forget_tool": {"type": "internal"},
    "competitor_analyze_tool": {"type": "internal"},
    "competitor_history_tool": {"type": "internal"},
    "competitor_watch_tool": {"type": "internal"},
    "competitor_watchlist_tool": {"type": "internal"},
    "travel_poi_search_tool": {"type": "internal"},
}


def get_display_name(tool_name: str) -> str:
    """查中文名；未登记返回空串（lock 生成与管理端按空值降级显示原名）。"""
    return TOOL_DISPLAY_NAMES.get(tool_name, "")


def get_display_name(tool_name: str) -> str:
    """查中文名；未登记返回空串（lock 生成与管理端按空值降级显示原名）。"""
    return TOOL_DISPLAY_NAMES.get(tool_name, "")


def get_data_source(tool_name: str) -> dict[str, str] | None:
    """查数据源归属；未登记返回 None（守护测试 fail-fast 提醒补齐）。"""
    return TOOL_DATA_SOURCES.get(tool_name)
