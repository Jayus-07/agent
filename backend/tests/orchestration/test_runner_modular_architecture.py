"""
test_runner_modular_architecture.py — GraphRunner 模块化架构守卫单测
"""
from pathlib import Path


class TestGraphRunnerArchitectureGuard:
    """守护 GraphRunner 门面架构，防止代码膨胀与契约漂移。"""

    def test_runner_line_count_within_budget(self):
        runner_path = Path("backend/orchestration/graph/runner.py")
        assert runner_path.exists(), "runner.py 文件必须存在"
        lines = runner_path.read_text(encoding="utf-8").splitlines()
        # 原 1716 行（单函数 931 行），重构后严格控制在 300 行以内（大幅削减 80% 以上）
        assert len(lines) <= 300, f"runner.py 行数 ({len(lines)}) 超出 300 行预算"

    def test_runner_exports_all_required_symbols(self):
        import backend.orchestration.graph.runner as runner

        required_symbols = [
            # 核心门面与类
            "GraphRunner",
            "_ANSWER_EVENT",
            # 前置门禁与决策
            "_should_exempt_guard_clarify",
            "_is_security_clarify",
            "_should_bypass_guard_for_human_relay",
            "_is_explicit_handoff",
            "_is_confirmation_text",
            "_resolve_followup_for_request",
            "_domain_node_names",
            "_new_main_graph_thread_id",
            "_looks_like_refusal_text",
            "_funnel_note_resolved",
            "_is_user_balance_exhausted",
            "_runner_error_event",
            "_pin_prompt_snapshot",
            "get_input_guard",
            # Trace 与快照导出
            "trace_from_state",
            "_build_graph_snapshot",
            "add_guard_span",
            "finish_guard_trace",
            "finish_customer_service_guard_trace",
            "_persist_cs_turn_if_needed",
            "_fallback_summary_from_results",
            "_count_rounds_from_results",
            "_end_root",
            # 引擎子模块
            "EventDispatcher",
            "GraphWorkerExecutor",
            "run_preflight",
            "PreflightResult",
        ]
        for symbol in required_symbols:
            assert hasattr(runner, symbol), f"runner 必须导出符号: {symbol}"

    def test_engine_submodules_exist_and_importable(self):
        from backend.orchestration.graph.engine import (
            dispatcher,
            executor,
            preflight,
            trace_builder,
        )

        assert dispatcher is not None
        assert executor is not None
        assert preflight is not None
        assert trace_builder is not None
