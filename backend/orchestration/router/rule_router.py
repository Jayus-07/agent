"""rule_router.py — Rule Router（强信号路由，2026-08-11）

设计原则：
- Rule 只判断"强信号"，不做最终决定
- confidence 高（>0.8）→ 直接返回；低（<0.8）→ 交给下层
- 不承担"业务判断"（不绑定 mode + capability）

示例：
  强信号：workflow 关键词（"每天"/"自动"/"发送"）→ workflow mode
  强信号：SQL 关键词（"多少"/"统计"/"排名"）→ 倾向 sql.query
  弱信号：业务 SOP 关键词（"审核"/"流程"）→ 不做强制（交给 embedding）
"""
from __future__ import annotations

import re
from typing import List

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


# ── Workflow 强信号（每天/自动/发送 → 跑工作流）──
_WORKFLOW_KEYWORDS = [
    r"每天", r"每日", r"定时", r"自动", r"生成日报", r"生成周报",
    r"发邮件", r"发送邮件", r"推送", r"跑一下", r"触发",
    r"检查库存风险", r"自动提醒", r"自动采购",
]
_WORKFLOW_PATTERNS = {
    r"每天.*?跑": "daily_report",
    r"每日.*?跑": "daily_report",
    r"每周.*?跑": "daily_report",
    r"自动.*?检查": "inventory_alert",
    r"自动.*?提醒": "inventory_alert",
    r"自动.*?采购": "inventory_alert",
    r"生成.*?日报": "daily_report",
    r"生成.*?周报": "daily_report",
}


# ── SQL 强信号（多少/统计/最近 → 查数据）──
_SQL_KEYWORDS = [
    r"多少", r"几个", r"统计", r"数量", r"金额", r"总和",
    r"排名", r"TOP", r"前.*?名", r"最高", r"最低",
    r"最近", r"本月", r"上月", r"今年", r"去年",
    r"环比", r"同比", r"增长率",
]


# ── 业务 SOP 弱信号（"怎么做"/"时效" → 走 RAG，不强制）──
_RAG_KEYWORDS = [
    r"制度", r"规定", r"规范", r"政策", r"流程", r"标准",
    r"时效", r"SLA", r"多久", r"如何", r"怎么",
    r"是什么", r"什么叫", r"定义",
    r"审核", r"退款", r"退货", r"换货", r"售后",  # 业务流程类
]


class RuleRouter:
    """Rule Router：基于关键词强信号快速路由（0 成本，~1ms）。"""

    def route(self, query: str) -> RouteDecision | None:
        """返回 RouteDecision 或 None（None 表示交给下层 Router）。

        Returns:
            RouteDecision: 强信号命中（confidence 高）
            None: 弱信号或无信号，交给下层
        """
        query_lower = query.lower()

        # 1. Workflow 强信号（最高优先级）
        for pattern, wf in _WORKFLOW_PATTERNS.items():
            if re.search(pattern, query_lower):
                return RouteDecision(
                    execution_mode=ExecutionMode.WORKFLOW,
                    candidates=[CapabilityScore(name=wf, score=0.95)],
                    confidence=0.95,
                    workflow_name=wf,
                    reason=f"匹配 workflow 模式: {pattern}",
                )

        if any(re.search(k, query_lower) for k in _WORKFLOW_KEYWORDS):
            return RouteDecision(
                execution_mode=ExecutionMode.WORKFLOW,
                candidates=[CapabilityScore(name="daily_report", score=0.85)],
                confidence=0.85,
                workflow_name="daily_report",
                reason="匹配 workflow 强关键词",
            )

        # 2. 业务 SOP 关键字（审核/退款/流程等 — 弱信号，给 Vector/LLM 做 hint）
        rag_hits = sum(1 for k in _RAG_KEYWORDS if re.search(k, query_lower))
        if rag_hits >= 3:
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="rag.search", score=0.85)],
                confidence=0.85,
                reason=f"匹配 RAG 关键词 {rag_hits} 个（强信号）",
            )
        if rag_hits >= 1:
            # 1-2 个 RAG 命中 → 低置信度，交给 Vector/LLM 最终决定
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="rag.search", score=0.55 + 0.05 * rag_hits)],
                confidence=0.70,  # < 0.8 → fallback 到 Vector
                reason=f"匹配 RAG 关键词 {rag_hits} 个（弱信号）",
            )

        # 3. SQL 关键字（同样：信号越强置信越高）
        sql_hits = sum(1 for k in _SQL_KEYWORDS if re.search(k, query_lower))
        if sql_hits >= 3:
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="sql.query", score=0.90)],
                confidence=0.90,
                reason=f"匹配 SQL 关键词 {sql_hits} 个（强信号）",
            )
        if sql_hits >= 1:
            # 1-2 个 SQL 命中 → 低置信，交给 Vector/LLM
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="sql.query", score=0.75 + 0.05 * sql_hits)],
                confidence=0.75,  # < 0.8 → fallback 到 Vector
                reason=f"匹配 SQL 关键词 {sql_hits} 个（弱信号）",
            )

        # 无信号 → 交给下层（embedding / LLM）
        return None
