"""selection_funnel_prefilter.py — Router 内的选品域预过滤（与 travel_prefilter 同层）

纯函数判定「是不是智能选品漏斗请求」，命中且域开关开启时短路进
selection_funnel 域图。**刻意排除选品决策类措辞** —— 「选品决策 /
值不值得做 / 能不能上」属 selection_decision workflow，两边不能抢路由。
"""
from __future__ import annotations

import re

from backend.shared.logger import logger

# 选品漏斗强信号
_FUNNEL_PATTERNS: tuple[str, ...] = (
    r"智能选品", r"选品", r"选款", r"挑款", r"上新款", r"卖什么好",
    r"选品报告", r"候选.{0,4}筛", r"测款",
)

# 决策类措辞让给 selection_decision workflow（宁可漏判，不可抢路由）
_DECISION_EXCLUDE = re.compile(r"决策|值不值得|能不能上|该不该做|评审团")

# 「XX 品类/类目」是类目运营语境，单独出现不足以判选品，只作弱信号
_RE_CATEGORY_HINT = re.compile(r"(品类|类目)")


def is_selection_funnel_request(query: str) -> bool:
    """是否为智能选品（漏斗）请求（纯函数，可单测）。"""
    if not query:
        return False
    if _DECISION_EXCLUDE.search(query):
        return False
    from backend.config.selection_funnel import SELECTION_FUNNEL_DETECT_MIN_HITS
    hits = sum(1 for p in _FUNNEL_PATTERNS if re.search(p, query))
    if hits >= max(1, SELECTION_FUNNEL_DETECT_MIN_HITS):
        return True
    # 弱组合：「品类 + 筛/推荐/看看」类口语
    return bool(_RE_CATEGORY_HINT.search(query)
                and re.search(r"筛|推荐|看看|挑", query))


def try_selection_funnel_prefilter(query: str, state: dict) -> dict | None:
    """选品域预过滤。

    Returns:
        命中且域开启 → 主图 state 更新 dict（route_mode="selection_funnel"）；
        未命中 / 域关闭 / 异常 → None（继续走主 Router）。
    """
    try:
        from backend.config.selection_funnel import SELECTION_FUNNEL_ENABLED
        if not SELECTION_FUNNEL_ENABLED:
            return None
    except Exception:
        return None

    if not is_selection_funnel_request(query):
        return None

    logger.info("[SelectionFunnelPrefilter] 选品域命中: query=%s...", query[:60])
    return {
        "route_decision": None,
        "route_mode": "selection_funnel",
        "funnel_context": {
            "conversation_id": state.get("session_id", ""),
            "source": "prefilter",
        },
    }
