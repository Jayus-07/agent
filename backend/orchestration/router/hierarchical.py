"""hierarchical.py — RoutingEngine 使用的域内能力解析器。

三层职责收敛（分层路由改造 2026-09-22，用户规格 §2/§14）：
  CoarseIntentClassifier   「属于哪个业务域？」   —— 本文件第一级
  Domain Tool Registry     「该域有哪些候选 Tool？」 —— capability_by_domain 派生视图
  FineToolRouter           「域内哪个 Tool？」      —— Fast Path / 交 tool_selector LLM
  tool_selector（Qwen3-8B）「灰区最终选 Tool + 填参」 —— 复用既有 FC 节点，仅见域内候选

规则和向量只负责提供证据，最终执行方式由 RoutingEngine 统一决定；
确定性最强的两块规则作为 domain_override：
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
    FINE_TOOL_HIGH_CONFIDENCE,
    FINE_TOOL_MIN_MARGIN,
)
from backend.orchestration.router.domain_classifier import (
    DomainPrediction,
    get_coarse_classifier,
)
from backend.orchestration.router.manifest import load_manifest
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouter
from backend.orchestration.router.vector_router import VectorRouteError
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
    fine_top2: str = ""
    fine_top2_score: float = 0.0
    fine_margin: float = 0.0
    candidate_scores: dict[str, float] = Field(default_factory=dict)
    score_type: str = "vector_similarity_heuristic"
    top1_risk_level: str = "UNKNOWN"
    fast_path_block_reason: str = ""
    need_clarification: bool = False
    clarification_reason: str = ""
    # 规则特征校准明细（2026-09-22 D6 修复，score_calibration.py）：
    # hits=各候选特征命中数，strong=强信号候选，basis=直通判定依据。
    # 空 dict = 本次未触发校准（无 opt-in 能力或零命中）。
    calibration: dict = Field(default_factory=dict)
    fallback_reason: str = ""


class _FineScoreMap(dict[str, float]):
    """兼容旧 dict 接口，同时携带向量基础设施故障码。"""

    def __init__(self, *args, failure_reason: str = "", score_type: str = "unknown", **kwargs):
        super().__init__(*args, **kwargs)
        self.failure_reason = failure_reason
        self.score_type = score_type


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

    def __init__(self, vector_router: VectorRouter | None = None):
        self.rule = RuleRouter()  # 仅承担 workflow / 复合意图两类确定性 override
        self.classifier = get_coarse_classifier()
        self.vector = vector_router or VectorRouter()

    # ── 细路由第一层：确定性 / 语义 Fast Path ──────────────────
    def _fine_scores(self, query: str, candidates: list[ToolCandidate]) -> dict[str, float]:
        """域内能力分数：复用既有向量路由索引（capability examples）。

        一次 pgvector 检索取全量分数（top_k 放大到域候选上限），按域内
        候选过滤 —— 与统一 VectorRouter 共用同一索引和语义空间，无新设施。
        """
        valid_names = {c.name for c in candidates}
        try:
            decision = self.vector.route(query, top_k=8)
            # TD-17（2026-10-03）：同一 capability 有多条 examples 时向量
            # 检索返回多行——dict 推导会被后行覆盖，capability 实际得分
            # 变成"最后一条 example"的分（实测"发票认证时限"0.640 的
            # top1 分被第 7 条"商品定价调价"0.443 覆盖，fine_top1 翻转到
            # web.search）。同名取最优分（与 route 的 top1 语义一致）。
            scores: dict[str, float] = {}
            for c in decision.candidates:
                if c.name in valid_names and c.score > scores.get(c.name, 0.0):
                    scores[c.name] = c.score
            score_type = str((decision.routing_meta or {}).get("score_type") or "vector_similarity_heuristic")
        except VectorRouteError as exc:
            return _FineScoreMap(
                {c.name: 0.3 for c in candidates},
                failure_reason=exc.code,
                score_type="unavailable_fallback_score",
            )
        except Exception as exc:
            scores = {}
            score_type = "vector_query_failed_fallback_score"
            failure_reason = f"vector_query_failed:{type(exc).__name__}"
        else:
            failure_reason = ""
        # 未进 top-K 的候选给保底分（保持候选完整，交给 FC/灰区路径）
        return _FineScoreMap(
            {c.name: scores.get(c.name, 0.3) for c in candidates},
            failure_reason=failure_reason,
            score_type=score_type,
        )

    def select_tool(self, query: str, domain: str,
                    candidates: list[ToolCandidate]) -> ToolSelection:
        """细路由：Fast Path 三条件（top1/margin/risk+白名单）→ 灰区交 LLM。

        _fine_scores 返回向量相似度或启发式调整后的相似度分数。规则特征
        只用于解释，不单独授权 Fast Path；Fast Path 必须同时满足分数、
        margin、LOW 风险与 fast_path_enabled 门槛。
        """
        from backend.orchestration.router.score_calibration import (
            STRONG_SIGNAL_HITS,
            compute_signal,
        )

        if not candidates:
            return ToolSelection(need_clarification=True, clarification_reason="domain_has_no_tools")

        scores = self._fine_scores(query, candidates)
        vector_failure_reason = getattr(scores, "failure_reason", "")
        ranked = sorted(candidates, key=lambda c: (-scores.get(c.name, 0.0), c.name))

        # 规则特征只作为附加证据记录，不替代向量分数、top1/top2 margin
        # 或风险门禁，也不单独授权 Fast Path。
        hits = compute_signal(query, [c.name for c in candidates])
        strong = sorted(c for c, h in hits.items() if h >= STRONG_SIGNAL_HITS)
        calib_meta: dict = {"hits": hits, "strong": strong} if hits else {}

        top1, top2 = ranked[0], ranked[1] if len(ranked) > 1 else None
        s1 = scores.get(top1.name, 0.0)
        s2 = scores.get(top2.name, 0.0) if top2 else 0.0
        margin = s1 - s2

        selection = ToolSelection(
            tool_name=top1.name, tool_confidence=round(s1, 3),
            candidate_tools=[c.name for c in ranked],
            fine_top1=top1.name, fine_top1_score=round(s1, 3),
            fine_top2=top2.name if top2 else "",
            fine_top2_score=round(s2, 3),
            fine_margin=round(margin, 3),
            candidate_scores={c.name: round(float(scores.get(c.name, 0.0)), 3)
                              for c in ranked},
            score_type=str(getattr(scores, "score_type", "vector_similarity_heuristic")),
            top1_risk_level=top1.risk_level,
            fallback_reason=vector_failure_reason,
        )

        fast_ok = (
            s1 >= FINE_TOOL_HIGH_CONFIDENCE
            and margin >= FINE_TOOL_MIN_MARGIN
            and top1.fast_path_enabled
            and top1.risk_level == "LOW"
        )
        if fast_ok:
            selection.route_mode = "fast_path"
            if calib_meta:
                calib_meta["basis"] = "score_and_margin_gate"
                selection.calibration = calib_meta
        else:
            # 灰区：候选原样交给 tool_selector（bind 的只有域内工具）；
            # 不在此处调 LLM —— LLM 调用统一收敛在 tool_selector 节点。
            selection.route_mode = "llm_selection"
            if s1 < FINE_TOOL_HIGH_CONFIDENCE:
                selection.fast_path_block_reason = "top1_score_below_threshold"
            elif margin < FINE_TOOL_MIN_MARGIN:
                selection.fast_path_block_reason = "margin_below_threshold"
            elif not top1.fast_path_enabled:
                selection.fast_path_block_reason = "fast_path_disabled"
            elif top1.risk_level != "LOW":
                selection.fast_path_block_reason = "risk_level_not_low"
            else:
                selection.fast_path_block_reason = "fast_path_policy_rejected"
            if calib_meta:
                calib_meta["basis"] = "grey_zone"
                selection.calibration = calib_meta
        return selection

    # ── 主流程 ─────────────────────────────────────────────────
    def route(self, query: str, context: dict | None = None) -> RouteDecision:
        """分层路由入口。返回带 routing_meta 的 RouteDecision。

        域图类域（CS/travel/selection）返回 plan 占位决策 + domain_action，
        由 router_node 复用既有 prefilter；prefilter 未命中时 router_node
        交回统一 RoutingEngine（灰度 control 组语义保持）。
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
            # 该类直接调用主要用于离线评测；线上由 RoutingEngine 捕获同类
            # 状态并调用统一 LLM fallback，不能在这里抛出未分类异常。
            return self._assemble(
                RouteDecision(
                    execution_mode=ExecutionMode.PLAN,
                    candidates=[],
                    confidence=0.0,
                    reason=f"coarse classifier degraded: {prediction.reason_code}",
                ),
                _meta(prediction, "clarify", None, None, t0),
            )

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
            return self._assemble(
                RouteDecision(
                    execution_mode=ExecutionMode.PLAN, candidates=[],
                    confidence=prediction.confidence,
                    reason=f"coarse unknown（{prediction.reason_code}）→ 澄清",
                ),
                _meta(prediction, "clarify", None, None, t0),
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

        ordered = [CapabilityScore(name=n, score=selection.candidate_scores.get(n, 0.0))
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
            "domain_score_type": p.score_type,
            "reason_code": p.reason_code,
            "domain_action": action,
            "candidate_tools": selection.candidate_tools if selection else [],
            "candidate_tool_count": len(selection.candidate_tools) if selection else 0,
            "fine_top1": selection.fine_top1 if selection else "",
            "fine_top1_score": selection.fine_top1_score if selection else 0.0,
            "fine_margin": selection.fine_margin if selection else 0.0,
            "tool_route_mode": selection.route_mode if selection else "",
            "selection_mode": selection.route_mode if selection else "",
            "calibration": (selection.calibration if selection else {}),
            "selected_tool": (selection.tool_name if selection.route_mode == "fast_path" else "") if selection else "",
            "fine_top2": selection.fine_top2 if selection else "",
            "fine_top2_score": selection.fine_top2_score if selection else 0.0,
            "candidate_scores": selection.candidate_scores if selection else {},
            "score_type": selection.score_type if selection else "",
            "fallback_reason": selection.fallback_reason if selection else "",
            "risk_level": selection.top1_risk_level if selection else "",
            "fast_path_block_reason": selection.fast_path_block_reason if selection else "",
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
