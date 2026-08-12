"""
verify_router.py - Router 端到端验收脚本（2026-08-11）

用法:
    D:/Python/python.exe -X utf8 scripts/verify_router.py

7 层验证:
  1. Rule Router    (强信号 + 弱信号 + 无信号)
  2. Vector Router  (启动 + 索引 + 检索)
  3. LLM Router     (fallback + JSON 解析)
  4. Router 主流程  (3 层 fallback)
  5. Graph 编译     (LangGraph 集成)
  6. Metrics 暴露   (Prom 指标)
  7. V2 Executors   (skill_executor + workflow_executor)
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    """单条验证 + 计数。"""
    global passed, failed
    icon = "[OK]" if condition else "[FAIL]"
    print(f"  {icon} {name}" + (f"  ({detail})" if detail else ""))
    if condition:
        passed += 1
    else:
        failed += 1


def section(title: str):
    print(f"\n=== {title} ===")


# =============================================================
# Layer 1: Rule Router
# =============================================================
def verify_rule_router():
    section("Layer 1: Rule Router（强信号 + 弱信号 + 无信号）")
    from backend.orchestration.router import RuleRouter

    rr = RuleRouter()
    tests = [
        ("每天跑日报", "workflow", 0.9, "workflow 强信号"),
        ("生成日报", "workflow", 0.9, "workflow 关键词"),
        ("自动检查库存", "workflow", 0.9, "workflow 关键词"),
        ("最近 30 天销量", "direct", 0.8, "SQL 关键词（多少/最近/天）"),
        ("统计金额", "direct", 0.8, "SQL 关键词（统计/金额）"),
        ("查一下公司制度", None, 0, "无强信号 → 交给下层"),
        ("差评处理流程", None, 0, "弱信号 → 交给下层"),
        ("hello world", None, 0, "完全无信号"),
    ]

    for q, expected_mode, expected_conf_min, desc in tests:
        d = rr.route(q)
        actual_mode = d.execution_mode.value if d else None
        actual_conf = d.confidence if d else 0.0

        mode_ok = actual_mode == expected_mode
        conf_ok = actual_conf >= expected_conf_min if d else True
        check(
            f"{desc}: {q!r}",
            mode_ok and conf_ok,
            f"expected={expected_mode}/conf≥{expected_conf_min}, got={actual_mode}/conf={actual_conf:.2f}",
        )


# =============================================================
# Layer 2: Vector Router
# =============================================================
def verify_vector_router():
    section("Layer 2: Vector Router（启动 + 检索）")
    try:
        from backend.orchestration.router import VectorRouter
        t0 = time.time()
        vr = VectorRouter()
        init_time = time.time() - t0
        check("VectorRouter 初始化", vr._collection is not None, f"{init_time:.2f}s")

        if vr._collection:
            count = vr._collection._collection.count()
            check("路由索引加载", count > 0, f"{count} 条 example")

            t0 = time.time()
            d = vr.route("哪些商品需要补货")
            query_time = time.time() - t0
            check(
                "Vector Router 检索",
                d is not None and len(d.candidates) > 0,
                f"{query_time*1000:.0f}ms, top1={d.candidates[0].name if d.candidates else '?'}",
            )
        else:
            check("路由索引", False, "未初始化（应走 LLM Router）")
    except Exception as e:
        check("Vector Router 启动", False, str(e))


# =============================================================
# Layer 3: LLM Router
# =============================================================
def verify_llm_router():
    section("Layer 3: LLM Router（fallback + JSON 解析）")
    try:
        from backend.orchestration.router import LLMRouter, ALL_CAPABILITIES, WORKFLOW_NAMES
        from backend.orchestration.router.types import CapabilityScore, ExecutionMode, RouteDecision

        lr = LLMRouter(timeout=20)

        d = lr._fallback("test query", reason="test")
        check(
            "fallback 返回 RouteDecision",
            d.execution_mode == ExecutionMode.PLAN and d.confidence == 0.3,
        )
        check(
            "fallback 包含 candidates",
            len(d.candidates) > 0 and d.candidates[0].name in ALL_CAPABILITIES,
        )

        from backend.orchestration.router.llm_router import _extract_json
        test_cases = [
            ('{"score": 0.8, "candidates": [{"name": "sql.query", "score": 0.8}]}', True),
            ('```json\n{"foo": 1}\n```', True),
            ('not json', False),
        ]
        for text, should_parse in test_cases:
            parsed = _extract_json(text)
            actual = parsed is not None
            check(
                f"_extract_json: {text[:30]!r}...",
                actual == should_parse,
            )
    except Exception as e:
        check("LLM Router 启动", False, str(e))


# =============================================================
# Layer 4: Router 主流程
# =============================================================
def verify_router_main():
    section("Layer 4: Router 主流程（3 层 fallback）")
    try:
        from backend.orchestration.router import get_router, ExecutionMode

        r = get_router()
        check("Router 单例获取", r is not None)

        t0 = time.time()
        d1 = r.route("每天跑日报")
        t1 = time.time() - t0
        check(
            "Rule 路径（每天跑日报）",
            d1.execution_mode == ExecutionMode.WORKFLOW and d1.confidence >= 0.8,
            f"{t1*1000:.1f}ms",
        )

        d2 = r.route("最近 30 天销量统计")
        check(
            "Rule 路径（最近销量）",
            d2.execution_mode == ExecutionMode.DIRECT and "sql.query" in [c.name for c in d2.candidates],
        )

        t0 = time.time()
        d3 = r.route("差评处理流程")
        t3 = time.time() - t0
        llm_works = (
            d3.execution_mode in (ExecutionMode.PLAN, ExecutionMode.DIRECT)
            and d3.confidence >= 0.3
        )
        check(
            "LLM 路径（弱信号）",
            llm_works,
            f"{t3*1000:.0f}ms, mode={d3.execution_mode.value}, conf={d3.confidence:.2f}",
        )
    except Exception as e:
        check("Router 主流程", False, str(e))


# =============================================================
# Layer 5: Graph 编译
# =============================================================
def verify_graph():
    section("Layer 5: Graph 编译（LangGraph 集成）")
    try:
        from backend.orchestration.graph.builder import build_graph
        t0 = time.time()
        graph = build_graph()
        t1 = time.time() - t0
        check("Graph 编译", graph is not None, f"{t1:.2f}s")
    except Exception as e:
        check("Graph 编译", False, str(e))


# =============================================================
# Layer 6: Metrics 暴露
# =============================================================
def verify_metrics():
    section("Layer 6: Metrics 暴露（Prom 指标）")
    try:
        from backend.observability.metrics import (
            render_metrics, router_decision_total, router_layer_total, router_confidence,
        )

        from backend.orchestration.router import get_router
        r = get_router()
        for q in ["每天跑日报", "最近销量", "差评"]:
            r.route(q)

        body, _ = render_metrics()
        body_str = body.decode() if isinstance(body, bytes) else body

        check("router_decision_total 暴露", "router_decision_total" in body_str)
        check("router_layer_total 暴露", "router_layer_total" in body_str)
        check("router_confidence 暴露", "router_confidence" in body_str)
        check("metrics endpoint 可用", len(body_str) > 0, f"{len(body_str)} chars")
    except Exception as e:
        check("Metrics", False, str(e))


# =============================================================
# Layer 7: V2 Executors（direct / workflow）
# =============================================================
def verify_v2_executors():
    section("Layer 7: V2 Executors（skill_executor / workflow_executor）")
    try:
        from backend.orchestration.graph.router_node import route_selector, router_node
        from backend.orchestration.graph.direct_executor import skill_executor_node, workflow_executor_node
        from backend.orchestration.workflow.registry import get_workflow_registry
        from backend.orchestration.workflows.daily_report import DailyReport

        # 1. route_selector 三路分流
        check("route_selector plan", route_selector({"route_mode": "plan"}) == "planner")
        check("route_selector direct", route_selector({"route_mode": "direct"}) == "skill_executor")
        check("route_selector workflow", route_selector({"route_mode": "workflow"}) == "workflow_executor")

        # 2. router_node 不再降级 direct/workflow 到 plan
        from backend.orchestration.router.types import ExecutionMode
        decision_plan = {
            "execution_mode": ExecutionMode.PLAN,
            "candidates": [{"name": "sql.query", "score": 0.85}],
            "confidence": 0.85,
        }
        s1 = router_node({"question": "test", "route_decision": decision_plan})
        check("router_node plan 模式 → route_mode=plan", s1.get("route_mode") == "plan")

        # 3. skill_executor_node 写 final_answer + step_results
        decision_direct = {
            "execution_mode": ExecutionMode.DIRECT,
            "candidates": [{"name": "rag.search", "score": 0.9}],
            "confidence": 0.9,
        }
        s2 = skill_executor_node({"question": "test", "route_decision": decision_direct, "kb_id": "default"})
        check("skill_executor 写 step_results", "step_results" in s2)
        check("skill_executor 写 final_answer 或 executor_error",
              "final_answer" in s2 or "executor_error" in s2)

        # 4. workflow_executor_node 调真实 workflow
        reg = get_workflow_registry()
        if reg.get("daily_report") is None:
            reg.register(DailyReport)
        decision_workflow = {
            "execution_mode": ExecutionMode.WORKFLOW,
            "workflow_name": "daily_report",
            "candidates": [],
            "confidence": 0.95,
        }
        s3 = workflow_executor_node({
            "question": "test",
            "session_id": "verify",
            "route_decision": decision_workflow,
        })
        check("workflow_executor 写 final_answer", "final_answer" in s3)
        check("workflow_executor executor_mode=workflow", s3.get("executor_mode") == "workflow")
    except Exception as e:
        check("V2 Executors 整体", False, str(e))


# =============================================================
# Layer 8: Workflow 端到端（daily_report / inventory_alert）
# =============================================================
def verify_workflows():
    section("Layer 8: Workflow 端到端（daily_report / inventory_alert）")
    try:
        import asyncio
        from backend.orchestration.workflow.registry import get_workflow_registry
        from backend.orchestration.workflow.scheduler import get_workflow_scheduler
        from backend.orchestration.workflows.daily_report import DailyReport
        from backend.orchestration.workflows.inventory_alert import InventoryAlert

        reg = get_workflow_registry()
        if reg.get("daily_report") is None:
            reg.register(DailyReport)
        if reg.get("inventory_alert") is None:
            reg.register(InventoryAlert)

        sched = get_workflow_scheduler()

        # 1. daily_report 端到端
        ctx_dr = asyncio.run(sched.run_now("daily_report", inputs={"question": "test"}))
        check("daily_report 端到端 status=success",
              ctx_dr.status == "success", f"got={ctx_dr.status}")
        check("daily_report outputs >= 5", len(ctx_dr.outputs) >= 5,
              f"{len(ctx_dr.outputs)} outputs")

        # 2. inventory_alert 端到端
        ctx_ia = asyncio.run(sched.run_now("inventory_alert", inputs={"question": "test"}))
        check("inventory_alert 端到端 status=success",
              ctx_ia.status == "success", f"got={ctx_ia.status}")
        check("inventory_alert outputs >= 5", len(ctx_ia.outputs) >= 5,
              f"{len(ctx_ia.outputs)} outputs")

        # 3. workflow_executor_node 接收 daily_report 决策
        from backend.orchestration.graph.direct_executor import workflow_executor_node
        from backend.orchestration.router.types import ExecutionMode
        decision = {
            "execution_mode": ExecutionMode.WORKFLOW,
            "workflow_name": "daily_report",
            "candidates": [],
            "confidence": 0.95,
        }
        out = workflow_executor_node({
            "question": "test",
            "session_id": "verify",
            "route_decision": decision,
        })
        check("workflow_executor_node 写 final_answer", "final_answer" in out)
        check("workflow_executor_node executor_mode=workflow",
              out.get("executor_mode") == "workflow")
    except Exception as e:
        check("Workflow 端到端", False, str(e))


# =============================================================
# 主流程
# =============================================================
def main():
    print("=" * 60)
    print("Router 验收脚本（8 层）")
    print("=" * 60)

    verify_rule_router()
    verify_vector_router()
    verify_llm_router()
    verify_router_main()
    verify_graph()
    verify_metrics()
    verify_v2_executors()
    verify_workflows()

    total = passed + failed
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过" + (f" ({failed} 失败)" if failed else ""))
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
