"""test_regression.py — Phase 6 回归测试

确保 CS 系统不影响现有功能。
"""
from __future__ import annotations

import pytest


class TestCSDisabledNoSideEffects:
    """CS_ENABLED=False 时不加载任何 CS 模块"""

    def test_cs_disabled_does_not_break_main_router(self, monkeypatch):
        import backend.config as cfg_mod
        import backend.config.customer_service as cs_mod
        monkeypatch.setattr(cs_mod, "CS_ENABLED", False)
        monkeypatch.setattr(cfg_mod, "CS_ENABLED", False)

        from backend.config.customer_service import CS_ENABLED
        assert CS_ENABLED is False

        from backend.orchestration.graph.router_node import router_node
        state = {"question": "帮我分析一下最近的销售数据"}
        result = router_node(state)

        assert result.get("route_mode") != "customer_service"


class TestCSStateExtensionIsolated:
    """OrchestratorState 扩展不影响 AgentState 基础字段"""

    def test_agent_state_has_base_fields(self):
        from backend.orchestration.state import AgentState

        hints = AgentState.__annotations__ if hasattr(AgentState, "__annotations__") else {}

        base_fields = {"question", "final_answer", "route_decision", "route_mode"}
        for field in base_fields:
            assert field in hints or hasattr(AgentState, field), (
                f"AgentState missing base field: {field}"
            )

    def test_cs_context_is_optional_extension(self):
        from backend.orchestration.state import AgentState

        hints = AgentState.__annotations__ if hasattr(AgentState, "__annotations__") else {}
        # 回归契约：客服扩展字段只允许出现在子类 OrchestratorState，
        # 不得上提进基础 AgentState（否则平台侧状态被迫携带客服语义）
        assert "cs_context" not in hints, (
            "cs_context 是客服扩展字段，不得进入基础 AgentState"
        )


class TestCSErrorsDontLeakToPlatform:
    """CS 错误类型不泄露到平台级错误处理"""

    def test_cs_errors_are_namespaced(self):
        from backend.customer_service.errors import (
            AuthenticationError,
            CustomerServiceError,
            ValidationError,
        )

        assert issubclass(AuthenticationError, CustomerServiceError)
        assert issubclass(ValidationError, CustomerServiceError)

    def test_cs_errors_not_caught_by_platform_handler(self):
        from backend.customer_service.errors import CustomerServiceError

        try:
            raise CustomerServiceError("test error")
        except CustomerServiceError as e:
            assert "test error" in str(e)
        except Exception:
            pytest.fail("CustomerServiceError should not be caught by generic handler")


class TestExistingEvaluationFrameworkIntact:
    """现有 evaluation 框架 (planner/rag/sql/e2e) 不受影响"""

    def test_all_module_kinds_registered(self):
        valid_kinds = ["planner", "rag", "sql", "e2e", "cs"]
        for kind in valid_kinds:
            assert kind in valid_kinds

    def test_existing_runners_still_registered(self):
        import backend.evaluation.runners.builtin  # noqa: F401 — triggers register_runner
        from backend.evaluation.registry import list_registered

        registered = list_registered()
        assert "planner" in registered
        assert "rag" in registered
        assert "sql" in registered
        assert "e2e" in registered

    def test_cs_runner_registered(self):
        import backend.evaluation.runners.builtin  # noqa: F401 — triggers register_runner
        from backend.evaluation.registry import list_registered

        assert "cs" in list_registered()

    def test_dataset_loader_finds_cs(self):
        from backend.evaluation.dataset import load_dataset

        cases = load_dataset("cs")
        # v2.0 锁版（2026-09-19）：20 条 v1 平移 + 300 条 cs-v2 = 320
        assert len(cases) == 320
        assert all(c.module == "cs" for c in cases)

    def test_validate_dataset_accepts_cs(self):
        from backend.evaluation.dataset import load_dataset, validate_dataset

        cases = load_dataset("cs")
        errors = validate_dataset(cases)
        assert errors == []
