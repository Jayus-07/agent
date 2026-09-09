"""test_performance.py — CS Router + InputGuard 性能基准测试

确保路由和安全检查响应时间达标。
旧节点性能测试已随 Phase 7 清理移除（Expert 层测试见 test_cs_experts.py）。
"""
from __future__ import annotations

import time

ITERATIONS = 100


def _p95(latencies: list[float]) -> float:
    """计算 p95 延迟（毫秒）。"""
    sorted_lat = sorted(latencies)
    idx = int(len(sorted_lat) * 0.95)
    return sorted_lat[min(idx, len(sorted_lat) - 1)] * 1000


class TestCSRouterPerformance:
    """CS Router 延迟基准"""

    def test_router_latency_p95_under_100ms(self):
        from backend.customer_service.router.cs_router import CSRouter
        from backend.customer_service.router.types import CSDetection

        router = CSRouter()
        detection = CSDetection(is_cs=True, rule_hits=["keyword"], rule_score=0.9)

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            router.route("退货政策是什么", detection)
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 100, f"CS Router p95={p95_ms:.1f}ms > 100ms"


class TestInputGuardPerformance:
    """输入安全检查延迟基准 — 纯正则"""

    def test_input_guard_latency_p95_under_10ms(self):
        from backend.customer_service.security.input_guard import get_cs_input_guard

        guard = get_cs_input_guard()

        latencies = []
        for _ in range(ITERATIONS):
            t0 = time.perf_counter()
            guard.check("正常的用户问题，退货政策是什么")
            latencies.append(time.perf_counter() - t0)

        p95_ms = _p95(latencies)
        assert p95_ms < 10, f"Input guard p95={p95_ms:.1f}ms > 10ms"
