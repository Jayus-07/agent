"""Prompt release API 状态和发布门禁测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


INTERNAL_TOKEN = "test-internal-token"
AUTH = {"X-Internal-Token": INTERNAL_TOKEN}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        "backend.config.messaging.AI_INTERNAL_TOKEN", INTERNAL_TOKEN, raising=False
    )
    from backend.app.api.routes.prompt_releases import router as release_router
    from backend.app.api.routes.prompts import router as prompts_router

    app = FastAPI()
    app.include_router(prompts_router, prefix="/api")
    app.include_router(release_router, prefix="/api")
    return TestClient(app)


def _release(**overrides):
    values = {
        "release_id": "rel-1",
        "prompt_key": "rag.qa",
        "version": 2,
        "status": "pending",
        "eval_suite": "pr_baseline",
        "dataset_provenance": {"suite": "pr_baseline", "version": "5.0.0"},
        "model_binding_fingerprint": "model-abc",
        "target_env": "production",
        "executor": "local",
        "metrics": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeReleaseService:
    def __init__(self, release=None):
        self.release = release or _release()
        self.published = False

    async def create_release(self, **_kwargs):
        return self.release

    async def list_releases(self, _key):
        return [self.release]

    async def list_all_releases(self, limit: int = 50):
        return [self.release]

    async def get(self, _release_id):
        return self.release

    async def get_approved_release(self, _key, _version):
        if self.release.status == "approved":
            return self.release
        return None

    async def approve(self, _release_id, _actor):
        self.release.status = "approved"
        return self.release

    async def publish(self, _release_id, _actor):
        self.published = True
        self.release.status = "published"
        return self.release


def test_publish_returns_409_before_release_passes(client):
    fake_service = _FakeReleaseService(_release(status="failed"))
    with patch(
        "backend.app.api.routes.prompt_releases.get_release_service",
        return_value=fake_service,
    ):
        response = client.post(
            "/api/prompts/rag.qa/publish",
            json={"version": 2},
            headers=AUTH,
        )

    assert response.status_code == 409
    assert response.json()["code"] == "PROMPT_RELEASE_GATE_BLOCKED"
    assert fake_service.published is False


def test_release_list_exposes_dataset_and_model_provenance(client):
    fake_service = _FakeReleaseService()
    with patch(
        "backend.app.api.routes.prompt_releases.get_release_service",
        return_value=fake_service,
    ):
        response = client.get(
            "/api/prompts/rag.qa/releases",
            headers=AUTH,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["dataset_provenance"]["suite"] == "pr_baseline"
    assert body["items"][0]["model_binding_fingerprint"] == "model-abc"


def test_create_release_returns_pending_record(client):
    fake_service = _FakeReleaseService()
    with patch(
        "backend.app.api.routes.prompt_releases.get_release_service",
        return_value=fake_service,
    ):
        response = client.post(
            "/api/prompts/rag.qa/versions/2/release",
            json={
                "suite": "pr_baseline",
                "dataset_version": {"version": "5.0.0"},
                "executor": "local",
            },
            headers=AUTH,
        )

    assert response.status_code == 202
    assert response.json()["status"] == "pending"


def test_approved_release_can_publish_through_release_endpoint(client):
    fake_service = _FakeReleaseService(_release(status="approved"))
    with patch(
        "backend.app.api.routes.prompt_releases.get_release_service",
        return_value=fake_service,
    ):
        response = client.post(
            "/api/prompts/rag.qa/releases/rel-1/publish",
            headers=AUTH,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert fake_service.published is True


def test_github_release_has_durable_backend_poller():
    from backend.app.api.routes.prompt_releases import _enqueue_github_poll

    calls: list[str] = []

    def enqueue(release_id: str):
        calls.append(release_id)
        return "celery-task-1"

    with patch(
        "backend.tasks.prompt_eval_tasks.enqueue_github_prompt_eval_poll",
        side_effect=enqueue,
    ):
        task_id = _enqueue_github_poll("rel-1")

    assert task_id == "celery-task-1"
    assert calls == ["rel-1"]


def test_release_request_defaults_to_github_executor():
    from backend.app.api.routes.prompt_releases import CreateReleaseRequest

    assert CreateReleaseRequest(suite="pr_smoke").executor == "github"

def test_approve_with_foreign_key_does_not_mutate(client, monkeypatch):
    """审计 #58：归属校验先于状态迁移——错误 key 返回 404 且状态不变。"""
    from backend.app.api.routes import prompt_releases as route

    monkeypatch.setattr(route, "_check_permission", lambda *_a, **_k: None)
    fake_service = _FakeReleaseService(_release(status="passed", prompt_key="key-b"))
    with patch(
        "backend.app.api.routes.prompt_releases.get_release_service",
        return_value=fake_service,
    ):
        response = client.post(
            "/api/prompts/key-a/releases/rel-1/approve",
            headers=AUTH,
        )

    assert response.status_code == 404
    assert fake_service.release.status == "passed"


class TestListAllReleases:
    """跨 Prompt 发布评测列表（评测中心「运行记录」聚合视图）。

    权限门槛 = is_platform_admin（admin/super_admin）。JWT 通道的集成
    验证依赖 identity 落库（测试环境无 DB），此处以服务凭据通道 +
    纯函数矩阵覆盖；editor 被拒的语义由 is_platform_admin 保证。
    """

    def test_internal_token_can_list_all(self, client):
        fake_service = _FakeReleaseService(_release(status="published"))
        with patch(
            "backend.app.api.routes.prompt_releases.get_release_service",
            return_value=fake_service,
        ):
            response = client.get("/api/prompts/releases/all", headers=AUTH)

        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["release_id"] == "rel-1"
        assert items[0]["prompt_key"] == "rag.qa"
        assert items[0]["status"] == "published"

    def test_platform_admin_gate_matrix(self):
        from backend.app.api.deps import is_platform_admin

        assert is_platform_admin("super_admin") is True
        assert is_platform_admin("admin") is True
        assert is_platform_admin("editor") is False
        assert is_platform_admin("viewer") is False

    def test_editor_role_forbidden_by_gate(self, client):
        """编辑角色（无平台管理员）访问聚合列表被 403 拒绝。

        用 dependency_overrides 注入 editor 身份，绕开 JWT 通道的 DB
        依赖，只验证路由层的权限门槛本身。
        """
        from backend.app.api.deps import OperatorIdentity
        from backend.app.api.routes import prompt_releases as route

        client.app.dependency_overrides[route.resolve_operator_role] = (
            lambda: OperatorIdentity(role="editor", actor="user:9")
        )
        try:
            response = client.get("/api/prompts/releases/all", headers=AUTH)
        finally:
            client.app.dependency_overrides.clear()

        assert response.status_code == 403
