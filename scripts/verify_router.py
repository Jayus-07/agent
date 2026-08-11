"""
verify_router.py — Router 端到端验收脚本（2026-08-11）

用法:
    python scripts/verify_router.py

5 层验证:
  1. Rule Router（强信号 + 弱信号 + 无信号）
  2. Vector Router（启动 + 索引 + 检索）
  3. LLM Router（fallback + JSON 解析）
  4. Graph 编译（含 router 节点）
  5. Metrics 暴露（router_decision / router_layer / router_confidence）
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
        # (query, expected_mode, expected_confidence_min, desc)
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

            # 测试检索
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

        # 测试 fallback
        d = lr._fallback("test query", reason="test")
        check(
            "fallback 返回 RouteDecision",
            d.execution_mode == ExecutionMode.PLAN and d.confidence == 0.3,
        )
        check(
            "fallback 包含 candidates",
            len(d.candidates) > 0 and d.candidates[0].name in ALL_CAPABILITIES,
        )

        # 测试 _extract_json（模块级函数）
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

        # Rule 强信号
        t0 = time.time()
        d1 = r.route("每天跑日报")
        t1 = time.time() - t0
        check(
            "Rule 路径（每天跑日报）",
            d1.execution_mode == ExecutionMode.WORKFLOW and d1.confidence >= 0.8,
            f"{t1*1000:.1f}ms",
        )

        # SQL 强信号
        d2 = r.route("最近 30 天销量统计")
        check(
            "Rule 路径（最近销量）",
            d2.execution_mode == ExecutionMode.DIRECT and "sql.query" in [c.name for c in d2.candidates],
        )

        # 弱信号（走 LLM）
        t0 = time.time()
        d3 = r.route("差评处理流程")
        t3 = time.time() - t0
        # LLM 可能输出 plan（多步） 或 direct（单 capability 如 RAG）
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

        # 跑 3 次路由
        from backend.orchestration.router import get_router
        r = get_router()
        for q in ["每天跑日报", "最近销量", "差评"]:
            r.route(q)

        # 渲染 /metrics
        body, _ = render_metrics()
        body_str = body.decode() if isinstance(body, bytes) else body

        check("router_decision_total 暴露", "router_decision_total" in body_str)
        check("router_layer_total 暴露", "router_layer_total" in body_str)
        check("router_confidence 暴露", "router_confidence" in body_str)
        check("metrics endpoint 可用", len(body_str) > 0, f"{len(body_str)} chars")
    except Exception as e:
        check("Metrics", False, str(e))


# =============================================================
# 主流程
# =============================================================
def main():
    print("=" * 60)
    print("Router 验收脚本（5 层）")
    print("=" * 60)

    verify_rule_router()
    verify_vector_router()
    verify_llm_router()
    verify_router_main()
    verify_graph()
    verify_metrics()

    # 总结
    total = passed + failed
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过" + (f" ({failed} 失败)" if failed else " ✓"))
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
