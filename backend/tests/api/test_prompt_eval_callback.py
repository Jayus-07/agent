"""Prompt 评测 GitHub/外部回调 API 测试。"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus


SECRET = "callback-secret"


@dataclass
class _CallbackService:
    release: PromptReleaseRecord
    record_calls: int = 0

    async def get(self, release_id: str) -> PromptReleaseRecord:
        if release_id != self.release.release_id:
            raise KeyError(release_id)
        return self.release

    async def record_result(
        self, release_id: str, result: dict[str, Any], actor: str
    ) -> PromptReleaseRecord:
        self.record_calls += 1
        self.release = PromptReleaseRecord(
            **{
                **self.release.__dict__,
                "status": PromptReleaseStatus(result["status"]),
                "eval_run_id": str(result.get("eval_run_id") or ""),
            }
        )
        return self.release


def _release(status: PromptReleaseStatus = PromptReleaseStatus.RUNNING):
    return PromptReleaseRecord(
        release_id="rel-1",
        prompt_key="rag.qa",
        version=2,
        status=status,
        eval_suite="pr_baseline",
        external_run_id="github-run-1",
    )


@pytest.fixture
def callback_service():
    return _CallbackService(_release())


@pytest.fixture
def client(monkeypatch, callback_service):
    monkeypatch.setenv("PROMPT_EVAL_CALLBACK_SECRET", SECRET)
    from backend.app.api.routes.prompt_eval_callback import router

    app = FastAPI()
    app.include_router(router)
    with patch(
        "backend.app.api.routes.prompt_eval_callback.get_release_service",
        return_value=callback_service,
    ):
        yield TestClient(app)


def _signed_callback(payload: dict[str, Any], *, timestamp: int | None = None):
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ts = str(timestamp or int(time.time()))
    digest = hmac.new(SECRET.encode(), ts.encode() + b"." + raw, hashlib.sha256)
    return {
        "content": raw,
        "headers": {
            "Content-Type": "application/json",
            "X-Prompt-Eval-Timestamp": ts,
            "X-Prompt-Eval-Signature": f"sha256={digest.hexdigest()}",
        },
    }


def _payload(status: str = "passed"):
    return {
        "release_id": "rel-1",
        "external_run_id": "github-run-1",
        "eval_run_id": "eval-1",
        "status": status,
        "metrics": {"pass_rate": 0.99},
    }


def test_callback_rejects_invalid_signature(client):
    body = json.dumps(_payload(), separators=(",", ":")).encode()
    response = client.post(
        "/internal/prompt-evals/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Prompt-Eval-Timestamp": str(int(time.time())),
            "X-Prompt-Eval-Signature": "sha256=bad",
        },
    )

    assert response.status_code == 401


def test_duplicate_pass_callback_is_idempotent(client, callback_service):
    signed = _signed_callback(_payload())

    first = client.post("/internal/prompt-evals/callback", **signed)
    second = client.post("/internal/prompt-evals/callback", **signed)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "passed"
    assert second.json()["status"] == "passed"
    assert callback_service.record_calls == 1


def test_callback_rejects_stale_timestamp(client):
    signed = _signed_callback(_payload(), timestamp=int(time.time()) - 1000)

    response = client.post("/internal/prompt-evals/callback", **signed)

    assert response.status_code == 401


def test_callback_rejects_wrong_external_run_id(client):
    payload = _payload()
    payload["external_run_id"] = "github-run-other"

    response = client.post(
        "/internal/prompt-evals/callback", **_signed_callback(payload)
    )

    assert response.status_code == 409
