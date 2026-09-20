"""反馈与评测候选写接口的幂等门禁契约。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.app.api.routes import feedback as feedback_route


def _request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode())
        for key, value in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/feedback",
        "headers": raw_headers,
        "query_string": b"",
    })


def _identity(_request: Request) -> Identity:
    return Identity(user_id="user-1", tenant_id="tenant-1", source="header")


def _patch_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_route, "resolve_identity", _identity)
    monkeypatch.setattr("backend.app.api.identity.resolve_identity", _identity)


@pytest.mark.asyncio
async def test_feedback_write_requires_idempotency_key_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_identity(monkeypatch)
    monkeypatch.setattr(
        feedback_route,
        "add_feedback",
        lambda **_kwargs: calls.append("feedback") or 1,
    )

    with pytest.raises(HTTPException) as exc_info:
        await feedback_route.post_feedback(
            feedback_route.FeedbackRequest(
                session_id="session-1",
                vote="positive",
            ),
            _request(),
        )

    assert exc_info.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_candidate_review_requires_idempotency_key_before_side_effect(
    decision: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_identity(monkeypatch)
    monkeypatch.setattr(
        feedback_route,
        "transition_candidate",
        lambda *_args: calls.append(decision) or {"status": decision},
    )
    review = getattr(feedback_route, f"{decision}_feedback_candidate")

    with pytest.raises(HTTPException) as exc_info:
        await review(
            "candidate-1",
            feedback_route.CandidateReviewRequest(),
            _request(),
            OperatorIdentity(role="admin", actor="user:user-1"),
        )

    assert exc_info.value.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_candidate_promotion_requires_idempotency_key_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _patch_identity(monkeypatch)
    monkeypatch.setattr(
        feedback_route,
        "get_candidate",
        lambda *_args: calls.append("get") or {
            "candidate_id": "candidate-1",
            "tenant_id": "tenant-1",
            "trace_id": "trace-1",
            "status": "approved",
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        await feedback_route.promote_feedback_candidate(
            "candidate-1",
            _request(),
            OperatorIdentity(role="admin", actor="user:user-1"),
        )

    assert exc_info.value.status_code == 400
    assert calls == []
