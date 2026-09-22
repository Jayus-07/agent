"""hierarchical.py — 分层路由 HierarchicalRouter（粗分类 → 域内细选择）

三层职责收敛（分层路由改造 2026-09-22，用户规格 §2/§14）：
  CoarseIntentClassifier   「属于哪个业务域？」   —— 本文件第一级
  Domain Tool Registry     「该域有哪些候选 Tool？」 —— capability_by_domain 派生视图
  FineToolRouter           「域内哪个 Tool？」      —— Fast Path / 交 tool_selector LLM
  tool_selector（Qwen3-8B）「灰区最终选 Tool + 填参」 —— 复用既有 FC 节点，仅见域内候选

废弃的重复路由：hierarchical 模式下 legacy 三层 Router 的 vector/LLM 层
不再承担「直接选 capability」职责（QueryRouter LLM fallback 不再触发）；
确定性最强的两块规则保留为 domain_override：
  - workflow 强信号（每天跑日报…）→ 既有 workflow 路径
  - 复合意图（连接词 + ≥2 能力组）→ 既有 plan 编排路径
  这两类规则语义是「执行方式」而非「选哪个工具」，与粗分类不竞争。

风险控制（§11）：HIGH 风险能力永不进 Fast Path（manifest 声明 +
本模块双保险）；副作用执行另有 Tool 层审批门（security/tool_approval）
与 CS 确认状态机（域图内，未触碰）。
"""
from __future__ import annotations

import time

from pydantic import BaseModel, Field

from backend.config import (
    COARSE_UNKNOWN_ACTION,
    FINE_TOOL_HIGH_CONFIDENCE,
    FINE_TOOL_MIN_MARGIN,
)
from backend.orchestration.router.domain_classifier import (
    DomainPrediction,
    get_coarse_classifier,
)
from backend.orchestration.router.manifest import load_manifest
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)

__all__ = ["ToolCandidate", "ToolSelection", "HierarchicalRouter", "get_hierarchical_router"]

# 域图类粗域 → router_node 里复用的既有 prefilter 动作（不新增路由实现）
_DOMAIN_ACTIONS = {
    "customer_service": "prefilter_cs",
    "travel": "prefilter_travel",
    "selection_funnel": "prefilter_selection",
}


class ToolCandidate(BaseModel):
    """域内候选工具（Domain Tool Registry 输出）。"""
    name: str
    score: float = 0.0
    risk_level: str = "LOW"
    fast_path_enabled: bool = True


class ToolSelection(BaseModel):
    """细路由决策结果（结构化，供 state / trace / metrics 消费）。"""
    tool_name: str = ""
    tool_confidence: float = 0.0
    route_mode: str = Field("", description="fast_path | llm_selection |")
    candidate_tools: list[str] = Field(default_factory=list)
    fine_top1: str = ""
    fine_top1_score: float = 0.0
    fine_margin: float = 0.0
    need_clarification: bool = False
    clarification_reason: str = ""
    # 规则特征校准明细（2026-09-22 D6 修复，score_calibration.py）：
    # hits=各候选特征命中数，strong=强信号候选，basis=直通判定依据。
    # 空 dict = 本次未触发校准（无 opt-in 能力或零命中）。
    calibration: dict = Field(default_factory=dict)


def resolve_domain_tools(domain: str) -> list[ToolCandidate]:
    """Domain Tool Registry：粗域 → 已注册的域内候选能力（含风险元数据）。

    单一事实源是 capabilities.yaml（capabilities_by_domain 派生视图），
    再与 Skill 注册表对账（未注册的能力不进候选，防御半注册状态）。
    """
    from backend.orchestration.capability_registry import tool_registry

    manifest = load_manifest()
    decls = {c.name: c for c in manifest.capabilities}
    candidates: list[ToolCandidate] = []
    for name in manifest.capabilities_by_domain.get(domain, ()):
        decl = decls.get(name)
        # 只放行 routed 能力：routed:false 是内部能力（如 competitor.watch
        # 长轮询副作用），用户问题路由不可见，FC 也绝不能 bind 到它们
        if decl is None or not decl.routed:
            continue
        if tool_registry.get_node(name) is None:
            continue  # yaml 声明了但 Skill 未注册 → 不进候选（fail-safe）
        candidates.append(
            ToolCandidate(
                name=name, risk_level=decl.risk_level,
                fast_path_enabled=decl.fast_path_enabled,
            )
        )
    return candidates


class HierarchicalRouter:
    """分层路由主流程（ coarse → resolve → fine → 组装 RouteDecision ）。"""

    def __init__(self):
        self.rule = RuleRouter()  # 仅承担 workflow / 复合意图两类确定性 override
        self.classifier = get_coarse_classifier()

    # ── 细路由第一层：确定性 / 语义 Fast Path ──────────────────
    def _fine_scores(self, query: str, candidates: list[ToolCandidate]) -> dict[str, float]:
        """域内能力分数：复用既有向量路由索引（capability examples）。

        一次 pgvector 检索取全量分数（top_k 放大到域候选上限），按域内
        候选过滤 —— 与 legacy 向量路由同一索引同一语义空间，无新设施。
        """
        from backend.orchestration.router.router import get_router

        valid_names = {c.name for c in candidates}
        try:
            decision = get_router().vector.route(query, top_k=8)
            scores = {
                c.name: c.score for c in decision.candidates if c.name in valid_names
            }
        except Exception:
            scores = {}
        # 未进 top-K 的候选给保底分（保持候选完整，交给 FC/灰区路径）
        return {c.name: scores.get(c.name, 0.3) for c in candidates}

    def select_tool(self, query: str, domain: str,
                    candidates: list[ToolCandidate]) -> ToolSelection:
        """细路由：Fast Path 三条件（top1/margin/risk+白名单）→ 灰区交 LLM。

        2026-09-22 D6 修复：_fine_scores 返回的已是校准后分数（校准在
        VectorRouter.route 单点生效）；此外唯一规则强信号候选（≥2 个特征
        命中）直通 —— 这是「高置信场景直通」的规则化表达，不是把某工具
        固定为最高优先级：直通资格随 query 命中的特征数变化，且仍受
        LOW 风险 + fast_path_enabled 双门槛约束（§11）。
        """
        from backend.orchestration.router.score_calibration import (
            STRONG_SIGNAL_HITS,
            compute_signal,
        )

        if not candidates:
            return ToolSelection(need_clarification=True, clarification_reason="domain_has_no_tools")

        scores = self._fine_scores(query, candidates)
        ranked = sorted(candidates, key=lambda c: (-scores.get(c.name, 0.0), c.name))

        # 规则强信号：唯一 ≥2 特征命中的候选（如有）提到首位——向量召回被
        # 语义搭便车的 examples 拉偏时（D6），特征信号兜住确定性。
        hits = compute_signal(query, [c.name for c in candidates])
        strong = sorted(c for c, h in hits.items() if h >= STRONG_SIGNAL_HITS)
        calib_meta: dict = {"hits": hits, "strong": strong} if hits else {}
        if len(strong) == 1 and ranked[0].name != strong[0]:
            ranked.sort(key=lambda c: c.name != strong[0])  # stable：强信号候选置顶

        top1, top2 = ranked[0], ranked[1] if len(ranked) > 1 else None
        s1 = scores.get(top1.name, 0.0)
        s2 = scores.get(top2.name, 0.0) if top2 else 0.0
        margin = s1 - s2

        selection = ToolSelection(
            tool_name=top1.name, tool_confidence=round(s1, 3),
            candidate_tools=[c.name for c in ranked],
            fine_top1=top1.name, fine_top1_score=round(s1, 3),
            fine_margin=round(margin, 3),
        )

        fast_ok = (
            s1 >= FINE_TOOL_HIGH_CONFIDENCE
            and margin >= FINE_TOOL_MIN_MARGIN
            and top1.fast_path_enabled
            and top1.risk_level == "LOW"
        )
        rule_signal_ok = (
            len(strong) == 1
            and top1.name == strong[0]
            and top1.fast_path_enabled
            and top1.risk_level == "LOW"
        )
        if fast_ok or rule_signal_ok:
            selection.route_mode = "fast_path"
            if calib_meta:
                calib_meta["basis"] = "rule_strong_signal" if rule_signal_ok else "calibrated_scores"
                selection.calibration = calib_meta
        else:
            # 灰区：候选原样交给 tool_selector（bind 的只有域内工具）；
            # 不在此处调 LLM —— LLM 调用统一收敛在 tool_selector 节点。
            selection.route_mode = "llm_selection"
            if calib_meta:
                calib_meta["basis"] = "grey_zone"
                selection.calibration = calib_meta
        return selection

    # ── 主流程 ─────────────────────────────────────────────────
    def route(self, query: str, context: dict | None = None) -> RouteDecision:
        """分层路由入口。返回带 routing_meta 的 RouteDecision。

        域图类域（CS/travel/selection）返回 plan 占位决策 + domain_action，
        由 router_node 复用既有 prefilter；prefilter 未命中时 router_node
        回退 legacy 路由（灰度 control 组语义保持）。
        """
        from backend.shared.logger import logger

        t0 = time.perf_counter()

        # 1. 确定性 override：workflow 强信号 / 复合意图 → 既有路径
        rule = self.rule.route(query)
        if rule is not None and rule.execution_mode == ExecutionMode.WORKFLOW:
            return self._assemble(rule, _meta("rule", "rule_workflow", None, None, t0))
        if (
            rule is not None
            and rule.execution_mode == ExecutionMode.PLAN
            and rule.confidence >= 0.8
            and len(rule.candidates) >= 2
        ):
            # 复合意图（「查库存并根据制度判断」）必须多步编排，不拆单一工具
            return self._assemble(rule, _meta("rule", "rule_composite", None, None, t0))

        # 2. 粗分类
        prediction = self.classifier.classify(query, context)

        # 3. 域分派
        if prediction.source == "degraded":
            # embedding 不可用 → 抛给 Router 回退 legacy（不阻塞请求）
            raise RuntimeError(f"coarse classifier degraded: {prediction.reason_code}")

        if prediction.domain in _DOMAIN_ACTIONS:
            decision = RouteDecision(
                execution_mode=ExecutionMode.PLAN,
                candidates=[], confidence=prediction.confidence,
                reason=f"coarse domain={prediction.domain} → 既有域图 prefilter",
            )
            return self._assemble(decision, _meta(
                prediction, _DOMAIN_ACTIONS[prediction.domain], None, None, t0))

        if prediction.domain == "general":
            # 寒暄/无需工具（路由入口重构 2026-09-22）：general_chat 主 LLM
            # 直答，不再进 plan 支线（planner/critique/supervisor 对寒暄是
            # 纯浪费，RAG 拒答话术对寒暄是噪声）
            decision = RouteDecision(
                execution_mode=ExecutionMode.PLAN, candidates=[],
                confidence=prediction.confidence,
                reason="coarse domain=general → general_chat 直答",
            )
            return self._assemble(decision, _meta(prediction, "general_chat", None, None, t0))

        if prediction.domain == "unknown":
            if COARSE_UNKNOWN_ACTION == "clarify":
                return self._assemble(
                    RouteDecision(
                        execution_mode=ExecutionMode.PLAN, candidates=[],
                        confidence=prediction.confidence,
                        reason=f"coarse unknown（{prediction.reason_code}）→ 澄清",
                    ),
                    _meta(prediction, "clarify", None, None, t0),
                )
            # legacy 处置：交回旧 plan 路径
            return self._assemble(
                RouteDecision(
                    execution_mode=ExecutionMode.PLAN, candidates=[],
                    confidence=prediction.confidence,
                    reason="coarse unknown → legacy plan 支线",
                ),
                _meta(prediction, "plan", None, None, t0),
            )

        # 4. 工具域：resolve domain tools → fine route
        candidates = resolve_domain_tools(prediction.domain)
        if not candidates:
            logger.warning(f"[Hierarchical] 域 {prediction.domain} 无已注册候选，回退 plan")
            return self._assemble(
                RouteDecision(
                    execution_mode=ExecutionMode.PLAN, candidates=[],
                    confidence=prediction.confidence,
                    reason=f"domain {prediction.domain} 无候选 → plan",
                ),
                _meta(prediction, "plan", None, None, t0),
            )

        selection = self.select_tool(query, prediction.domain, candidates)
        if selection.need_clarification:
            return self._assemble(
                RouteDecision(
                    execution_mode=ExecutionMode.PLAN, candidates=[],
                    confidence=prediction.confidence,
                    reason="域内无候选 → 澄清",
                ),
                _meta(prediction, "clarify", selection, None, t0),
            )

        ordered = [CapabilityScore(name=n, score=selection.tool_confidence if n == selection.fine_top1 else 0.3)
                   for n in selection.candidate_tools]
        decision = RouteDecision(
            execution_mode=ExecutionMode.DIRECT,
            candidates=ordered,
            confidence=selection.tool_confidence,
            reason=(
                f"domain={prediction.domain} fine_top1={selection.fine_top1} "
                f"({selection.fine_top1_score:.2f}, margin={selection.fine_margin:.2f}) "
                f"→ {selection.route_mode}"
            ),
        )
        return self._assemble(decision, _meta(prediction, "tool_route", selection, None, t0))

    def _assemble(self, decision: RouteDecision, meta: dict) -> RouteDecision:
        decision.routing_meta = meta
        return decision

    # ── Shadow mode（§20 双轨评估）────────────────────────────
    def shadow_compare(self, query: str, legacy: RouteDecision) -> dict | None:
        """legacy 拍板执行的同时，计算 hierarchical 结果并对比。

        只做粗分类 + 域内向量细选（零 LLM，零额外模型成本）；
        记录 legacy_tool / hierarchical_tool / is_match 供真实流量评估。
        """
        try:
            prediction = self.classifier.classify(query)
            hierarchical_tool = ""
            if prediction.domain not in _DOMAIN_ACTIONS and prediction.domain not in (
                "general", "unknown",
            ):
                candidates = resolve_domain_tools(prediction.domain)
                if candidates:
                    selection = self.select_tool(query, prediction.domain, candidates)
                    hierarchical_tool = selection.fine_top1
            legacy_tool = legacy.candidates[0].name if legacy.candidates else ""
            return {
                "legacy_tool": legacy_tool,
                "legacy_mode": legacy.execution_mode.value,
                "hierarchical_domain": prediction.domain,
                "hierarchical_confidence": prediction.confidence,
                "hierarchical_tool": hierarchical_tool,
                "is_match": bool(legacy_tool) and legacy_tool == hierarchical_tool,
            }
        except Exception:
            return None


def _meta(source_or_prediction, action: str,
          selection: ToolSelection | None, decision_hint: str | None,
          t0: float) -> dict:
    """组装 routing_meta（统一结构，router_node 据此写 state 平铺字段）。"""
    if isinstance(source_or_prediction, DomainPrediction):
        p = source_or_prediction
        meta = {
            "architecture": "hierarchical",
            "domain": p.domain,
            "domain_confidence": p.confidence,
            "domain_margin": p.margin,
            "domain_source": p.source,
            "reason_code": p.reason_code,
            "domain_action": action,
            "candidate_tools": selection.candidate_tools if selection else [],
            "candidate_tool_count": len(selection.candidate_tools) if selection else 0,
            "fine_top1": selection.fine_top1 if selection else "",
            "fine_top1_score": selection.fine_top1_score if selection else 0.0,
            "fine_margin": selection.fine_margin if selection else 0.0,
            "tool_route_mode": selection.route_mode if selection else "",
            "calibration": (selection.calibration if selection else {}),
            "selected_tool": (selection.tool_name if selection.route_mode == "fast_path" else "") if selection else "",
            "need_clarification": selection.need_clarification if selection else (action == "clarify"),
            "clarification_reason": selection.clarification_reason if selection else (
                "unknown_domain" if action == "clarify" else ""),
            "routing_latency_ms": int((time.perf_counter() - t0) * 1000),
        }
        return meta
    # rule override（workflow / 复合意图）
    return {
        "architecture": "hierarchical",
        "domain": "", "domain_confidence": 0.0, "domain_margin": 0.0,
        "domain_source": source_or_prediction,
        "reason_code": source_or_prediction.upper(),
        "domain_action": action,
        "candidate_tools": [], "candidate_tool_count": 0,
        "fine_top1": "", "fine_top1_score": 0.0, "fine_margin": 0.0,
        "tool_route_mode": "", "selected_tool": "",
        "need_clarification": False, "clarification_reason": "",
        "routing_latency_ms": int((time.perf_counter() - t0) * 1000),
    }


# ── 模块级单例 ────────────────────────────────────────────────
_hierarchical_router: HierarchicalRouter | None = None


def get_hierarchical_router() -> HierarchicalRouter:
    global _hierarchical_router
    if _hierarchical_router is None:
        _hierarchical_router = HierarchicalRouter()
    return _hierarchical_router
