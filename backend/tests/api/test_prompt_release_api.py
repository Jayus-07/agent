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
