"""Release Gate 0 必须拒绝不可追溯或与发布版本不符的镜像。"""

import json

import pytest

from backend.scripts import verify_release_gate


@pytest.mark.parametrize(
    ("status", "build", "expected_result"),
    [
        (200, {"commit": "abc1234", "build_time": "2026-11-06T02:13:02Z", "dirty": False}, True),
        (200, {"commit": "abc1234", "build_time": "2026-11-06T02:13:02Z", "dirty": True}, False),
        (200, {"commit": "abc1234", "build_time": "2026-11-06T02:13:02Z", "dirty": None}, False),
        (200, {"commit": "abc1234", "build_time": "unknown", "dirty": False}, False),
        (200, {"commit": "other", "build_time": "2026-11-06T02:13:02Z", "dirty": False}, False),
        (200, {"commit": "abc1234", "build_time": "2026-11-06T02:13:02Z", "dirty": "false"}, False),
        (503, {}, False),
    ],
)
def test_gate_zero_requires_clean_exact_build_identity(
    monkeypatch,
    status,
    build,
    expected_result,
):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    monkeypatch.setattr(
        verify_release_gate,
        "_request",
        lambda *_args, **_kwargs: (status, json.dumps({"build": build})),
    )
    verify_release_gate.DETAIL.clear()

    assert verify_release_gate.g0_build() is expected_result


def test_gate_zero_rejects_non_json_health(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "abc1234")
    monkeypatch.setattr(
        verify_release_gate,
        "_request",
        lambda *_args, **_kwargs: (200, "not-json"),
    )

    assert verify_release_gate.g0_build() is False
