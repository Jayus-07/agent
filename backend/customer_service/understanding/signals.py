"""signals.py — 情绪 / 紧迫度 / 风险信号（规则层）。

规划稿 §七：情绪只影响语气和转人工优先级，不直接触发业务写操作。
风险基线取自 INTENT_PROFILES，命中升级信号只升不降。
"""
from __future__ import annotations

import re

from backend.customer_service.understanding.types import Sentiment, Urgency

_ANGRY_MARKERS = (
    "骗子", "欺诈", "垃圾", "气死", "忍无可忍", "曝光", "报警",
    "12315", "受骗", "胡说", "恶心", "无耻",
)
_DISSATISFIED_MARKERS = (
    "不满意", "太慢", "拖了", "敷衍", "第四次", "又", "再也不", "受不了",
    "没人管", "没人处理",
)
_URGENT_MARKERS = (
    "立刻", "马上", "尽快", "现在就", "紧急", "今天必须", "等着用",
)
# 跨用户/越权探测与工具滥用信号（只升风险，不做拦截——拦截是 Input Guard 职责）
_RISK_MARKERS = (
    "别人的订单", "他人的订单", "他的订单", "她的订单", "所有用户的",
    "全部用户", "别人的手机号", "后台取消", "直接改数据库", "忽略之前的指令",
    "忽略以上", "打印你的系统提示词", "开发者模式",
)
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


# P0 升级子集（监管/舆情信号，设计方案 §4.6 P0 档）：_ANGRY_MARKERS 的高危
# 子集，命中即 Supervisor 直通投诉专家（迁移 B5 消费）。只做既有词条的
# 子集选择、不新增词条——词表扩充归词表单一源治理（迁移 B8）。
P0_ESCALATION_MARKERS = ("12315", "曝光", "报警")


def is_p0_escalation(sentiment_hits: list[str]) -> bool:
    """情绪信号命中 P0 升级子集（hits 形如 "angry:12315"）。"""
    for h in sentiment_hits or []:
        if h.split(":", 1)[-1] in P0_ESCALATION_MARKERS:
            return True
    return False


def escalate(base_risk: str, hits: list[str]) -> tuple[str, bool]:
    """风险只升不降：命中越权/注入信号 → high。返回 (risk, escalated)。"""
    if hits:
        return "high", True
    return base_risk, False
