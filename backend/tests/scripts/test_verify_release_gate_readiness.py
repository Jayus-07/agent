"""发布门禁应检查真实运行结果，并安全展示失败原因。"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.scripts import verify_release_gate as gate


def test_chat_captures_safe_error_code_and_requires_done(monkeypatch):
    body = (
        'event: error\ndata: {"code":"UPSTREAM_AUTH_FAILED",'
        '"message":"不得进入门禁详情"}\n\n'
    )
    monkeypatch.setattr(gate, "_request", lambda *_args, **_kwargs: (200, body))

    status, answer, nodes, error_code, done = gate.chat("token", "s", "q")

    assert (status, answer, nodes, error_code, done) == (
        200,
        "",
        [],
        "UPSTREAM_AUTH_FAILED",
        False,
    )


def test_chat_non_200_returns_complete_result_shape(monkeypatch):
    monkeypatch.setattr(gate, "_request", lambda *_args, **_kwargs: (503, ""))

    assert gate.chat("token", "s", "q") == (503, "", [], "", False)


def test_domain_gate_records_error_code_without_error_message(monkeypatch):
    monkeypatch.setattr(
        gate,
        "chat",
        lambda *_args, **_kwargs: (
            200,
            "",
            [],
            "UPSTREAM_AUTH_FAILED",
            False,
        ),
    )
    gate.DETAIL.clear()

    assert gate.g_domain("token", "g4", "q", None) is False
    assert "error=UPSTREAM_AUTH_FAILED" in gate.DETAIL["g4"]
    assert "不得进入门禁详情" not in gate.DETAIL["g4"]


def test_task_runtime_gate_requires_live_worker_and_agent_queue(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"workers": 2, "agent_queue": True}),
            stderr="",
        )

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    gate.DETAIL.clear()

    assert gate.g10_task() is True
    assert captured["command"][:4] == ["docker", "exec", "agent-app-1", "python"]
    assert captured["kwargs"]["timeout"] == 30
    assert gate.DETAIL["g10"] == "workers_online=2 agent_queue=True"


def test_worker_probe_code_is_valid_python():
    compile(gate._celery_worker_probe_code(), "release-worker-probe", "exec")


def test_task_runtime_gate_fails_closed_without_agent_queue(monkeypatch):
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"workers": 2, "agent_queue": False}),
            stderr="",
        ),
    )
    gate.DETAIL.clear()

    assert gate.g10_task() is False
    assert gate.DETAIL["g10"] == "workers_online=2 agent_queue=False"


def test_model_gate_counts_only_recent_live_chat_probes():
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = [
        {
            "modelKind": "chat",
            "probeMode": "live_call",
            "status": "healthy",
            "lastCheckedAt": "2026-11-12T23:00:00Z",
        },
        {
            "modelKind": "chat",
            "probeMode": "live_call",
            "status": "healthy",
            "lastCheckedAt": "2026-11-11T22:00:00Z",
        },
        {
            "modelKind": "embedding",
            "probeMode": "live_call",
            "status": "healthy",
            "lastCheckedAt": "2026-11-12T23:00:00Z",
        },
        {
            "modelKind": "chat",
            "probeMode": "live_call",
            "status": "auth_failed",
            "lastCheckedAt": "2026-11-12T23:00:00Z",
        },
    ]

    assert gate._fresh_healthy_chat_models(rows, now=now) == 1


def test_model_gate_requires_fresh_healthy_chat_probe(monkeypatch):
    checked = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        gate,
        "_request",
        lambda *_args, **_kwargs: (
            200,
            json.dumps({"items": [{
                "role": "main",
                "effectiveModel": "active-chat-model",
                "available": True,
                "health": {
                    "probeMode": "live_call",
                    "status": "healthy",
                    "lastCheckedAt": checked,
                },
            }]}),
        ),
    )
    gate.DETAIL.clear()

    assert gate.g11_model("editor-token") is True
    assert "main_configured=True" in gate.DETAIL["g11"]
    assert "recent_live_probe=True" in gate.DETAIL["g11"]


def test_model_gate_rejects_healthy_unbound_model(monkeypatch):
    checked = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        gate,
        "_request",
        lambda *_args, **_kwargs: (
            200,
            json.dumps({"items": [
                {
                    "role": "main",
                    "effectiveModel": "active-chat-model",
                    "available": True,
                    "health": {
                        "probeMode": "live_call",
                        "status": "auth_failed",
                        "lastCheckedAt": checked,
                    },
                },
                {
                    "role": "fallback",
                    "effectiveModel": "unbound-healthy-model",
                    "available": True,
                    "health": {
                        "probeMode": "live_call",
                        "status": "healthy",
                        "lastCheckedAt": checked,
                    },
                },
            ]}),
        ),
    )
    gate.DETAIL.clear()

    assert gate.g11_model("editor-token") is False
    assert "recent_live_probe=False" in gate.DETAIL["g11"]


def test_observability_gate_uses_configured_prometheus_url(monkeypatch):
    seen_urls = []

    def fake_request(_method, url, **_kwargs):
        seen_urls.append(url)
        if url.endswith("/metrics"):
            return 200, "routing_domain_total 1\nchat_request_total 1\n"
        return 200, json.dumps({
            "data": {"activeTargets": [{"health": "up"}]}
        })

    monkeypatch.setenv("PROMETHEUS_URL", "https://metrics.example/prom/")
    monkeypatch.setattr(gate, "_request", fake_request)
    gate.DETAIL.clear()

    assert gate.g12_observability() is True
    assert seen_urls[-1] == (
        "https://metrics.example/prom/api/v1/targets?state=active"
    )
    assert "metrics.example" not in gate.DETAIL["g12"]


def test_observability_gate_rejects_invalid_prometheus_url(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "file:///tmp/metrics")
    monkeypatch.setattr(
        gate,
        "_request",
        lambda *_args, **_kwargs: (200, "routing_domain_total chat_request_total"),
    )
    gate.DETAIL.clear()

    assert gate.g12_observability() is False
    assert gate.DETAIL["g12"] == "metrics=200 prometheus_configured=false"
