"""Tests for prompt status workflow — state machine + API + publish tightening."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.prompts.workflow import (
    PROMPT_STATUSES,
    STATUS_INDEX,
    StatusTransitionError,
    validate_transition,
)
from backend.prompts.release_models import PromptReleaseStatus

# ── State machine unit tests ──────────────────────────────────


class TestValidateTransitionForward:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("draft", "testing"),
            ("testing", "evaluation"),
            ("evaluation", "passed"),
            ("passed", "published"),
            ("published", "archived"),
        ],
    )
    def test_forward_transitions_allowed(self, current, target):
        validate_transition(current, target)

    def test_full_pipeline_walk(self):
        for i in range(len(PROMPT_STATUSES) - 1):
            validate_transition(PROMPT_STATUSES[i], PROMPT_STATUSES[i + 1])


class TestValidateTransitionBackward:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("testing", "draft"),
            ("evaluation", "testing"),
            ("passed", "evaluation"),
        ],
    )
    def test_backward_transitions_allowed(self, current, target):
        validate_transition(current, target)

    @pytest.mark.parametrize(
        "current,target",
        [
            ("published", "passed"),
            ("archived", "published"),
            ("draft", "draft"),
        ],
    )
    def test_backward_or_terminal_blocked(self, current, target):
        with pytest.raises(StatusTransitionError):
            validate_transition(current, target)


class TestValidateTransitionIllegal:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("draft", "evaluation"),
            ("draft", "passed"),
            ("draft", "published"),
            ("draft", "archived"),
            ("testing", "published"),
            ("testing", "passed"),
            ("evaluation", "published"),
            ("published", "draft"),
            ("archived", "draft"),
            ("archived", "testing"),
        ],
    )
    def test_jump_transitions_blocked(self, current, target):
        with pytest.raises(StatusTransitionError):
            validate_transition(current, target)

    def test_same_status_rejected(self):
        for s in PROMPT_STATUSES:
            with pytest.raises(StatusTransitionError, match="same status"):
                validate_transition(s, s)

    def test_unknown_current_status(self):
        with pytest.raises(StatusTransitionError, match="Unknown status"):
            validate_transition("nonexistent", "draft")

    def test_unknown_target_status(self):
        with pytest.raises(StatusTransitionError, match="Unknown status"):
            validate_transition("draft", "nonexistent")


class TestStatusConstants:
    def test_all_statuses_indexed(self):
        assert set(STATUS_INDEX.keys()) == set(PROMPT_STATUSES)
        assert len(STATUS_INDEX) == len(PROMPT_STATUSES)

    def test_indices_sequential(self):
        for i, s in enumerate(PROMPT_STATUSES):
            assert STATUS_INDEX[s] == i


# ── API endpoint tests ────────────────────────────────────────

# 2026-09-15 S0-2：prompts 全部端点的角色改由
# `backend.app.api.deps.resolve_operator_role` 解析 —— 当前只认
# `X-Internal-Token` 服务凭据。原先用 `X-Operator-Role` 头传角色的写法已废除
# （该头是客户端自设头、可伪造，曾是越权发布高风险 Prompt 的入口）。
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


class TestTransitionAPI:
    def test_transition_success(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.transition_status = AsyncMock(
                return_value={"version": 1, "status": "testing"}
            )
            resp = client.post(
                "/api/prompts/rag.qa/versions/1/transition",
                json={"status": "testing"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "testing"

    def test_transition_invalid_status_422(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.transition_status = AsyncMock(
                side_effect=ValueError("Transition draft → published is not allowed")
            )
            resp = client.post(
                "/api/prompts/rag.qa/versions/1/transition",
                json={"status": "published"},
                headers=AUTH,
            )
        assert resp.status_code == 422

    def test_transition_unknown_prompt_404(self, client):
        resp = client.post(
            "/api/prompts/nonexistent.key/versions/1/transition",
            json={"status": "testing"},
            headers=AUTH,
        )
        assert resp.status_code == 404

    def test_transition_high_risk_denied_without_credential(self, client):
        """高风险转换在**无凭据**下必须拒绝（S0-2 后的边界）。

        注：原先本用例是「editor 角色被拒」。角色头已废除，请求级无法再表达
        「一个已认证但权限不足的 editor」；该权限矩阵语义改由
        `test_prompts_api.py::TestPermissionMatrix` 直接单测 `_check_permission` 锁定。
        """
        resp = client.post(
            "/api/prompts/planner.system/versions/1/transition",
            json={"status": "testing"},
        )
        assert resp.status_code == 401

    def test_old_spoofed_role_header_no_longer_grants_access(self, client):
        """回归锁：客户端自带 `X-Operator-Role: admin` 不再有任何作用。

        改造前：该头即角色来源，任意客户端可提权发布/回滚高风险 Prompt。
        改造后：无 X-Internal-Token 一律 401 —— 即便把角色头写到最满。
        """
        resp = client.post(
            "/api/prompts/rag.qa/versions/1/transition",
            json={"status": "testing"},
            headers={"X-Operator-Role": "admin"},
        )
        assert resp.status_code == 401, "伪造角色头必须已失效"

    def test_transition_medium_risk_service_credential_allowed(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.transition_status = AsyncMock(
                return_value={"version": 1, "status": "testing"}
            )
            resp = client.post(
                "/api/prompts/rag.qa/versions/1/transition",
                json={"status": "testing"},
                headers=AUTH,
            )
        assert resp.status_code == 200


class TestPublishTightening:
    def test_publish_without_approved_release_rejected(self, client):
        with patch(
            "backend.app.api.routes.prompt_releases.get_release_service"
        ) as get_service:
            get_service.return_value.get_approved_release = AsyncMock(
                return_value=None
            )
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json={"version": 1},
                headers=AUTH,
            )
        assert resp.status_code == 409
        assert resp.json()["code"] == "PROMPT_RELEASE_GATE_BLOCKED"

    def test_publish_passed_status_allowed(self, client):
        approved = SimpleNamespace(
            release_id="rel-workflow-1",
            version=1,
            status=PromptReleaseStatus.APPROVED,
        )
        published = SimpleNamespace(
            release_id="rel-workflow-1",
            version=1,
            status=PromptReleaseStatus.PUBLISHED,
        )
        with patch(
            "backend.app.api.routes.prompt_releases.get_release_service"
        ) as get_service:
            get_service.return_value.get_approved_release = AsyncMock(
                return_value=approved
            )
            get_service.return_value.publish = AsyncMock(return_value=published)
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json={"version": 1},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert resp.json() == {
            "active_version": 1,
            "release_id": "rel-workflow-1",
            "status": "published",
        }
