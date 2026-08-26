"""tools/competitor.py — 竞品分析 Tool（LangChain）

入口动作:
  analyze  — 分析指定 URL（默认；question 里带 URL 也走这里）
  watch    — 巡检全部监控项
  history  — 查看某 URL 的价格历史
  add      — 加入监控列表
  remove   — 从监控列表移除
  toggle   — 启用/停用监控项
  list     — 查看监控列表
"""
import re

from langchain_core.tools import tool

from backend.competitor.adapters import detect_platform
from backend.competitor.pipeline import analyze_url, history_report, scan_watchlist
from backend.competitor.store import get_store
from backend.shared.logger import logger

_URL_RE = re.compile(r"https?://[^\s)）\]】<>\"']+")


def _extract_url(question: str) -> str:
    """从自然语言里抠出第一个 URL"""
    m = _URL_RE.search(question or "")
    return m.group(0).rstrip(".,;，。；") if m else ""


def _format_watchlist() -> str:
    items = get_store().list_watch(enabled_only=False)
    if not items:
        return "监控列表为空。"
    lines = ["## 竞品监控列表", "", "| # | 名称 | 平台 | 频率 | 状态 | URL |", "|---|---|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        status = "启用" if it["enabled"] else "停用"
        lines.append(f"| {i} | {it['name']} | {it['platform']} | {it['frequency']} | {status} | {it['url'][:60]} |")
    return "\n".join(lines)


@tool
def competitor_analyze_tool(action: str = "analyze", url: str = "",
                            name: str = "", question: str = "",
                            enabled: bool = True) -> str:
    """
    竞品分析：抓取竞品商品页/官网页，抽取价格、促销、评价等结构化信息，
    存为快照并与历史对比（变价提醒）。支持监控列表管理与价格历史。

    action: analyze（分析 URL，默认）| watch（巡检全部监控项）| history（价格历史）
            | add（加入监控）| remove（移除监控）| toggle（启用/停用）| list（查看监控列表）
    url: 竞品页面完整 URL（action=analyze/history/add/remove/toggle 时需要）
    name: 竞品名称（action=add 时可选，便于阅读）
    question: 用户原始问题（其中的 URL 会被自动提取）
    enabled: toggle 时是否启用（默认 True）
    返回: Markdown 格式的分析结果
    """
    # question 里带 URL 时自动切换到 analyze
    target_url = url or _extract_url(question)
    if not url and question and target_url and action == "analyze":
        url = target_url

    try:
        if action == "analyze":
            if not url:
                return ("请提供竞品页面 URL（如 item.jd.com 商品页、竞品官网产品页），"
                        "或先通过 competitor.list 查看已监控的竞品。")
            return analyze_url(url, name=name)

        if action == "watch":
            return scan_watchlist()

        if action == "history":
            if not url:
                return "请提供要查价格历史的竞品 URL。"
            return history_report(url)

        if action == "add":
            if not url:
                return "请提供要监控的竞品 URL。"
            store = get_store()
            watch = store.add_watch(
                name=name or url[:50], url=url, platform=detect_platform(url)
            )
            # 立即抓一次，建立基线快照
            first = analyze_url(url, name=watch["name"])
            return f"已加入监控: {watch['name']}\n\n{first}"

        if action == "list":
            return _format_watchlist()

        if action == "remove":
            if not url:
                return "请提供要移除监控的竞品 URL。"
            store = get_store()
            removed = store.remove_watch(url)
            if removed:
                return f"已从监控列表移除: {url}"
            return f"监控列表中未找到: {url}"

        if action == "toggle":
            if not url:
                return "请提供要启用/停用的竞品 URL。"
            store = get_store()
            watch = store.toggle_watch(url, enabled=enabled)
            if not watch:
                return f"监控列表中未找到: {url}"
            status = "启用" if watch["enabled"] else "停用"
            return f"已{status}监控: {watch['name']} ({url})"

        return f"未知 action: {action}（支持 analyze / watch / history / add / remove / toggle / list）"

    except Exception as e:
        logger.warning(f"[Tool:competitor] 失败: {e}")
        return f"[COMPETITOR FAILED] 竞品分析失败: {e}"
