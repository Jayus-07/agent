"""customer_service/router/coarse_router.py — 客服粗分类路由

2 层 fallback（2026-09-17 删除 Chroma 向量层，全站存储收口 pgvector；
种子语义路由随 512 维轨一并移除）:
  1. Rule: CS_DOMAIN_KEYWORDS 关键词计数 → 直接决定
  2. Hint: DomainDetector 预检的域命中作为弱兜底
  3. UNKNOWN: 无匹配（交由上层 LLM 兜底）
"""
from __future__ import annotations

import re

from backend.customer_service.router.types import CSDomain


class CSCoarseRouter:
    """客服域粗分类 — 输出 CSDomain。"""

    def classify(self, query: str, rule_hint: CSDomain | None = None) -> tuple[CSDomain, float, str]:
        """粗分类入口。

        Returns:
            (domain, confidence, reason)
        """
        from backend.config.customer_service import (
            CS_CONFIDENCE_CAUTIOUS,
            CS_DOMAIN_KEYWORDS,
        )

        # 售后词本身不等于办理动作：政策、范围、条件、费用等知识问句
        # 必须进入知识域，避免客服知识金标被 ActionExpert 误拦成补订单号。
        # 仅在完整默认双域词表存在时启用，保持单域/测试定制词表的既有语义。
        if {
            "KNOWLEDGE", "AFTER_SALES"
        }.issubset(CS_DOMAIN_KEYWORDS) and self._is_after_sales_policy_question(query):
            return CSDomain.KNOWLEDGE, CS_CONFIDENCE_CAUTIOUS, "policy_question: KNOWLEDGE"

        domain, conf, reason = self._rule_classify(query, CS_DOMAIN_KEYWORDS)
        # P2.1（audit #157）：规则决定线 0.8 → CS_CONFIDENCE_CAUTIOUS（0.6）。
        # 向量层删除后规则通道是唯一判定，规则命中即采信。
        if conf > 0:
            return domain, conf, f"rule: {reason}"

        if rule_hint and rule_hint != CSDomain.UNKNOWN:
            return rule_hint, 0.5, f"hint_fallback: {rule_hint.value}"

        return CSDomain.UNKNOWN, 0.0, "no_match"

    def _rule_classify(
        self,
        query: str,
        keywords: dict,
        patterns: dict | None = None,
    ) -> tuple[CSDomain, float, str]:
        best_domain, best_count = CSDomain.UNKNOWN, 0
        if patterns is None:
            # 正则模式与关键词同权计票（P3.5 实测修复）：CS_DOMAIN_PATTERNS
            # 此前定义了却从未参与打分，"申请退款"这类单关键词问法全部
            # 落 UNKNOWN → 域锁兜底 0.5+0.6=0.55 打穿 Supervisor 0.6 闸门
            from backend.config.customer_service import CS_DOMAIN_PATTERNS
            patterns = CS_DOMAIN_PATTERNS
        for domain, kws in keywords.items():
            count = sum(1 for kw in kws if kw in query)
            count += sum(
                1 for p in patterns.get(domain, []) if p.search(query)
            )
            if count > best_count:
                best_domain, best_count = CSDomain(domain), count

        if best_count >= 2:
            conf = min(best_count / 3.0, 1.0)
            return best_domain, conf, f"{best_domain.value}({best_count}hits)"
        return CSDomain.UNKNOWN, 0.0, ""

    @staticmethod
    def _is_after_sales_policy_question(query: str) -> bool:
        """识别售后词驱动的知识问句，排除显式副作用动作。"""
        if not any(token in query for token in ("退款", "退货", "换货", "售后", "维修", "保修")):
            return False
        if re.search(
            r"(申请|提交|办理|我要|帮我|给我|退这个|换一个|怎么(退|换|修)|"
            r"送修|返修|报修)",
            query,
        ):
            return False
        return bool(re.search(
            r"(政策|规则|范围|条件|标准|时效|多久|几天|什么时候|包括|"
            r"流程|费用|承担|赔付|是什么|怎样|怎么填|能不能|可以吗)",
            query,
        ))
