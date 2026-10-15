"""验证全局降级事件和 Skill 健康只读接口的管理员边界。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# observability 与错误分类模块有既有导入顺序约束。
from backend.core.tool_runtime.models import ToolStatus  # noqa: F401
from backend.infra.llm.error_taxonomy import MODEL_ERROR_TYPES  # noqa: F401
from backend.app.api import deps
from backend.app.api.routes import observability as obs


READ_ENDPOINTS = (
    "/api/observability/alerts",
    "/api/observability/skill-health",
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(obs.router, prefix="/api")
    return TestClient(app)


def _stub_user_role(monkeypatch, role: str) -> None:
    async def resolve_user(_request):
        return SimpleNamespace(role=role, actor="user:test")

    monkeypatch.setattr(deps, "require_user_actor", resolve_user)
    monkeypatch.setattr(deps, "get_mode", lambda _key: "enforce")


@pytest.mark.parametrize("path", READ_ENDPOINTS)
@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_global_observability_reads_reject_non_admin(monkeypatch, path, role):
    _stub_user_role(monkeypatch, role)

    response = _client().get(path)

    assert response.status_code == 403


@pytest.mark.parametrize("path", READ_ENDPOINTS)
@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_global_observability_reads_allow_admin(monkeypatch, path, role):
    _stub_user_role(monkeypatch, role)
    client = _client()
    monkeypatch.setattr(obs.trace_collector, "list", lambda **_kwargs: [])
    monkeypatch.setattr(obs.os.path, "exists", lambda _path: False)

    response = client.get(path)

    assert response.status_code == 200
