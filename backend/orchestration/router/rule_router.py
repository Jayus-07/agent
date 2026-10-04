"""rule_router.py — Rule Router（强信号路由，2026-08-11）

设计原则：
- Rule 只判断"强信号"，不做最终决定
- confidence 高（>0.8）→ 直接返回；低（<0.8）→ 交给下层
- 不承担"业务判断"（不绑定 mode + capability）

示例：
  强信号：workflow 关键词（"每天"/"自动"/"发送"）→ workflow mode
  强信号：SQL 关键词（"多少"/"统计"/"排名"）≥3 个 → 倾向 sql.query
  强信号：业务 SOP 关键词（"审核"/"退款"）≥2 个 → rag.search（2026-09-10 起）
  弱信号：单个业务 SOP 关键词 → 不做强制（交给 embedding/LLM）
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


# ── 竞品分析强信号（竞品/比价/商品链接 → competitor.analyze）──
# 注: 需在 SQL 段之前判断，否则"竞品价格多少"会被 SQL 关键词（"价格"不在
# SQL 列表但"多少"在）抢走路由
_COMPETITOR_KEYWORDS = [
    r"竞品", r"竞对", r"比价", r"价格监控", r"价格历史", r"降价", r"涨价",
    r"item\.jd\.com", r"detail\.tmall\.com", r"item\.taobao\.com", r"amazon\.",
    r"巡检", r"监控列表",
]

# ── SQL 强信号（多少/统计/最近 → 查数据）──
# 2026-09-15 迁移：关键词组已迁入 capabilities.yaml 的 rule_keywords 字段
# （单一事实源），本列表降级为「yaml 未声明时的内置缺省表」，行为不变。
_SQL_KEYWORDS_DEFAULT = [
    r"多少", r"几个", r"统计", r"数量", r"金额", r"总和",
    r"排名", r"TOP", r"前.*?名", r"最高", r"最低",
    r"最近", r"本月", r"上月", r"今年", r"去年",
    r"环比", r"同比", r"增长率",
]


# ── 业务 SOP 弱信号（"怎么做"/"时效" → 走 RAG，不强制）──
_RAG_KEYWORDS_DEFAULT = [
    r"制度", r"规定", r"规范", r"政策", r"流程", r"标准",
    r"时效", r"SLA", r"多久", r"如何", r"怎么",
    r"是什么", r"什么叫", r"定义",
    r"审核", r"退款", r"退货", r"换货", r"售后",  # 业务流程类
]


def _load_rule_keyword_groups() -> dict[str, list[str]]:
    """从 capabilities.yaml 的 rule_keywords 字段派生关键词组。

    yaml 未声明某能力的关键词时回退内置缺省表（与迁移前行为逐条一致）；
    manifest 加载失败（理论上 fail-fast 不会走到这）同样回退。
    """
    groups: dict[str, list[str]] = {
        "sql.query": list(_SQL_KEYWORDS_DEFAULT),
        "rag.search": list(_RAG_KEYWORDS_DEFAULT),
    }
    try:
        from backend.orchestration.router.manifest import load_manifest
        declared = load_manifest().rule_keyword_groups
        for cap, kws in declared.items():
            if kws:
                groups[cap] = list(kws)
    except Exception:  # noqa: BLE001 — 回退内置表
        pass
    return groups


_RULE_KEYWORD_GROUPS = _load_rule_keyword_groups()


def sql_signal_hits(query: str) -> int:
    """查询里 sql.query 规则关键词的命中数（复用单一词表源，G2）。

    供 clarify_content 的 SQL 倾向追问判定复用——拒答兜底需要识别
    「这像是数据查询诉求」，词表只此一份，不允许第四处手抄。
    """
    if not query:
        return 0
    q = query.lower()
    return sum(1 for k in _RULE_KEYWORD_GROUPS.get("sql.query", ())
               if re.search(k, q))


def sql_lean_hits(query: str) -> int:
    """查询里「数据诉求宽词表」的命中数（复用复合意图专用词表，G2）。

    与 sql_signal_hits 的区别：rule_keywords 是路由强/弱信号词（窄，
    yaml 可覆盖）；复合意图词表是「这个问题涉不涉及数据」的宽词表
    （查询/数据/商品/订单/成本…）。拒答兜底的 SQL 倾向识别要宽不要窄
    ——实机验证（2026-10-03）：「查一下经营数据」在 rule_keywords 命中 0
    （yaml 列表无「数据」），宽词表才接得住。

    排除「多少」：纯疑问词，知识型问句（「税率是多少」）也会带；
    真实数据查询必携带数据名词（订单/销售额/库存…），不因它误判。
    """
    if not query:
        return 0
    q = query.lower()
    return sum(1 for k in _COMPOSITE_GROUP_KEYWORDS.get("sql.query", ())
               if k != "多少" and re.search(k, q))

# ── 复合意图强信号（2026-09-15，企业路由器主流做法）──────────
# 单一能力的问题由 direct 秒答；带"同时/并"等连接词、横跨 ≥2 个能力组
# 的复合问题需要多步编排，必须稳定走 plan（DAG 并行 + Reporter 汇总），
# 不能交给 Vector/LLM 层随机拍板成 direct（此前同一问题在 plan/direct
# 间波动、plan 并行难触发的根因之一）。
#
# 判定（防误杀，双条件同时满足）：
#   1. 显式复合连接词（同时/并且/以及/还要/然后再/顺便）
#   2. ≥2 个不同能力组的关键词命中
# 仅关键词共现不算（"退款审核时间是多少"是单意图 RAG 问题，不能被拆）。
_COMPOUND_CONNECTOR = re.compile(r"同时|并且|并(?![发联排集])|以及|还要|然后再|然后|顺便|接着|另外")

# 复合意图专用富词表（独立于单意图强信号列表——判定目的是"这个问题
# 涉及哪几类能力"，词表要宽；单意图路由的强/弱信号语义不受影响）
_COMPOSITE_GROUP_KEYWORDS: dict[str, list[str]] = {
    "sql.query": [
        r"查询", r"统计", r"多少", r"数量", r"库存", r"商品", r"订单",
        r"销售额", r"销量", r"金额", r"数据", r"报表", r"排名", r"项目",
        r"预算", r"成本", r"利润", r"供应商", r"客户数",
    ],
    "rag.search": [
        r"知识库", r"制度", r"规定", r"政策", r"流程", r"经验", r"文档",
        r"资料", r"规范", r"标准", r"SOP", r"说明", r"解释", r"怎么操作",
    ],
}


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

        # 1.5 竞品分析强信号（含商品链接 URL 或 明确竞品关键词 → competitor.analyze）
        comp_hits = sum(1 for k in _COMPETITOR_KEYWORDS if re.search(k, query_lower))
        has_url = bool(re.search(r"https?://", query_lower))
        if comp_hits >= 2 or (comp_hits >= 1 and has_url):
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="competitor.analyze", score=0.90)],
                confidence=0.90,
                reason=f"匹配竞品分析关键词 {comp_hits} 个（强信号）",
            )
        if comp_hits == 1:
            # 单个弱信号 → 低置信，交给 Vector/LLM 最终决定
            return RouteDecision(
                execution_mode=ExecutionMode.DIRECT,
                candidates=[CapabilityScore(name="competitor.analyze", score=0.65)],
                confidence=0.72,  # < 0.8 → fallback 到 Vector
                reason="匹配竞品分析关键词 1 个（弱信号）",
            )

        # 2. 复合意图强信号（显式连接词 + ≥2 能力组命中 → 稳定走 plan）
        if _COMPOUND_CONNECTOR.search(query_lower):
            hit_groups = [
                cap for cap, kws in _COMPOSITE_GROUP_KEYWORDS.items()
                if any(re.search(k, query_lower) for k in kws)
            ]
            if len(hit_groups) >= 2:
                return RouteDecision(
                    execution_mode=ExecutionMode.PLAN,
                    candidates=[
                        CapabilityScore(name=cap, score=0.80)
                        for cap in hit_groups
                    ],
                    confidence=0.85,
                    reason=f"复合意图（连接词 + {'/'.join(hit_groups)}）→ plan 编排",
                )

        # 2.5 其他 yaml 声明组（2026-09-22 D6 修复，如 data.collect）：
        # 采集/抓取/同步/导入类特征词与 SQL 统计词正交，先于 RAG/SQL 判定，
        # 防止「导入供应商商品信息」这类外部数据诉求被统计词误吸进 sql.query。
        # 只消费 capabilities.yaml rule_keyword_groups 里显式声明的组
        # （sql.query/rag.search 保留下方专属块，行为不变），不扩写内置词表。
        _DEDICATED_GROUPS = ("sql.query", "rag.search")
        for cap, kws in _RULE_KEYWORD_GROUPS.items():
            if cap in _DEDICATED_GROUPS or not kws:
                continue
            cap_hits = sum(1 for k in kws if re.search(k, query_lower))
            if cap_hits >= 2:
                return RouteDecision(
                    execution_mode=ExecutionMode.DIRECT,
                    candidates=[CapabilityScore(name=cap, score=0.85)],
                    confidence=0.85,
                    reason=f"匹配 {cap} 关键词 {cap_hits} 个（强信号）",
                )
            if cap_hits == 1:
                return RouteDecision(
                    execution_mode=ExecutionMode.DIRECT,
                    candidates=[CapabilityScore(name=cap, score=0.75 + 0.05 * cap_hits)],
                    confidence=0.75,  # < 0.8 → fallback 到 Vector
                    reason=f"匹配 {cap} 关键词 1 个（弱信号）",
                )

        # 3. 业务 SOP 关键字（审核/退款/流程等）
        # 2026-09-10：2 个命中即视为强信号直接拍板（原阈值 3）。
        # 依据：trace c8431b548b01 —— 「退款审核时间是多少？」命中 2 个 RAG 关键词，
        # 规则层给出候选 rag.search(0.70)，随后 Vector miss，LLM 花 8.4s 得出
        # 与规则完全相同的结论。RAG 误路由代价低（Evidence Gate 保护，无证据即拒答），
        # 而 2 个业务 SOP 关键词（如「退款」+「审核」「制度」+「规范」）已足够强。
        rag_hits = sum(1 for k in _RULE_KEYWORD_GROUPS["rag.search"] if re.search(k, query_lower))
        if rag_hits >= 2:
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
        sql_hits = sum(1 for k in _RULE_KEYWORD_GROUPS["sql.query"] if re.search(k, query_lower))
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
