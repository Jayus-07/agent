"""Prompt API integration tests (httpx + FastAPI TestClient).

Tests the REST endpoints with mocked DB layer.

2026-09-15 S0-2 迁移说明：
  prompts 全部端点（含只读）的角色改由 `backend.app.api.deps.resolve_operator_role`
  解析 —— 当前只认 `X-Internal-Token` 服务凭据。原先的 `X-Operator-Role` 请求头
  已废除（客户端自设头、可伪造，曾是越权发布/回滚高风险 Prompt 的入口）。

  因此本文件做了两处结构性调整：
    ① 所有请求统一带 `AUTH`（并 monkeypatch 内部令牌），否则会 401；
    ② 请求级无法再表达「已认证但权限不足的 editor」→ 权限矩阵语义下移到
       `TestPermissionMatrix`，直接单测 `_check_permission`（纯函数）锁定。
"""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

INTERNAL_TOKEN = "test-internal-token"
AUTH = {"X-Internal-Token": INTERNAL_TOKEN}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "backend.config.messaging.AI_INTERNAL_TOKEN", INTERNAL_TOKEN, raising=False
    )
    from backend.app.api.routes.prompts import router as prompts_router
    app = FastAPI()
    app.include_router(prompts_router, prefix="/api")
    return TestClient(app)


class TestAuthRequired:
    """S0-2 的边界：无凭据一律拒绝，伪造角色头一律失效。"""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/prompts"),
            ("get", "/api/prompts/meta/registry"),  # 只读端点同样受保护
            ("get", "/api/prompts/rag.qa"),
        ],
    )
    def test_read_endpoints_require_credential(self, client, method, path):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method} {path} 应要求服务凭据"

    def test_write_endpoint_requires_credential(self, client):
        resp = client.post(
            "/api/prompts/rag.qa/publish",
            json={"version": 1},
        )
        assert resp.status_code == 401

    def test_wrong_credential_rejected(self, client):
        resp = client.get("/api/prompts", headers={"X-Internal-Token": "wrong"})
        assert resp.status_code == 401

    def test_spoofed_role_header_ignored(self, client):
        """回归锁：伪造 `X-Operator-Role` 不再具备任何效果。"""
        resp = client.get(
            "/api/prompts",
            headers={"X-Operator-Role": "admin", "X-Operator-Id": "attacker"},
        )
        assert resp.status_code == 401, "客户端角色头必须已彻底失效"


class TestGetRegistry:
    def test_returns_all_specs(self, client):
        resp = client.get("/api/prompts/meta/registry", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 34
        keys = {s["key"] for s in data["specs"]}
        assert "security.input_guard" in keys
        assert "rag.qa" in keys


class TestListPrompts:
    def test_list_empty_db(self, client):
        with patch("backend.memory.database.AsyncSessionLocal") as mock_session:
            mock_sess = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_repo = AsyncMock()
            mock_repo.list_all.return_value = []

            with patch("backend.memory.repository.prompt_repo.PromptRepository", return_value=mock_repo):
                resp = client.get("/api/prompts", headers=AUTH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestGetPrompt:
    def test_unknown_key_404(self, client):
        resp = client.get("/api/prompts/nonexistent.key", headers=AUTH)
        assert resp.status_code == 404

    def test_from_defaults(self, client):
        with patch("backend.memory.database.AsyncSessionLocal") as mock_session:
            mock_sess = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_repo = AsyncMock()
            mock_repo.get_by_key.return_value = None

            with patch("backend.memory.repository.prompt_repo.PromptRepository", return_value=mock_repo):
                with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
                    mock_svc._defaults = {"memory.trigger": "Test template {content}"}
                    resp = client.get("/api/prompts/memory.trigger", headers=AUTH)

        assert resp.status_code == 200
        data = resp.json()
        assert data["key"] == "memory.trigger"
        assert data["source"] == "default"


class TestRender:
    def test_render_with_override(self, client):
        body = {
            "variables": {"name": "World"},
            "template": "Hello {name}!",
        }
        resp = client.post("/api/prompts/memory.trigger/render", json=body, headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Hello World!"
        assert data["source"] == "playground"

    def test_render_missing_vars_422(self, client):
        body = {
            "variables": {},
            "template": "Hello {name}!",
        }
        resp = client.post("/api/prompts/memory.trigger/render", json=body, headers=AUTH)
        assert resp.status_code == 422


class TestPermissions:
    """请求级能表达的权限语义（角色固定为服务凭据 → admin）。"""

    def test_critical_draft_403(self, client):
        """critical 风险为代码受控、只读 —— 即便 admin 也不能改。"""
        body = {"template": "evil", "change_note": ""}
        resp = client.post(
            "/api/prompts/security.input_guard/drafts",
            json=body,
            headers=AUTH,
        )
        assert resp.status_code == 403

    def test_medium_publish_allowed_with_credential(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.publish = AsyncMock(return_value={"active_version": 1})
            body = {"version": 1}
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json=body,
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_seed_requires_credential(self, client):
        """无凭据不得触发种子写入。"""
        resp = client.post(
            "/api/prompts/seed",
            json={"auto_seed": True},
        )
        assert resp.status_code == 401


class TestPermissionMatrix:
    """`_check_permission` 纯函数矩阵（S0-2 起权限语义的权威落点）。

    原先这些语义由「请求头传 editor/admin」来表达；角色头废除后请求级无法再构造
    「已认证但权限不足的 editor」，故在此直接单测矩阵 —— 覆盖度不降反升
    （含 viewer / 未知风险等级 等原先未覆盖的分支）。
    """

    @staticmethod
    def _check(risk_level: str, action: str, role: str):
        from backend.app.api.routes.prompts import _check_permission
        return _check_permission(risk_level, action, role)

    # critical：代码受控，任何角色都不可写
    @pytest.mark.parametrize("action", ["draft", "publish", "rollback", "transition"])
    @pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
    def test_critical_is_read_only(self, action, role):
        with pytest.raises(HTTPException) as e:
            self._check("critical", action, role)
        assert e.value.status_code == 403

    def test_critical_read_allowed(self):
        self._check("critical", "read", "viewer")

    # high：editor 可草稿，但发布/回滚/转换仅 admin
    def test_high_editor_can_draft(self):
        self._check("high", "draft", "editor")

    @pytest.mark.parametrize("action", ["publish", "rollback", "transition"])
    def test_high_editor_cannot_publish_rollback_transition(self, action):
        with pytest.raises(HTTPException) as e:
            self._check("high", action, "editor")
        assert e.value.status_code == 403

    @pytest.mark.parametrize("action", ["draft", "publish", "rollback", "transition"])
    def test_high_admin_can_all(self, action):
        self._check("high", action, "admin")

    # medium / low：editor 全权
    @pytest.mark.parametrize("risk", ["medium", "low"])
    @pytest.mark.parametrize("action", ["draft", "publish", "rollback", "transition"])
    def test_medium_low_editor_can_all(self, risk, action):
        self._check(risk, action, "editor")

    # viewer：仅读
    @pytest.mark.parametrize("risk", ["high", "medium", "low"])
    @pytest.mark.parametrize("action", ["draft", "publish", "rollback", "transition"])
    def test_viewer_read_only(self, risk, action):
        with pytest.raises(HTTPException) as e:
            self._check(risk, action, "viewer")
        assert e.value.status_code == 403

    @pytest.mark.parametrize("risk", ["critical", "high", "medium", "low"])
    def test_viewer_can_read(self, risk):
        self._check(risk, "read", "viewer")

    def test_unknown_risk_falls_back_to_low(self):
        """未知风险等级按 low 处理（与矩阵实现一致，防静默放行到高风险语义）。"""
        self._check("unknown", "publish", "editor")


class TestDiff:
    def test_diff_code_controlled_403(self, client):
        resp = client.get(
            "/api/prompts/security.input_guard/diff",
            params={"from_version": 1, "to_version": 2},
            headers=AUTH,
        )
        assert resp.status_code == 403
