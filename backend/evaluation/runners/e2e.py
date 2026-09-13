"""Graph E2E Runner — 全链路评测（Router → Planner → Supervisor → Skill → Reporter）。

与 RAG/SQL 数据集并列的第三轨：不测单个节点，测整图协作是否正确完成任务，
覆盖路由决策、DAG 拆解、capability 分发、workflow 命中与最终终态。

双模式（与 rag/sql runner 的 live/offline 双轨一致）：

离线模式 (live=False) — 健全性校验，不调 LLM / 不执行图：
  - expected.route 必须是合法路由模式（direct/plan/workflow/customer_service）
  - expected.capabilities 必须全部在 Skill 注册表（capability 单一事实来源）
  - expected.workflow_name 必须在 Workflow 注册表（与 app/server.py 启动注册对齐）
  - expected.final_state 必须是合法终态（SUCCESS/FAILED/BLOCKED）
  - 对抗用例（should_block）直接评测 Input Guard 拦截语义
    （L0/L1 为纯规则，LLM 分支默认关闭 → 离线确定可跑，不 skip）
  - 故障注入用例（expected.fault）确定性执行探针 Skill / direct executor，
    断言 Skill 边界语义：重试上限、错误分类、输出契约归一化、参数契约
    快速失败、审批门降级、SQLResult 渲染。适合 CI 常驻。

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

        # 故障注入用例：确定性执行探针 Skill / executor，不进图不调 LLM
        if exp.get("fault"):
            try:
                er = _eval_fault_case(case, exp)
            except Exception as e:
                er = EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=exp, actual={}, error_msg=f"故障注入评测异常: {e}",
                )
            results.append(er)
            continue

        # 对抗用例：Guard L0/L1 是纯规则，离线确定性可跑（不进图）
        if exp.get("should_block"):
            try:
                er = _eval_guard_case(case, exp)
            except Exception as e:
                er = EvalResult(
                    case_id=case.id, module="e2e", status="error",
                    expected=exp, actual={}, error_msg=f"Guard 评测异常: {e}",
                )
            results.append(er)
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


# =================================================
# 离线故障注入：Skill 边界语义回归（不依赖 LLM / 真实图）
# =================================================

def _probe_state(capability: str, params: dict, step_id: str = "step_1") -> dict:
    return {
        "current_step_id": step_id,
        "step_results": {},
        "question": "评估探针",
        "plan": {"nodes": {step_id: {
            "capability": capability, "description": "评估探针",
            "params": params}}, "edges": {}},
    }


class _FaultProbeSkill:
    """动态构造的探针 Skill：真实 BaseSkill 子类 + 可编程假 Tool。

    类名以 "_" 开头以跳过 BaseSkill.__init_subclass__ 必填校验。
    """

    @staticmethod
    def build(tool_fn, *, params_schema=None, output_type=None,
              default_timeout=None):
        from backend.skills.base import BaseSkill

        attrs = {
            "name": "eval_probe",
            "capabilities": ["probe.cap"],
            "description": "评估故障注入探针",
            "params_schema": params_schema or {},
            "examples": [{"q": "probe"}],
            # 必须在 type() 创建时覆写抽象属性——事后赋值不会清除
            # __abstractmethods__
            "_tool_fn": property(lambda self: tool_fn),
        }
        if output_type is not None:
            attrs["output_type"] = output_type
        if default_timeout is not None:
            attrs["default_timeout"] = default_timeout

        cls = type("_EvalFaultProbe", (BaseSkill,), attrs)
        return cls()


class _InvokeAdapter:
    """把普通函数适配成 BaseSkill 期待的 .invoke(params) 接口。"""

    def __init__(self, fn):
        self._fn = fn

    def invoke(self, params):
        return self._fn(params)


def _run_probe(tool_fn, capability="probe.cap", params=None, **skill_kw) -> dict:
    """执行探针 Skill，返回 {"step_results": ...}。"""
    import asyncio

    if not hasattr(tool_fn, "invoke"):
        tool_fn = _InvokeAdapter(tool_fn)
    skill = _FaultProbeSkill.build(tool_fn, **skill_kw)
    return asyncio.run(skill.execute(
        _probe_state(capability, params or {}), step_capability=capability))


def _eval_fault_case(case: TestCase, exp: dict) -> EvalResult:
    """故障注入用例：按 fault 类型执行确定性探针，断言 Skill 边界语义。

    fault 类型：
      unretryable_error / retryable_error / timeout  — 重试与错误分类
      output_dict_normalized / structured_str_parsed — 输出契约归一化
      param_missing_required / param_enum_violation  — 参数契约快速失败
      approval_pending                               — 写操作审批门降级
      executor_no_candidates / executor_skill_not_found — direct 执行降级
      executor_step_failed                           — 失败步骤不产 final_answer
      sqlresult_rendered                             — SQLResult 渲染进 final_answer
    """
    import json as _json
    from unittest.mock import patch

    fault = exp.get("fault")
    problems: list[str] = []
    actual: dict = {"fault": fault}
    metrics: dict[str, float | None] = {}
    sr: dict = {}

    if fault == "unretryable_error":
        calls = {"n": 0}

        def tool(params):
            calls["n"] += 1
            raise RuntimeError("no such table: orders")

        sr = _run_probe(tool)["step_results"]["step_1"]
        metrics["tool_calls"] = calls["n"]
        if sr.get("status") != "failed":
            problems.append(f"status 期望 failed，实际 {sr.get('status')}")
        if sr.get("error_type") != "not_found":
            problems.append(
                f"error_type 期望 not_found，实际 {sr.get('error_type')}")
        if calls["n"] != 1:
            problems.append(f"不可重试错误应只调 1 次，实际 {calls['n']} 次")

    elif fault == "retryable_error":
        calls = {"n": 0}

        def tool(params):
            calls["n"] += 1
            raise RuntimeError("connection refused")

        sr = _run_probe(tool)["step_results"]["step_1"]
        metrics["tool_calls"] = calls["n"]
        if sr.get("status") != "failed":
            problems.append(f"status 期望 failed，实际 {sr.get('status')}")
        if sr.get("error_type") != "unknown":
            problems.append(
                f"error_type 期望 unknown，实际 {sr.get('error_type')}")
        if calls["n"] != 3:  # 首次 + max_retries=2
            problems.append(f"可重试错误应耗尽 3 次，实际 {calls['n']} 次")

    elif fault == "timeout":
        import time as _time

        def tool(params):
            _time.sleep(0.5)

        sr = _run_probe(tool, default_timeout=0.1)["step_results"]["step_1"]
        if sr.get("status") != "failed":
            problems.append(f"status 期望 failed，实际 {sr.get('status')}")
        if sr.get("error_type") != "timeout":
            problems.append(
                f"error_type 期望 timeout，实际 {sr.get('error_type')}")

    elif fault == "output_dict_normalized":
        def tool(params):
            return {"rows": [{"x": 1}]}

        sr = _run_probe(tool)["step_results"]["step_1"]
        output = sr.get("output")
        if not isinstance(output, str):
            problems.append(f"text 契约下 output 应为 str，实际 {type(output).__name__}")
        elif _json.loads(output) != {"rows": [{"x": 1}]}:
            problems.append("dict 序列化后内容不一致")

    elif fault == "structured_str_parsed":
        def tool(params):
            return _json.dumps({"summary": "洞察"}, ensure_ascii=False)

        sr = _run_probe(tool, output_type="structured")["step_results"]["step_1"]
        if sr.get("output") != {"summary": "洞察"}:
            problems.append(
                f"structured 契约应从 JSON 字符串解析出 dict，实际 {sr.get('output')!r}")

    elif fault == "param_missing_required":
        calls = {"n": 0}

        def tool(params):
            calls["n"] += 1
            return "ok"

        schema = {"url": {"type": "string", "required": True, "description": "目标"}}
        sr = _run_probe(tool, params_schema=schema, params={})["step_results"]["step_1"]
        metrics["tool_calls"] = calls["n"]
        if sr.get("status") != "failed" or sr.get("error_type") != "invalid_param":
            problems.append(
                f"缺必填参数应快速失败 invalid_param，实际 {sr.get('status')}/{sr.get('error_type')}")
        if calls["n"] != 0:
            problems.append(f"参数校验失败不应调用 Tool，实际 {calls['n']} 次")

    elif fault == "param_enum_violation":
        calls = {"n": 0}

        def tool(params):
            calls["n"] += 1
            return "ok"

        schema = {"action": {"type": "string", "enum": ["list", "add"]}}
        sr = _run_probe(tool, params_schema=schema,
                        params={"action": "turbo"})["step_results"]["step_1"]
        metrics["tool_calls"] = calls["n"]
        if sr.get("error_type") != "invalid_param" or "不在允许范围" not in (sr.get("error") or ""):
            problems.append(
                f"枚举违规应报 invalid_param，实际 {sr.get('error_type')}/{sr.get('error')}")

    elif fault == "approval_pending":
        def tool(params):
            # 模拟真实门控工具：先查审批，等待中则把提示作为输出返回
            from backend.security.tool_approval import ensure_approved
            pending = ensure_approved("probe", "write", user_id="eval", detail={})
            if pending is not None:
                return pending
            return "不应被调用"

        fake_pending = "⏳ 待审批：写操作需管理员批准"
        with patch("backend.security.tool_approval.ensure_approved",
                   lambda *a, **k: fake_pending):
            sr = _run_probe(tool)["step_results"]["step_1"]
        if fake_pending not in (sr.get("output") or ""):
            problems.append(f"审批等待提示应透传到 output，实际 {sr.get('output')!r}")

    elif fault in ("executor_no_candidates", "executor_skill_not_found"):
        from backend.orchestration.graph import direct_executor as _de

        if fault == "executor_no_candidates":
            state = {"question": "q", "route_decision": {"candidates": []}}
        else:
            state = {"question": "q",
                     "route_decision": {"candidates": [{"name": "ghost.cap", "score": 0.9}]}}

        with patch.object(_de.tool_registry, "get_skill_nodes", lambda: {}):
            out = _de.skill_executor_node(state)
        executor_error = out.get("executor_error", "")
        want = "no_candidates" if fault == "executor_no_candidates" else "skill_not_found"
        if not executor_error.startswith(want):
            problems.append(f"executor_error 应以 {want} 开头，实际 {executor_error!r}")
        failed_step = (out.get("step_results") or {}).get("direct_1", {})
        if failed_step.get("status") != "failed":
            problems.append("降级路径应补 failed step_results")
        actual["executor_error"] = executor_error

    elif fault == "sqlresult_rendered":
        from backend.orchestration.graph import direct_executor as _de

        async def fake_sql(state):
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "success",
                "output": {"columns": ["商品", "库存"],
                           "rows": [{"商品": "A", "库存": 3}]}}}}

        state = {"question": "查库存",
                 "route_decision": {"candidates": [{"name": "sql.query", "score": 0.9}]}}
        with patch.object(_de.tool_registry, "get_skill_nodes",
                          lambda: {"sql_skill": fake_sql}):
            out = _de.skill_executor_node(state)
        answer = out.get("final_answer")
        if not isinstance(answer, str) or "|" not in answer or "商品" not in answer:
            problems.append(f"SQLResult 应渲染为 Markdown 表格，实际 {answer!r}")
        actual["final_answer_preview"] = (answer or "")[:80]

    elif fault == "executor_step_failed":
        from backend.orchestration.graph import direct_executor as _de

        async def failing_sql(state):
            sid = state["current_step_id"]
            return {"step_results": {sid: {
                "status": "failed", "output": None,
                "error": "no such table: orders", "error_type": "not_found"}}}

        state = {"question": "查库存",
                 "route_decision": {"candidates": [{"name": "sql.query", "score": 0.9}]}}
        with patch.object(_de.tool_registry, "get_skill_nodes",
                          lambda: {"sql_skill": failing_sql}):
            out = _de.skill_executor_node(state)
        answer = out.get("final_answer")
        if answer:
            # 曾返回 str(None)="None"，占住 truthy final_answer 后 reporter
            # 的降级文案被 runner 忽略，用户看到字面量 "None" 且被记忆落库
            problems.append(
                f"失败步骤 final_answer 必须为空串（让 reporter 降级接管），实际 {answer!r}")
        failed_step = (out.get("step_results") or {}).get("direct_1", {})
        if failed_step.get("status") != "failed" \
                or failed_step.get("error_type") != "not_found":
            problems.append(
                f"失败步骤应留痕 status=failed/error_type=not_found，"
                f"实际 {failed_step.get('status')}/{failed_step.get('error_type')}")
        actual["final_answer_preview"] = (answer or "")[:80]

    else:
        return EvalResult(
            case_id=case.id, module="e2e", status="error",
            expected=exp, actual=actual,
            error_msg=f"未知故障类型: {fault!r}",
        )

    # 通用终态断言：声明了 expected.final_state 时校验步骤状态
    want_state = exp.get("final_state")
    if want_state and sr:
        actual_status = sr.get("status")
        if actual_status != want_state.lower():
            problems.append(
                f"步骤状态期望 {want_state.lower()}，实际 {actual_status}")

    return EvalResult(
        case_id=case.id, module="e2e",
        status="fail" if problems else "pass",
        expected=exp, actual=actual, metrics=metrics,
        error_msg="; ".join(problems) or None,
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
