"""test_memory_routes.py — 记忆 API 路由测试

覆盖 memory 路由的 HTTP 语义边界（这一层此前把所有失败都包成 200 + error 字段，
导致 PostgreSQL 认证失败在前端表现为"没有会话"，真因只能靠翻 PG 日志找到）：

- _raise_for_error 三个分支：正常 / 业务缺失(404) / 基础设施故障(503)
- GET /memory/sessions        — 成功 200、DB 故障 503
- DELETE /memory/sessions/{id} — 会话不存在 404
- _validate_config            — 配置缺失时抛 MemoryDatabaseUnavailable
- 异常处理器                   — MemoryDatabaseUnavailable → 503（而非 500 兜底）
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.routes import memory as memory_route
from backend.app.exceptions import memory_db_unavailable_handler
from backend.memory.database import MemoryDatabaseUnavailable, _validate_config


# ─────────────────────────────────────────────────────────────
# _raise_for_error 单元行为
# ─────────────────────────────────────────────────────────────

def test_raise_for_error_passes_through_success():
    """无 error 字段 → 原样返回，不抛异常"""
    payload = {"sessions": [{"session_id": "s1"}], "total": 1}
    assert memory_route._raise_for_error(payload) is payload


def test_raise_for_error_maps_missing_session_to_404():
    """业务性缺失 → 404（不能当成基础设施故障）"""
    with pytest.raises(HTTPException) as exc:
        memory_route._raise_for_error({"ok": False, "error": "会话不存在"})
    assert exc.value.status_code == 404
    assert exc.value.detail == "会话不存在"


def test_raise_for_error_maps_infra_failure_to_503():
    """DB 连接类错误 → 503，且原始信息保留在 detail 里便于排查"""
    driver_msg = "connection was closed in the middle of operation"
    with pytest.raises(HTTPException) as exc:
        memory_route._raise_for_error({"sessions": [], "total": 0, "error": driver_msg})
    assert exc.value.status_code == 503
    assert driver_msg in exc.value.detail


# ─────────────────────────────────────────────────────────────
# 路由级：挂 memory router，替换 service 单例
# ─────────────────────────────────────────────────────────────

class _FakeService:
    """按需返回成功/失败载荷的假 MemoryService"""

    def __init__(self, payload: dict):
        self._payload = payload

    async def list_sessions(self, user_id: str = "default") -> dict:
        return self._payload

    async def delete_session(self, session_id: str) -> dict:
        return self._payload


@pytest.fixture
def client_factory(monkeypatch):
    """构造只挂 memory router 的 app，service 由测试指定"""

    def _make(service) -> TestClient:
        monkeypatch.setattr(memory_route, "_get_service", lambda: service)
        app = FastAPI()
        app.add_exception_handler(MemoryDatabaseUnavailable, memory_db_unavailable_handler)
        app.include_router(memory_route.router)
        # raise_server_exceptions=False：让异常走 handler 而非直接抛给测试
        return TestClient(app, raise_server_exceptions=False)

    return _make


def test_list_sessions_success_returns_200(client_factory):
    client = client_factory(_FakeService({"sessions": [{"session_id": "s1", "title": "t"}], "total": 1}))
    res = client.get("/memory/sessions")
    assert res.status_code == 200
    assert res.json()["total"] == 1


def test_list_sessions_db_failure_returns_503_not_empty_200(client_factory):
    """回归防护：DB 挂掉不能再返回 200 + 空列表（那会被前端显示为"暂无记录"）"""
    client = client_factory(_FakeService(
        {"sessions": [], "total": 0, "error": "connection was closed in the middle of operation"}
    ))
    res = client.get("/memory/sessions")
    assert res.status_code == 503
    assert "记忆库不可用" in res.json()["detail"]


def test_delete_missing_session_returns_404(client_factory):
    client = client_factory(_FakeService({"ok": False, "error": "会话不存在"}))
    res = client.delete("/memory/sessions/does-not-exist")
    assert res.status_code == 404


def test_memory_db_unavailable_is_handled_as_503(client_factory):
    """配置缺失异常应命中专用 handler → 503，而不是落到 500 兜底"""

    class _BrokenService:
        async def list_sessions(self, user_id: str = "default") -> dict:
            raise MemoryDatabaseUnavailable("PostgreSQL 连接配置缺失: PGPASSWORD")

    client = client_factory(_BrokenService())
    res = client.get("/memory/sessions")
    assert res.status_code == 503
    assert res.json()["error"] == "MemoryDatabaseUnavailable"
    # 完整配置细节只进日志，不能泄到 HTTP 响应体
    assert "PGPASSWORD" not in res.json()["detail"]


# ─────────────────────────────────────────────────────────────
# 配置校验：尽早暴露
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("missing_key,expected_env", [
    ("password", "PGPASSWORD"),
    ("user", "PGUSER"),
    ("dbname", "PGDATABASE"),
])
def test_validate_config_rejects_missing_field(monkeypatch, missing_key, expected_env):
    """缺任一必填项都要抛错，并在信息里点名对应环境变量"""
    from backend.memory import database as db_mod

    cfg = {"host": "localhost", "port": 5432, "dbname": "demo", "user": "postgres", "password": "pw"}
    cfg[missing_key] = ""
    monkeypatch.setattr(db_mod, "DB_CONFIG", cfg)

    with pytest.raises(MemoryDatabaseUnavailable) as exc:
        _validate_config()
    assert expected_env in str(exc.value)


def test_validate_config_accepts_complete_config(monkeypatch):
    from backend.memory import database as db_mod

    monkeypatch.setattr(db_mod, "DB_CONFIG", {
        "host": "localhost", "port": 5432, "dbname": "demo",
        "user": "postgres", "password": "pw",
    })
    _validate_config()  # 不抛异常即通过
