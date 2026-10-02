"""tools/search — 搜索域工具层

zhihu.py — 知乎官方 MCP 搜索（zhihu_search / global_search）：
  知乎站内经验/攻略检索 + 全网检索，数据源为 developer.zhihu.com
  官方 MCP（免费配额按期计），开关 ZHIHU_MCP_ENABLED 默认关。
"""
from backend.tools.search.zhihu import global_search_tool, zhihu_search_tool

__all__ = ["zhihu_search_tool", "global_search_tool"]
