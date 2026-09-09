"""Prompt API integration tests (httpx + FastAPI TestClient).

Tests the REST endpoints with mocked DB layer.
"""
import pytest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.app.api.routes.prompts import router as prompts_router
    app = FastAPI()
    app.include_router(prompts_router, prefix="/api")
    return TestClient(app)


class TestGetRegistry:
    def test_returns_all_specs(self, client):
        resp = client.get("/api/prompts/meta/registry")
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
                resp = client.get("/api/prompts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestGetPrompt:
    def test_unknown_key_404(self, client):
        resp = client.get("/api/prompts/nonexistent.key")
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
                    resp = client.get("/api/prompts/memory.trigger")

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
        resp = client.post("/api/prompts/memory.trigger/render", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Hello World!"
        assert data["source"] == "playground"

    def test_render_missing_vars_422(self, client):
        body = {
            "variables": {},
            "template": "Hello {name}!",
        }
        resp = client.post("/api/prompts/memory.trigger/render", json=body)
        assert resp.status_code == 422


class TestPermissions:
    def test_critical_draft_403(self, client):
        body = {"template": "evil", "change_note": ""}
        resp = client.post(
            "/api/prompts/security.input_guard/drafts",
            json=body,
            headers={"X-Operator-Role": "editor"},
        )
        assert resp.status_code == 403

    def test_high_publish_requires_admin(self, client):
        body = {"version": 1}
        resp = client.post(
            "/api/prompts/planner.system/publish",
            json=body,
            headers={"X-Operator-Role": "editor"},
        )
        assert resp.status_code == 403

    def test_medium_publish_editor_allowed(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.publish = AsyncMock(return_value={"active_version": 1})
            body = {"version": 1}
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json=body,
                headers={"X-Operator-Role": "editor"},
            )
        assert resp.status_code == 200

    def test_seed_requires_admin(self, client):
        resp = client.post(
            "/api/prompts/seed",
            json={"auto_seed": True},
            headers={"X-Operator-Role": "editor"},
        )
        assert resp.status_code == 403


class TestDiff:
    def test_diff_code_controlled_403(self, client):
        resp = client.get(
            "/api/prompts/security.input_guard/diff",
            params={"from_version": 1, "to_version": 2},
            headers={"X-Operator-Role": "admin"},
        )
        assert resp.status_code == 403
