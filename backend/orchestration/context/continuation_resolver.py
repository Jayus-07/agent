"""continuation_resolver.py — Continuation Resolver（路由入口重构 2026-09-22）

目标架构位置：
    Guard → Context Assembler → **ContinuationResolver** → Coarse Domain Router

解决的问题：多轮短指令被误杀。「改成3天」「太赶了」「换一个」这类话
没有旅游/客服强信号词，此前会被 Guard CLARIFY、被粗分类判 unknown →
澄清、或漏进 RAG 检索（实测 D3「太赶了」走到知识库拒答）。

判定规则（用户规格 §3）：
  1. 无活跃任务（active_domain 为空）→ 永不判定为延续，照常路由；
  2. 命中延续信号 + 无**其他域**的强业务信号 → 判定为上一任务延续，
     直接回 active_domain；
  3. 命中其他域的强业务信号 → 放行给正常路由（允许切域）。

纯规则零 LLM 零 IO；输入是 Context Assembler 产出的 dict，可单测穷举。
"""
from __future__ import annotations

import re

from backend.config.conversation_context import CONTINUATION_RESOLVER_ENABLED

__all__ = ["resolve_continuation", "is_continuation_query"]

# 延续信号（按语义分组；全部要求「脱离上下文不可理解」的短指令形态）
_CONTINUATION_PATTERNS: tuple[str, ...] = (
    # 改单/调参：「改成3天」「换成轻松节奏」「多排一天」
    r"改成|改为|换成|换到|再排|多排|加一天|少一天|时间改|日期改|改[个到].{0,6}天",
    # 体验反馈：「太赶了」「太贵」「节奏太快」「不想去」
    r"太赶|太累|太贵|太远|太紧|太松|太快|不赶|轻松[一]?[点些]|节奏",
    # 续聊/重生成：「继续」「换一个」「再来一个」「重新排」
    r"继续|接着|然后呢|还有呢|再来一|下一个|换一个|换一批|重新排|重新规划|重排",
    # 比较/调优：「便宜点」「更好一点」
    r"便宜[一]?[点些]|贵[一]?[点些]|再便宜|更便宜|好[一]?[点些]|再好[一]?[点些]",
    # 序数指代：「第二个」
    r"第[一二三四五]个",
)
_CONTINUATION_RE = re.compile("|".join(_CONTINUATION_PATTERNS))

# 「换一个」类在改单句式上已覆盖，这里补充裸「换」+ 量词的残留形态由
# 上面模式足够；不扩展到单字「换」，避免「换成成都」被误判（那是
# follow_up_resolver 的 overwrite 语义，两处不竞争——overwrite 判定在前）。

# 其他域强业务信号：命中即允许切域（不做延续判定）。取各域预过滤/规则
# 的同源信号件，不新增词表（与 clarify_content 同一复用纪律）。
_SQL_DATA_INTENT = re.compile(
    r"统计|查询|查一下|查下|查查|报表|导出|环比|同比|排名|多少|占比|"
    r"分析|趋势|毛利|销售额|库存|订单量|销量"
)


def is_continuation_query(query: str) -> bool:
    """query 是否携带延续信号（纯函数，供单测与调试）。"""
    q = (query or "").strip()
    if not q or len(q) > 40:
        return False
    return bool(_CONTINUATION_RE.search(q))


def resolve_continuation(query: str, routing_context: dict | None) -> dict:
    """延续判定统一入口。

    Args:
        query: 本轮用户输入（normalize 后）
        routing_context: Context Assembler 产出（active_domain 等）

    Returns:
        {
            "is_continuation": bool,
            "domain": str,        # 命中时 = active_domain
            "reason": str,        # continuation_hit | no_active_domain |
                                  # domain_switch_allowed | no_signal | disabled
            "matched": str,       # 命中的信号片段（trace 用）
        }
    """
    q = (query or "").strip()
    base = {"is_continuation": False, "domain": "", "matched": ""}
    if not CONTINUATION_RESOLVER_ENABLED:
        base["reason"] = "disabled"
        return base

    active_domain = ((routing_context or {}).get("active_domain") or "").strip()
    if not active_domain:
        base["reason"] = "no_active_domain"
        return base

    if not is_continuation_query(q):
        base["reason"] = "no_signal"
        return base

    matched = _CONTINUATION_RE.search(q).group(0)

    # 其他域强业务信号 → 允许切域，不强行拉回（用户规格 §3）
    if _has_foreign_strong_signal(q, active_domain):
        base["reason"] = "domain_switch_allowed"
        return base

    return {
        "is_continuation": True,
        "domain": active_domain,
        "matched": matched,
        "reason": "continuation_hit",
    }


def _has_foreign_strong_signal(query: str, active_domain: str) -> bool:
    """query 是否带有 active_domain 之外的其他域强信号（允许切域）。"""
    # 旅游强信号（复用预过滤判定；同域时不算「外部」信号）
    if active_domain != "travel":
        try:
            from backend.orchestration.graph.travel_prefilter import is_travel_request
            if is_travel_request(query):
                return True
        except Exception:  # noqa: BLE001 — 信号件故障按无信号处理
            pass
    # 客服强信号
    if active_domain != "customer_service":
        try:
            from backend.customer_service.router.domain_detector import cs_rule_hit_count
            if cs_rule_hit_count(query) >= 2:
                return True
        except Exception:  # noqa: BLE001
            pass
    # 选品强信号
    if active_domain != "selection_funnel":
        try:
            from backend.orchestration.graph.selection_funnel_prefilter import (
                is_selection_funnel_request,
            )
            if is_selection_funnel_request(query):
                return True
        except Exception:  # noqa: BLE001
            pass
    # 数据/SQL 强意图（活跃域本身是数据/分析类时不算外部信号）
    if active_domain not in ("data", "business", "report", "knowledge",
                             "communication"):
        if _SQL_DATA_INTENT.search(query):
            return True
    return False
