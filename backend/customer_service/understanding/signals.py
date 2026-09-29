"""signals.py — 情绪 / 紧迫度 / 风险信号（规则层）。

规划稿 §七：情绪只影响语气和转人工优先级，不直接触发业务写操作。
风险基线取自 INTENT_PROFILES，命中升级信号只升不降。
"""
from __future__ import annotations

import re

from backend.customer_service.understanding.types import Sentiment, Urgency

# 情绪/紧迫/风险标记已收敛至 vocab.py 单一事实源（迁移 B8，值逐字
# 搬移）；本模块保留检测逻辑，标记从 vocab import。
from backend.customer_service.vocab import (  # noqa: E402
    ANGRY_MARKERS as _ANGRY_MARKERS,
    DISSATISFIED_MARKERS as _DISSATISFIED_MARKERS,
    P0_ESCALATION_MARKERS as _P0_ESCALATION_MARKERS,
    RISK_MARKERS as _RISK_MARKERS,
    URGENT_MARKERS as _URGENT_MARKERS,
)

P0_ESCALATION_MARKERS = _P0_ESCALATION_MARKERS  # 对外名字保持（B5 契约）

_ANGRY_PUNCT = re.compile(r"[!！?？]{2,}")


def detect_sentiment(normalized_text: str) -> tuple[Sentiment, list[str]]:
    """返回 (情绪, 命中的信号名)。"""
    text = normalized_text or ""
    hits: list[str] = []
    for w in _ANGRY_MARKERS:
        if w in text:
            hits.append(f"angry:{w}")
    if _ANGRY_PUNCT.search(text):
        hits.append("angry:punct")
    if hits:
        return Sentiment.ANGRY, hits
    for w in _DISSATISFIED_MARKERS:
        if w in text:
            hits.append(f"dissatisfied:{w}")
    if hits:
        return Sentiment.DISSATISFIED, hits
    return Sentiment.CALM, []


def detect_urgency(normalized_text: str) -> tuple[Urgency, list[str]]:
    text = normalized_text or ""
    hits = [f"urgent:{w}" for w in _URGENT_MARKERS if w in text]
    if hits:
        return Urgency.HIGH, hits
    return Urgency.NORMAL, []


def detect_risk_hits(normalized_text: str) -> list[str]:
    """越权/注入信号（供风险升级与 Trace 观测；拦截归 Input Guard）。"""
    text = normalized_text or ""
    return [f"risk:{w}" for w in _RISK_MARKERS if w in text]


def is_p0_escalation(sentiment_hits: list[str]) -> bool:
    """情绪信号命中 P0 升级子集（hits 形如 "angry:12315"）。"""
    for h in sentiment_hits or []:
        if h.split(":", 1)[-1] in _P0_ESCALATION_MARKERS:
            return True
    return False


def escalate(base_risk: str, hits: list[str]) -> tuple[str, bool]:
    """风险只升不降：命中越权/注入信号 → high。返回 (risk, escalated)。"""
    if hits:
        return "high", True
    return base_risk, False
