"""Tests for prompt status workflow — state machine + API + publish tightening."""
from __future__ import annotations

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


@pytest.fixture
def client():
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
                headers={"X-Operator-Role": "admin"},
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
                headers={"X-Operator-Role": "admin"},
            )
        assert resp.status_code == 422

    def test_transition_unknown_prompt_404(self, client):
        resp = client.post(
            "/api/prompts/nonexistent.key/versions/1/transition",
            json={"status": "testing"},
            headers={"X-Operator-Role": "admin"},
        )
        assert resp.status_code == 404

    def test_transition_high_risk_requires_admin(self, client):
        resp = client.post(
            "/api/prompts/planner.system/versions/1/transition",
            json={"status": "testing"},
            headers={"X-Operator-Role": "editor"},
        )
        assert resp.status_code == 403

    def test_transition_medium_risk_editor_allowed(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.transition_status = AsyncMock(
                return_value={"version": 1, "status": "testing"}
            )
            resp = client.post(
                "/api/prompts/rag.qa/versions/1/transition",
                json={"status": "testing"},
                headers={"X-Operator-Role": "editor"},
            )
        assert resp.status_code == 200


class TestPublishTightening:
    def test_publish_draft_status_rejected(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.publish = AsyncMock(
                side_effect=ValueError(
                    "Version 1 has status 'draft'; only 'passed' or 'published' versions can be published"
                )
            )
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json={"version": 1},
                headers={"X-Operator-Role": "admin"},
            )
        assert resp.status_code == 422

    def test_publish_passed_status_allowed(self, client):
        with patch("backend.app.api.routes.prompts.prompt_service") as mock_svc:
            mock_svc.publish = AsyncMock(return_value={"active_version": 1})
            resp = client.post(
                "/api/prompts/rag.qa/publish",
                json={"version": 1},
                headers={"X-Operator-Role": "admin"},
            )
        assert resp.status_code == 200
