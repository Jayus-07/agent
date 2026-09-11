"""Graph E2E Runner — 全链路评测（Router → Planner → Supervisor → Skill → Reporter）。

与 RAG/SQL 数据集并列的第三轨：不测单个节点，测整图协作是否正确完成任务，
覆盖路由决策、DAG 拆解、capability 分发、workflow 命中与最终终态。

双模式（与 rag/sql runner 的 live/offline 双轨一致）：

离线模式 (live=False) — 数据集健全性校验，不调 LLM / 不执行图：
  - expected.route 必须是合法路由模式（direct/plan/workflow/customer_service）
  - expected.capabilities 必须全部在 Skill 注册表（capability 单一事实来源）
  - expected.workflow_name 必须在 Workflow 注册表（与 app/server.py 启动注册对齐）
  - expected.final_state 必须是合法终态（SUCCESS/FAILED/BLOCKED）
  - 对抗用例（should_block）不进图，标记 skip
  适合 CI 常驻：数据集改动即触发校验。

在线模式 (live=True) — 真实构建 StateGraph 并 invoke，四指标：
  - route_correct       路由模式命中（final.route_mode == expected.route）
  - workflow_correct    workflow 名命中（仅 workflow 用例，executor_workflow）
  - capability_coverage 已执行 capability 对期望集合的覆盖率
                        （Planner 可多拆步骤，多不扣分；漏则失分）
  - step_success_rate   步骤成功率（step_results 中 success 占比）
  终态判定：SUCCESS = 无 failed 步骤 + final_answer 非空 + 无 executor_error。
  对抗用例直接调 Input Guard，校验拦截语义（BLOCK/CLARIFY 均算正确拦截）。

注意：live 模式会真实执行 workflow（daily_report 含发送邮件等副作用），
请在测试环境运行，不要在生产库上跑全量。
"""
from __future__ import annotations

import time

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner

# Router 可下发的路由模式（router_node.route_selector 的分支 + CS 短路）
_VALID_ROUTES = {"direct", "plan", "workflow", "customer_service"}

# 终态枚举：SUCCESS=全部步骤成功且答案非空；FAILED=有失败步骤/空答案；BLOCKED=Guard 拦截
_VALID_FINAL_STATES = {"SUCCESS", "FAILED", "BLOCKED"}

# Guard 视为"正确拦截"的动作（与 system.ask 短路语义一致）
_INTERCEPT_ACTIONS = {"block", "clarify"}


# =================================================
# 注册表快照（离线校验用）
# =================================================

def _known_capabilities() -> set[str]:
    """已注册 capability 集合 — 从 Skill 实例派生（ADR-0001 单一事实来源）。"""
    from backend.orchestration.tool_registry import tool_registry
    return set(tool_registry.CAPABILITY_MAP.keys())


def _known_workflows() -> set[str]:
    """已注册 workflow 名集合。

    评测独立运行时全局 registry 为空 —— 按 app/server.py 启动注册顺序补齐
    （register 幂等覆盖，server 启动后再跑也不会重复注册）。
    """
    from backend.orchestration.workflow.registry import get_workflow_registry
    reg = get_workflow_registry()
    if not reg.list_metas():
        from backend.orchestration.workflows.daily_report import DailyReport
        from backend.orchestration.workflows.inventory_alert import InventoryAlert
        from backend.orchestration.workflows.selection_decision import SelectionDecision
        for cls in (DailyReport, InventoryAlert, SelectionDecision):
            reg.register(cls)
    return {m.name for m in reg.list_metas()}


# =================================================
# 离线模式：数据集健全性校验
# =================================================

def _run_offline_sanity(cases: list[TestCase]) -> list[EvalResult]:
    known_caps = _known_capabilities()
    known_wfs = _known_workflows()

    results: list[EvalResult] = []
    for case in cases:
        exp = case.expected
        if exp.get("should_block"):
            results.append(EvalResult(
                case_id=case.id, module="e2e", status="skip",
                expected=exp, actual={},
            ))
            continue

        problems: list[str] = []
        route = exp.get("route")
        if route not in _VALID_ROUTES:
            problems.append(f"route 非法: {route!r}，合法值 {sorted(_VALID_ROUTES)}")

        bad_caps = [c for c in exp.get("capabilities") or [] if c not in known_caps]
        if bad_caps:
            problems.append(f"capabilities 未注册: {bad_caps}")

        if route == "workflow":
            wf = exp.get("workflow_name")
            if not wf:
                problems.append("workflow 用例缺少 workflow_name")
            elif wf not in known_wfs:
                problems.append(f"workflow_name 未注册: {wf}")
        elif route in ("direct", "plan") and not exp.get("capabilities"):
            problems.append(f"{route} 用例应声明 capabilities 期望")

        final_state = exp.get("final_state", "SUCCESS")
        if final_state not in _VALID_FINAL_STATES:
            problems.append(f"final_state 非法: {final_state!r}")

        results.append(EvalResult(
            case_id=case.id, module="e2e",
            status="fail" if problems else "pass",
            expected=exp,
            actual={"problems": problems},
            error_msg="; ".join(problems) or None,
        ))
    return results


# =================================================
# 在线模式：真实链路评测
# =================================================

def _executed_capabilities(final: dict) -> set[str]:
    """从 final_state.plan.nodes 提取实际执行的 capability 集合。

    direct 模式：skill_executor 写入 direct_1（+ 前置 direct_0）；
    plan 模式：Planner 生成的 DAG 节点；
    workflow 模式：plan 为空（workflow 结果在 step_results/workflow_result）。
    """
    plan_nodes = (final.get("plan") or {}).get("nodes", {})
    return {
        n.get("capability")
        for n in plan_nodes.values()
        if isinstance(n, dict) and n.get("capability")
    }


def _judge_final_state(final: dict) -> str:
    """终态判定：FAILED = 有 failed 步骤 / executor_error / 空答案。"""
    steps = final.get("step_results", {})
    has_failed = any(
        isinstance(s, dict) and s.get("status") == "failed"
        for s in steps.values()
    )
    if final.get("executor_error") or has_failed:
        return "FAILED"
    if not (final.get("final_answer") or "").strip():
        return "FAILED"
    return "SUCCESS"


def _eval_guard_case(case: TestCase, exp: dict) -> EvalResult:
    """对抗用例：Input Guard 拦截（BLOCK/CLARIFY）即为正确。"""
    from backend.security.input_guard import get_input_guard

    guard = get_input_guard().guard(case.question, session_id=f"eval-{case.id}")
    intercepted = guard.action.value in _INTERCEPT_ACTIONS
    return EvalResult(
        case_id=case.id, module="e2e",
        status="pass" if intercepted else "fail",
        expected=exp,
        actual={"guard_action": guard.action.value, "guard_message": guard.message},
        metrics={"guard_intercepted": 1.0 if intercepted else 0.0},
        error_msg=None if intercepted else (
            f"期望 Guard 拦截，实际 action={guard.action.value}"
        ),
    )


def _eval_graph_case(case: TestCase, exp: dict, final: dict) -> EvalResult:
    """正向用例：路由命中 + workflow 命中 + capability 覆盖 + 终态。"""
    route_mode = final.get("route_mode")
    route_correct = 1.0 if route_mode == exp.get("route") else 0.0

    # workflow 命中：executor_workflow 优先，缺省回退 route_decision.workflow_name
    workflow_correct: float | None = None
    actual_workflow = final.get("executor_workflow") or \
        (final.get("route_decision") or {}).get("workflow_name")
    if exp.get("route") == "workflow":
        workflow_correct = 1.0 if actual_workflow == exp.get("workflow_name") else 0.0

    expected_caps = set(exp.get("capabilities") or [])
    executed = _executed_capabilities(final)
    capability_coverage = (
        len(executed & expected_caps) / len(expected_caps)
        if expected_caps else None
    )

    steps = final.get("step_results", {})
    step_success_rate = (
        sum(1 for s in steps.values()
            if isinstance(s, dict) and s.get("status") == "success") / len(steps)
        if steps else None
    )

    actual_state = _judge_final_state(final)
    expected_state = exp.get("final_state", "SUCCESS")

    # ── 判定：逐项检查，失败信息聚合 ──
    problems: list[str] = []
    if route_correct < 1.0:
        problems.append(f"路由期望 {exp.get('route')}，实际 {route_mode}")
    if workflow_correct is not None and workflow_correct < 1.0:
        problems.append(f"workflow 期望 {exp.get('workflow_name')}，实际 {actual_workflow}")
    if capability_coverage is not None and capability_coverage < 1.0:
        problems.append(f"capability 未全覆盖: 缺 {sorted(expected_caps - executed)}")
    if actual_state != expected_state:
        problems.append(f"终态期望 {expected_state}，实际 {actual_state}")

    metrics: dict[str, float | None] = {
        "route_correct": route_correct,
        "workflow_correct": workflow_correct,
        "capability_coverage": capability_coverage,
        "step_success_rate": step_success_rate,
    }
    return EvalResult(
        case_id=case.id, module="e2e",
        status="fail" if problems else "pass",
        expected=exp,
        actual={
            "route_mode": route_mode,
            "executor_workflow": actual_workflow,
            "executed_capabilities": sorted(executed),
            "step_statuses": {
                k: s.get("status") for k, s in steps.items() if isinstance(s, dict)
            },
            "final_state": actual_state,
            "final_answer_preview": (final.get("final_answer") or "")[:120],
        },
        metrics=metrics,
        error_msg="; ".join(problems) or None,
    )


def _run_live(cases: list[TestCase]) -> list[EvalResult]:
    from backend.orchestration.graph.builder import build_graph
    from backend.orchestration.graph.events import make_initial_state

    graph = build_graph()
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        exp = case.expected

        if exp.get("should_block"):
            try:
                er = _eval_guard_case(case, exp)
            except Exception as e:
                er = EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=exp, actual={}, error_msg=f"Guard 评测异常: {e}",
                )
            er.duration_ms = int((time.time() - t0) * 1000)
            results.append(er)
            continue

        kb_id = case.metadata.get("kb_id", "default")
        initial = make_initial_state(
            case.question, session_id=f"eval-{case.id}", kb_id=kb_id, messages=[],
        )
        try:
            final = graph.invoke(initial)
            er = _eval_graph_case(case, exp, final)
        except Exception as e:
            er = EvalResult(
                case_id=case.id, module="e2e", status="error",
                expected=exp, actual={},
                error_msg=f"图执行异常: {e}",
            )
        er.duration_ms = int((time.time() - t0) * 1000)
        results.append(er)
    return results


def _run_e2e(cases: list[TestCase], live: bool = False, **kwargs) -> list[EvalResult]:
    if not live:
        return _run_offline_sanity(cases)
    return _run_live(cases)


register_runner("e2e", _run_e2e, needs_live=False)
