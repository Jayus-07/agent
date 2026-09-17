"""tests/customer_service/test_demo_sandbox.py — 演示业务沙盒单测

覆盖 SB-1（demo 身份映射）与 SB-4（物流轨迹 Mock + 故障注入）的纯逻辑部分，
不依赖数据库。方案: docs/customer-service/演示沙盒方案-2026-09-17.md
"""
from __future__ import annotations

import pytest

from backend.customer_service.service import demo_mode
from backend.customer_service.service.logistics_trace import (
    MockTraceProvider,
    TraceProviderError,
    TraceResult,
    _DEMO_TRACES,
)


# =============================================
# SB-1: demo 身份映射
# =============================================


class TestDemoMode:
    def test_off_returns_original_user_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(demo_mode, "is_demo_mode", lambda: False)
        assert demo_mode.resolve_user_id("user-abc-123") == "user-abc-123"

    def test_on_maps_to_demo_customer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(demo_mode, "is_demo_mode", lambda: True)
        monkeypatch.setattr(
            "backend.config.customer_service.CS_DEMO_CUSTOMER_ID", "99001"
        )
        # 真实登录身份（UUID 等）被映射为演示客户 ID
        assert demo_mode.resolve_user_id("3f2a...uuid") == "99001"

    def test_is_demo_mode_reads_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.config import customer_service as cs_config

        monkeypatch.setattr(cs_config, "CS_DEMO_MODE", True)
        assert demo_mode.is_demo_mode() is True
        monkeypatch.setattr(cs_config, "CS_DEMO_MODE", False)
        assert demo_mode.is_demo_mode() is False


# =============================================
# SB-4: MockTraceProvider 基本行为
# =============================================


class TestMockTraceProvider:
    def test_known_order_returns_events(self) -> None:
        provider = MockTraceProvider()
        result = provider.get_trace("DEMO-1001")
        assert isinstance(result, TraceResult)
        assert result.order_no == "DEMO-1001"
        assert len(result.events) >= 2
        assert result.latest_event is not None

    def test_unknown_order_returns_empty(self) -> None:
        provider = MockTraceProvider()
        result = provider.get_trace("REAL-9999")
        assert result.events == []
        assert result.stale_days() == -1

    def test_demo1003_is_stale_five_days(self) -> None:
        """DEMO-1003 是物流延误剧本：最后轨迹停滞约 5 天。"""
        provider = MockTraceProvider()
        result = provider.get_trace("DEMO-1003")
        assert 4 <= result.stale_days() <= 6

    def test_custom_traces_override(self) -> None:
        events = [_DEMO_TRACES["DEMO-1001"][0]]
        provider = MockTraceProvider(traces={"X-1": events})
        assert len(provider.get_trace("X-1").events) == 1
        assert provider.get_trace("DEMO-1001").events == []


# =============================================
# SB-4: 故障注入（演示异常链路）
# =============================================


class TestFaultInjection:
    def test_timeout_fault(self) -> None:
        provider = MockTraceProvider(faults={"logistics.timeout": True})
        with pytest.raises(TraceProviderError, match="超时"):
            provider.get_trace("DEMO-1001")

    def test_error_fault(self) -> None:
        provider = MockTraceProvider(faults={"logistics.error": True})
        with pytest.raises(TraceProviderError, match="错误"):
            provider.get_trace("DEMO-1001")

    def test_empty_fault(self) -> None:
        provider = MockTraceProvider(faults={"logistics.empty": True})
        assert provider.get_trace("DEMO-1001").events == []

    def test_no_faults_by_default(self) -> None:
        provider = MockTraceProvider()
        assert provider.get_trace("DEMO-1001").events  # 不应抛错


# =============================================
# SB-4: Provider 工厂
# =============================================


class TestTraceProviderFactory:
    def test_factory_returns_none_when_demo_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.customer_service.service import logistics_trace

        monkeypatch.setattr(logistics_trace, "is_demo_mode", lambda: False)
        logistics_trace.reset_trace_provider()
        assert logistics_trace.get_trace_provider() is None
        logistics_trace.reset_trace_provider()

    def test_factory_returns_mock_when_demo_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.config import customer_service as cs_config
        from backend.customer_service.service import logistics_trace

        monkeypatch.setattr(logistics_trace, "is_demo_mode", lambda: True)
        monkeypatch.setattr(cs_config, "CS_DEMO_TRACE_PROVIDER", "mock")
        monkeypatch.setattr(cs_config, "CS_DEMO_FAULTS", {"logistics.error": True})
        logistics_trace.reset_trace_provider()

        provider = logistics_trace.get_trace_provider()
        assert isinstance(provider, MockTraceProvider)
        # 故障开关已注入 Provider
        with pytest.raises(TraceProviderError):
            provider.get_trace("DEMO-1001")

        logistics_trace.reset_trace_provider()
