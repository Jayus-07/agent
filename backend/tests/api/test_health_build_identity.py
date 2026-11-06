"""/health 构建身份字段的兼容契约。"""

import asyncio

import pytest

from backend.app.api import deps
from backend.app.api.routes import health


def _stub_health_dependencies(monkeypatch):
    """禁止健康检查单元测试触达数据库、Redis 或 Provider。"""
    monkeypatch.setattr(health, "_migration_watermark", lambda: {"status": "ok"})
    monkeypatch.setattr(health, "_schema_consistency", lambda: {"status": "ok"})
    monkeypatch.setattr(health, "_context_backend_status", lambda: {"status": "healthy"})
    monkeypatch.setattr(health, "_travel_providers_status", lambda: {"status": "healthy"})
    monkeypatch.setattr(health, "_redis_status", lambda: {"status": "healthy"})
    monkeypatch.setattr(deps, "get_rag_status", lambda: {"status": "healthy"})


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("true", True), ("TRUE", True), ("false", False), ("unknown", None), ("1", None)],
)
def test_health_reports_build_dirty_state(monkeypatch, raw_value, expected):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    monkeypatch.setenv("BUILD_TIME", "2026-11-06T02:13:02Z")
    monkeypatch.setenv("GIT_DIRTY", raw_value)
    _stub_health_dependencies(monkeypatch)

    result = asyncio.run(health.health())

    assert result["build"] == {
        "commit": "abc1234",
        "build_time": "2026-11-06T02:13:02Z",
        "dirty": expected,
    }
