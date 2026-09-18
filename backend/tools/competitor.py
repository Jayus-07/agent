"""tools/competitor.py — 竞品分析 Tools（LangChain，按职责拆分）

单职责拆分（原 7-action 巨型工具，action 参数承载全部分支）:
  competitor_analyze_tool    — 分析单个竞品页面（capability: competitor.analyze）
  competitor_watch_tool      — 巡检全部监控项（capability: competitor.watch）
  competitor_history_tool    — 查某 URL 的价格历史（capability: competitor.history）
  competitor_watchlist_tool  — 监控列表管理 list/add/remove/toggle（写操作需审批）

Skill 层按 capability/params 分发到对应 Tool（CompetitorAnalysisSkill._select_tool）。
analyze/watch 虽写快照库，但属缓存量级副作用且是分析主路径，不阻断审批
（快照仅追加不修改业务状态）；监控列表增删改属业务状态变更，需审批。
"""
import re

from langchain_core.tools import tool

from backend.competitor.adapters import detect_platform
from backend.competitor.pipeline import analyze_url, history_report, scan_watchlist
from backend.competitor.store import get_store
from backend.shared.logger import logger

_URL_RE = re.compile(r"https?://[^\s)）\]】<>\"']+")

# 需人工审批的写动作（增删改监控列表）
_WRITE_ACTIONS = ("add", "remove", "toggle")


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


def _run_idempotent_competitor_operation(
    operation: str,
    payload: dict,
    callback,
    *,
    client_key: str = "",
) -> str:
    """对会写入快照或监控状态的竞品动作加全局幂等边界。"""
    from backend.tools.session import (
        get_tool_idempotency_key,
        get_tool_tenant_id,
    )

    if not get_tool_tenant_id():
        # 兼容尚未经网关注入租户的旧直调/本地开发路径；有可信租户时不降级。
        return str(callback())

    from backend.shared.idempotency import run_idempotent_operation

    result = run_idempotent_operation(
        operation,
        payload,
        lambda: {"message": callback()},
        client_key=client_key or get_tool_idempotency_key(),
    )
    return str(result["message"])


@tool
def competitor_analyze_tool(url: str = "", name: str = "", question: str = "",
                            idempotency_key: str = "") -> str:
    """
    竞品分析：抓取竞品商品页/官网页，抽取价格、促销、评价等结构化信息，
    存为快照并与历史对比（变价提醒）。

    url: 竞品页面完整 URL
    name: 竞品名称（可选，便于阅读）
    question: 用户原始问题（其中的 URL 会被自动提取）
    返回: Markdown 格式的分析结果
    """
    target_url = url or _extract_url(question)
    if not target_url:
        return ("请提供竞品页面 URL（如 item.jd.com 商品页、竞品官网产品页），"
                "或先通过监控列表查看已监控的竞品。")
    try:
        return _run_idempotent_competitor_operation(
            "competitor.analyze",
            {"url": target_url, "name": name},
            lambda: analyze_url(target_url, name=name),
            client_key=idempotency_key,
        )
    except Exception as e:
        # 上抛给 BaseSkill：抓取/抽取失败可重试，吞掉会绕过 Skill 层重试机制
        logger.warning(f"[Tool:competitor-analyze] 失败：{e}")
        raise


@tool
def competitor_watch_tool(idempotency_key: str = "") -> str:
    """
    竞品巡检：巡检监控列表中的全部竞品，汇报价格变动。

    返回: Markdown 格式的巡检报告
    """
    try:
        return _run_idempotent_competitor_operation(
            "competitor.watch",
            {"scope": "enabled_watchlist"},
            scan_watchlist,
            client_key=idempotency_key,
        )
    except Exception as e:
        logger.warning(f"[Tool:competitor-watch] 失败：{e}")
        raise


@tool
def competitor_history_tool(url: str = "", question: str = "") -> str:
    """
    竞品价格历史：查询某竞品 URL 的历史价格走势。

    url: 竞品页面完整 URL
    question: 用户原始问题（其中的 URL 会被自动提取）
    返回: Markdown 格式的价格历史报告
    """
    target_url = url or _extract_url(question)
    if not target_url:
        return "请提供要查价格历史的竞品 URL。"
    try:
        return history_report(target_url)
    except Exception as e:
        logger.warning(f"[Tool:competitor-history] 失败：{e}")
        raise


@tool
def competitor_watchlist_tool(action: str = "list", url: str = "",
                              name: str = "", enabled: bool = True,
                              idempotency_key: str = "") -> str:
    """
    竞品监控列表管理：查看、加入、移除、启用/停用监控项。
    注意：add/remove/toggle 属写操作，首次执行需管理员审批。

    action: list（查看监控列表，默认）| add（加入监控）| remove（移除监控）
            | toggle（启用/停用）
    url: 竞品页面完整 URL（add/remove/toggle 时需要）
    name: 竞品名称（add 时可选，便于阅读）
    enabled: toggle 时是否启用（默认 True）
    返回: Markdown 格式的操作结果
    """
    if action not in ("list", "add", "remove", "toggle"):
        return f"未知 action: {action}（支持 list / add / remove / toggle）"

    if action == "list":
        return _format_watchlist()

    if action in _WRITE_ACTIONS:
        from backend.security.tool_approval import ensure_approved
        from backend.tools.session import get_tool_user_id
        pending = ensure_approved(
            "competitor_watchlist", action,
            user_id=get_tool_user_id(),
            detail={"action": action, "url": url, "name": name,
                    "enabled": enabled},
        )
        if pending is not None:
            return pending

    try:
        payload = {
            "action": action,
            "url": url,
            "name": name,
            "enabled": enabled,
        }
        return _run_idempotent_competitor_operation(
            "competitor.watchlist",
            payload,
            lambda: _watchlist_after_approval(action, url, name, enabled),
            client_key=idempotency_key,
        )

    except Exception as e:
        # 上抛给 BaseSkill：抓取/存储失败可重试，吞掉会绕过 Skill 层重试机制
        logger.warning(f"[Tool:competitor-watchlist] 失败：{e}")
        raise


def _watchlist_after_approval(
    action: str, url: str, name: str, enabled: bool,
) -> str:
    """审批通过且幂等 claim 成功后的监控列表写操作。"""
    if action == "list":
        return _format_watchlist()

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

    if action == "remove":
        if not url:
            return "请提供要移除监控的竞品 URL。"
        store = get_store()
        removed = store.remove_watch(url)
        if removed:
            return f"已从监控列表移除: {url}"
        return f"监控列表中未找到: {url}"

    # toggle
    if not url:
        return "请提供要启用/停用的竞品 URL。"
    store = get_store()
    watch = store.toggle_watch(url, enabled=enabled)
    if not watch:
        return f"监控列表中未找到: {url}"
    status = "启用" if watch["enabled"] else "停用"
    return f"已{status}监控: {watch['name']} ({url})"


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(competitor_analyze_tool, __file__)
tool_registry.register(competitor_watch_tool, __file__)
tool_registry.register(competitor_history_tool, __file__)
tool_registry.register(competitor_watchlist_tool, __file__)
