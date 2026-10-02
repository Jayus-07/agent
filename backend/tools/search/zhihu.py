"""tools/search/zhihu.py — 知乎/全网搜索（外部 MCP 数据源：知乎官方）

数据源形态：developer.zhihu.com 官方 MCP（Streamable HTTP，Bearer 鉴权），
平台经 ``infra/mcp_client.py`` 消费其 ``zhihu_search`` 与 ``global_search``
两个上游工具。与 12306（非官方、仅供学习）不同，这是**官方开放平台**：
有免费配额（搜索类各 5000 次/期，超额需购买），故本地 TTL 缓存默认 300s、
按源独立节流（ZHIHU_MCP_MIN_INTERVAL），开关 ``ZHIHU_MCP_ENABLED`` 默认关，
Access Secret 只从 .env 读（ZHIHU_MCP_API_KEY），不落代码。

与既有 web_search_tool（DuckDuckGo/Bing 爬虫兜底）的关系：
  本模块是官方 API 数据源，覆盖知乎站内内容（爬虫拿不到）与全网检索；
  爬虫版是存量例外（E8），二者数据源与失败语义不同，不互为兜底。

上游返回形态全部来自 2026-10-02 实测（解析规则勿凭文档改）：
  成功     ``{"code": 0, "message": "success", "data": {"has_more": bool,
            "item_count": N, "items": [...]}}``
  items 键（zhihu_search）：title/url/content_type/content_id/author_name/
            author_avatar/author_badge/author_badge_text/author_signature/summary
  items 键（global_search）更宽：另含 authority_level/comment_count/
            comment_info_list/content_image/edit_time/ranking_score/
            vote_up_count，且 content_type 可为空串
  code != 0 → 上游业务失败（配额尽/无权限等），message 可读

归一化只保留模型消费有用的字段（丢 avatar/badge/图片与排序内部分），
summary 截断——搜索结果整包进 prompt，截断是防上下文爆炸的硬闸。
「查不到」（code=0 且 items 空）是确定答案走成功封套；code!=0 与基础设施
失败（McpClientError）走失败封套，绝不混同。
"""
from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from backend.config import mcp as MCP_CFG
from backend.infra.mcp_client import McpClientError, call_tool
from backend.shared.logger import logger
from backend.shared.tool_envelope import tool_error_result, tool_success_result

# 上游工具名（MCP server 侧定义，勿改）；单次条数上限来自平台文档口径
# （站内搜索最多 10 条、全网搜索最多 20 条），超上限请求按上限截断
_UPSTREAM_ZHIHU = "zhihu_search"
_UPSTREAM_GLOBAL = "global_search"
_UPSTREAM_MAX_COUNT = {_UPSTREAM_ZHIHU: 10, _UPSTREAM_GLOBAL: 20}

# summary 进 prompt 前的硬截断（字符）
_SUMMARY_MAX_CHARS = 500

# items 归一化保留键：核心溯源 + 作者信号 + 互动量（有则透传，无则不造）
_KEEP_ITEM_KEYS = ("title", "url", "content_type", "author_name",
                   "author_signature", "vote_up_count", "comment_count",
                   "edit_time")


def _normalize_items(items: list, limit: int) -> list[dict]:
    """裁键 + summary 截断 + 条数截断，字段缺失不补造。"""
    normalized = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        entry = {k: item[k] for k in _KEEP_ITEM_KEYS if item.get(k) not in (None, "")}
        summary = item.get("summary")
        if summary:
            entry["summary"] = str(summary)[:_SUMMARY_MAX_CHARS]
        normalized.append(entry)
    return normalized


def _search(upstream_tool: str, query: str, count: int) -> str:
    """zhihu_search / global_search 共用执行体（形态同构，见模块头）。"""
    if not MCP_CFG.is_zhihu_mcp_enabled():
        return tool_error_result(
            "知乎搜索未启用",
            hint="请在 .env 设置 ZHIHU_MCP_ENABLED=true 与 ZHIHU_MCP_API_KEY"
                 "（developer.zhihu.com 个人中心生成 Access Secret）",
        )
    query = (query or "").strip()
    if not query:
        return tool_error_result("query 不能为空")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
        return tool_error_result("count 必须是 1 到 20 的整数")
    # 超上游上限的请求静默收敛到上限（上游 400 会浪费一次配额）
    count = min(count, _UPSTREAM_MAX_COUNT[upstream_tool])

    try:
        payload = call_tool(
            MCP_CFG.ZHIHU_MCP_BASE_URL, upstream_tool,
            {"query": query, "count": count},
            timeout=MCP_CFG.ZHIHU_MCP_TIMEOUT,
            ttl=MCP_CFG.ZHIHU_MCP_CACHE_TTL,
            headers={"Authorization": f"Bearer {MCP_CFG.ZHIHU_MCP_API_KEY}"},
            min_interval=MCP_CFG.ZHIHU_MCP_MIN_INTERVAL,
        )
    except McpClientError as e:
        logger.warning("[ZhihuSearchTool] 上游调用失败: %s", e)
        return tool_error_result(
            f"搜索失败（知乎 MCP 服务不可用）：{e}",
            hint="可稍后重试；需要站外网页结果时可回退 web_search_tool",
        )
    except Exception as e:  # noqa: BLE001 — Tool 边界统一兜底
        logger.warning("[ZhihuSearchTool] 未预期异常: %s", e)
        return tool_error_result(f"搜索异常: {e}")

    if not isinstance(payload, dict) or "code" not in payload:
        return tool_error_result("知乎搜索返回了无法识别的结构",
                                 payload=str(payload)[:300])
    if payload.get("code") != 0:
        # 上游业务失败（配额尽/无权限/参数拒）：失败封套，保留可读原因
        return tool_error_result(
            f"知乎搜索上游报错：{payload.get('message') or payload.get('code')}",
            hint="配额用尽时需等待按期重置或在开放平台购买资源包",
        )

    data = payload.get("data") or {}
    items = data.get("items")
    if not isinstance(items, list):
        return tool_error_result("知乎搜索返回了无法识别的结果结构",
                                 payload=str(payload)[:300])

    queried_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return tool_success_result({
        "query": query,
        "count": len(items),
        "total_matched": data.get("item_count"),
        "results": _normalize_items(items, count),
        "source": "zhihu_mcp" if upstream_tool == _UPSTREAM_ZHIHU else "zhihu_mcp_global",
        "queried_at": queried_at,
    })


@tool
def zhihu_search_tool(query: str, count: int = 5) -> str:
    """
    搜索知乎站内内容（经验帖、观点、攻略、问答），返回标题、链接、作者与摘要。
    query: 搜索关键词，如 "泉州 美食 推荐"
    count: 返回条数，1-10，默认 5
    适用场景：需要真实用户经验/观点对比/旅游攻略/「怎么选」类中文内容时。
    注意：数据来自知乎官方开放平台，配额有限（按期计），同问题短窗内会命中
    缓存；拿不到时效性新闻，新闻类请用 global_search_tool。
    """
    return _search(_UPSTREAM_ZHIHU, query, count)


@tool
def global_search_tool(query: str, count: int = 5) -> str:
    """
    全网搜索（网页、新闻、媒体文章），返回标题、链接与内容摘要。
    query: 搜索关键词，如 "泉州 十大美食 旅游线路"
    count: 返回条数，1-20，默认 5
    适用场景：需要时效性信息（新闻、近期活动、官方公告）或站外网页结果时。
    注意：数据来自知乎官方开放平台的全网检索，配额有限（按期计）；查知乎
    站内经验帖优先用 zhihu_search_tool（内容质量更高）。
    """
    return _search(_UPSTREAM_GLOBAL, query, count)


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(zhihu_search_tool, __file__)
tool_registry.register(global_search_tool, __file__)
