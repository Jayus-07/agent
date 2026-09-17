"""Travel Runner — 对接旅游域子图（backend.travel.graph_builder）。

按任务书 §15 的 A-F 六组断言规划质量（组别见 datasets/travel/manifest.json）：
- 调 get_travel_graph().invoke()（全规则图，无需 LLM → needs_live=False，离线可跑）
- 每条 case 全新 thread_id；跨轮组（metadata.same_thread）同 thread 连发两轮，
  第二轮临时切 memory checkpointer（跑完恢复默认图配置）
- Failure 组经 Provider 失败注入：TransitProvider 走 routing.set_route_provider
  运行时插槽（Phase 1 Protocol 红利）；POI 检索走 tools.travel.poi.search_poi
  模块级插桩（POI 尚无运行时插槽）。两者 finally 无条件恢复。
- 断言契约字段：status / destination / days / party_size / pace /
  min_plan_version / parent_linked / repair_triggered / decision_required /
  no_error_violations / violation_codes / confidence_max / must_contain /
  must_not_contain / notes_contain / notes_not_contain / clarification
"""
import time
import uuid

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner


# =============================================
# 假 Provider（Failure 组注入）
# =============================================
def _failing_transit_estimate(*args, **kwargs):
    """实现 TransitProvider 协议的 estimate 形态，但恒抛错——
    图内 estimate_leg 会捕获并回落本地直线估算（设计内降级）。"""
    raise RuntimeError("[EvalFailure] 注入的假 TransitProvider 调用失败")


def _failing_search_poi(*args, **kwargs):
    raise RuntimeError("[EvalFailure] 注入的假 POI 检索失败")


# =============================================
# 实际值抽取与判定
# =============================================
def _snapshot_actual(final: dict) -> dict:
    """从图 final state 抽取断言所需字段（缺键安全）。"""
    it = final.get("itinerary") or {}
    brief = final.get("brief") or {}
    val = final.get("validation") or {}
    viols = val.get("violations", [])
    ans = final.get("final_answer") or ""
    return {
        "status": it.get("status"),
        "destination": brief.get("destination"),
        "days": brief.get("days"),
        "party_size": brief.get("party_size"),
        "pace": brief.get("pace"),
        "plan_version": it.get("plan_version"),
        "parent_plan_version": it.get("parent_plan_version"),
        "repair_rounds": final.get("repair_rounds", 0) or 0,
        "decision_required": any(
            v.get("level") == "decision_required" for v in viols),
        "error_violations": sum(1 for v in viols if v.get("level") == "error"),
        "violation_codes": sorted({v.get("code", "") for v in viols if v.get("code")}),
        "confidence": it.get("confidence"),
        "final_answer": ans,
        "notes": list(final.get("notes") or []),
        "has_itinerary": bool(it),
        "clarification": (not it) and bool(ans),
    }


def _note_hit(notes: list, needle: str) -> bool:
    return any(needle in (n or "") for n in notes)


def _judge_one(exp: dict, act: dict, turn: str) -> list[str]:
    """按契约逐字段断言，返回失败原因列表（空 = 通过）。"""
    if not exp:
        return []
    reasons: list[str] = []
    tag = f"[{turn}] "

    def _fail(msg: str) -> None:
        reasons.append(tag + msg)

    if "status" in exp and act["status"] != exp["status"]:
        _fail(f"status={act['status']}!={exp['status']}")
    for key in ("destination", "days", "party_size", "pace"):
        if key in exp and act.get(key) != exp[key]:
            _fail(f"{key}={act.get(key)}!={exp[key]}")
    if "min_plan_version" in exp and (act["plan_version"] or 0) < exp["min_plan_version"]:
        _fail(f"plan_version={act['plan_version']}<{exp['min_plan_version']}")
    if "parent_linked" in exp and exp["parent_linked"] and not act["parent_plan_version"]:
        _fail("parent_plan_version 为空，修复链未形成")
    if "repair_triggered" in exp:
        triggered = (act["repair_rounds"] or 0) >= 1
        if triggered != exp["repair_triggered"]:
            _fail(f"repair_rounds={act['repair_rounds']}，期望 triggered={exp['repair_triggered']}")
    if "decision_required" in exp and act["decision_required"] != exp["decision_required"]:
        _fail(f"decision_required={act['decision_required']}!={exp['decision_required']}")
    if "no_error_violations" in exp and exp["no_error_violations"] and act["error_violations"]:
        _fail(f"存在 {act['error_violations']} 条 error 级违反")
    if "violation_codes" in exp:
        missing = [c for c in exp["violation_codes"] if c not in act["violation_codes"]]
        if missing:
            _fail(f"缺少期望违反码 {missing}（实际 {act['violation_codes']}）")
    if "confidence_max" in exp:
        conf = act["confidence"]
        if conf is None or conf > exp["confidence_max"]:
            _fail(f"confidence={conf} 超上限 {exp['confidence_max']}")
    ans = act["final_answer"]
    for needle in exp.get("must_contain", []):
        if needle not in ans:
            _fail(f"final_answer 缺「{needle}」")
    for needle in exp.get("must_not_contain", []):
        if needle in ans:
            _fail(f"final_answer 不应含「{needle}」")
    for needle in exp.get("notes_contain", []):
        if not _note_hit(act["notes"], needle):
            _fail(f"notes 缺「{needle}」")
    for needle in exp.get("notes_not_contain", []):
        if _note_hit(act["notes"], needle):
            _fail(f"notes 不应含「{needle}」")
    if "clarification" in exp and act["clarification"] != exp["clarification"]:
        _fail(f"clarification={act['clarification']}!={exp['clarification']}")
    return reasons


# =============================================
# 图获取与 Provider 注入
# =============================================
def _get_graph(cross_turn: bool):
    """获取旅游域图；跨轮用例临时切 memory checkpointer（degraded 三态）。

    返回 (graph, restore)。restore() 恢复默认图配置（checkpointer disabled）。
    """
    import backend.config.travel as T
    import backend.travel.graph_builder as gb

    if cross_turn:
        T.TRAVEL_CHECKPOINTER_ENABLED = True
        T.TRAVEL_CHECKPOINTER_BACKEND = "memory"
        gb._travel_graph = None
    graph = gb.get_travel_graph()

    def restore() -> None:
        T.TRAVEL_CHECKPOINTER_ENABLED = False
        gb._travel_graph = None

    return graph, restore


class _ProviderFailure:
    """Failure 组的 Provider 失败注入（with 语义，finally 必恢复）。"""

    def __init__(self, fail_transit: bool, fail_poi: bool):
        self.fail_transit = fail_transit
        self.fail_poi = fail_poi
        self._orig_provider = None
        self._orig_search_poi = None

    def __enter__(self) -> "_ProviderFailure":
        if self.fail_transit:
            from backend.tools.travel import routing
            self._orig_provider = routing._route_provider
            routing.set_route_provider(_failing_transit_estimate)
        if self.fail_poi:
            import backend.tools.travel.poi as poi_mod
            self._orig_search_poi = poi_mod.search_poi
            poi_mod.search_poi = _failing_search_poi
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._orig_provider is not None or self.fail_transit:
                from backend.tools.travel import routing
                routing.set_route_provider(self._orig_provider)
            if self.fail_poi and self._orig_search_poi is not None:
                import backend.tools.travel.poi as poi_mod
                poi_mod.search_poi = self._orig_search_poi
        except Exception:  # noqa: BLE001 — 恢复失败不能再抛，避免污染后续 case
            pass


def _invoke_turn(graph, message: str, session_id: str, thread_id: str) -> dict:
    from backend.travel.graph_state import new_travel_graph_input
    return graph.invoke(
        new_travel_graph_input(message, session_id=session_id),
        config={"configurable": {"thread_id": thread_id}},
    )


# =============================================
# Runner 主体
# =============================================
def _run_travel(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    results: list[EvalResult] = []
    try:
        from backend.travel.graph_builder import get_travel_graph  # noqa: F401 — 可用性探测
        for case in cases:
            results.append(_run_case(case))
    except ImportError as e:
        results = [
            EvalResult(
                case_id=c.id, module="travel", status="error",
                expected=c.expected, actual={},
                error_msg=f"Travel module not available: {e}",
            )
            for c in cases
        ]
    return results


def _run_case(case: TestCase) -> EvalResult:
    t0 = time.time()
    meta = case.metadata or {}
    cross = bool(meta.get("same_thread"))
    tid = f"travel-eval-{case.id}-{uuid.uuid4().hex[:8]}"
    session = f"travel-eval-{case.id}"
    reasons: list[str] = []
    actual_turns: list[dict] = []

    graph = None
    restore = None
    try:
        graph, restore = _get_graph(cross)
        with _ProviderFailure(bool(meta.get("fail_transit")), bool(meta.get("fail_poi"))):
            final = _invoke_turn(graph, case.question, session, tid)
            act = _snapshot_actual(final)
            reasons += _judge_one(case.expected, act, "turn1")
            actual_turns.append(act)

            followup = meta.get("followup")
            if cross and followup:
                final2 = _invoke_turn(graph, followup, session, tid)
                act2 = _snapshot_actual(final2)
                reasons += _judge_one(meta.get("followup_expected") or {}, act2, "turn2")
                actual_turns.append(act2)
    except Exception as e:  # noqa: BLE001 — 单 case 失败不拖垮整批
        reasons.append(f"[runner] 异常: {type(e).__name__}: {e}")
    finally:
        if restore is not None:
            restore()

    status = "pass" if not reasons else "fail"
    return EvalResult(
        case_id=case.id, module="travel", status=status,
        expected=case.expected,
        actual={"turns": actual_turns},
        error_msg="; ".join(reasons) or None,
        duration_ms=int((time.time() - t0) * 1000),
    )


register_runner("travel", _run_travel, needs_live=False)
