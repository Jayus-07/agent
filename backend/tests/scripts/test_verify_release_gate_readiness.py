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
    seen_requests = []

    def fake_request(method, url, **kwargs):
        seen_requests.append((method, url, kwargs))
        if url.endswith("/metrics"):
            return 200, "routing_domain_total 1\nchat_request_total 1\n"
        return 200, json.dumps({
            "data": {"activeTargets": [{"health": "up"}]}
        })

    monkeypatch.setenv("PROMETHEUS_URL", "https://metrics.example/prom/")
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "prometheus-test-token")
    monkeypatch.setattr(gate, "_request", fake_request)
    gate.DETAIL.clear()

    assert gate.g12_observability() is True
    assert seen_requests[-1][0:2] == (
        "GET",
        "https://metrics.example/prom/api/v1/targets?state=active"
    )
    assert seen_requests[-1][2]["include_platform_api_key"] is False
    assert seen_requests[-1][2]["headers"] == {
        "Authorization": "Bearer prometheus-test-token"
    }
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


def test_observability_gate_rejects_prometheus_token_over_http(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://metrics.example")
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "prometheus-test-token")
    monkeypatch.setattr(
        gate,
        "_request",
        lambda *_args, **_kwargs: (
            200,
            "routing_domain_total chat_request_total",
        ),
    )
    gate.DETAIL.clear()

    assert gate.g12_observability() is False
    assert gate.DETAIL["g12"] == "metrics=200 prometheus_auth_requires_https=true"


def test_request_separates_platform_and_prometheus_authentication(monkeypatch):
    captured = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"ok"

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("API_KEY", "platform-secret")
    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)

    status, body = gate._request(
        "GET",
        "https://metrics.example/api/v1/targets",
        headers={"Authorization": "Bearer prometheus-secret"},
        include_platform_api_key=False,
        timeout=15,
    )

    assert (status, body) == (200, "ok")
    request, timeout = captured[0]
    assert timeout == 15
    assert request.get_header("Authorization") == "Bearer prometheus-secret"
    assert request.get_header("X-api-key") is None

    status, body = gate._request("GET", "http://127.0.0.1:8000/health")

    assert (status, body) == (200, "ok")
    assert captured[1][0].get_header("X-api-key") == "platform-secret"


def test_domain_gate_cs_entry_uses_customer_service_stream(monkeypatch):
    """Gate7 语义（2026-11-21）：客服域经专属入口验证。

    产品身份由注册路径决定：/chat/stream → public（域节点不可达），
    /chat/customer-service/stream → customer_service（域锁入口）。
    """
    captured = {}

    def fake_chat(token, session, question, **kwargs):
        captured["kwargs"] = kwargs
        return 200, "回答内容足够长可以过关", ["router", "cs_graph_node"], "", True

    monkeypatch.setattr(gate, "chat", fake_chat)
    gate.DETAIL.clear()

    assert gate.g_domain(
        "token", "g7", "帮我查一下订单物流", "cs_graph_node",
        cs_entry=True,
    ) is True
    assert captured["kwargs"]["cs_entry"] is True
    assert "entry=cs" in gate.DETAIL["g7"]


def test_chat_cs_entry_targets_customer_service_stream(monkeypatch):
    """cs_entry=True 时 URL 必须指向客服专属路径（产品身份靠路径判定）。"""
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        return 503, ""

    monkeypatch.setattr(gate, "_request", fake_request)

    gate.chat("token", "s", "q", cs_entry=True)

    assert captured["url"].endswith("/chat/customer-service/stream")


def test_chat_default_entry_targets_public_stream(monkeypatch):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        return 503, ""

    monkeypatch.setattr(gate, "_request", fake_request)

    gate.chat("token", "s", "q")

    assert captured["url"].endswith("/chat/stream")
    assert "customer-service" not in captured["url"]


def test_g8_travel_direct_requires_done_with_ok_status(monkeypatch):
    """Gate8 语义（2026-11-21）：旅游域经专属直达流验证。

    产品路由按路径判定（/chat/stream → public，域节点不可达）；Gate8
    改走旅游页现役 V2 规划流 /api/travel/v2/plan/stream（V1 /plan/stream
    已退役且整组路由未挂载，生产实测 404），断言 done 终帧且 status 非 failed。
    """
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["url"] = url
        return 200, (
            'event: ping\ndata: {"event":"ping"}\n\n'
            'event: done\ndata: {"event":"done","status":"success",'
            '"result":{"result_kind":"plan"}}\n\n'
        )

    monkeypatch.setattr(gate, "_request", fake_request)
    gate.DETAIL.clear()

    assert gate.g8_travel_direct("token") is True
    assert "status=success" in gate.DETAIL["g8"]
    assert captured["url"].endswith("/api/travel/v2/plan/stream")


def test_g8_travel_direct_fails_on_error_frame(monkeypatch):
    body = (
        'event: done\ndata: {"event":"done","status":"failed"}\n\n'
        'event: error\ndata: {"event":"error","error_type":"plan_failed"}\n\n'
    )
    monkeypatch.setattr(gate, "_request", lambda *_args, **_kwargs: (200, body))
    gate.DETAIL.clear()

    assert gate.g8_travel_direct("token") is False
    assert "error=plan_failed" in gate.DETAIL["g8"]


def test_g8_travel_direct_fails_without_done(monkeypatch):
    body = 'event: ping\ndata: {"event":"ping"}\n\n'
    monkeypatch.setattr(gate, "_request", lambda *_args, **_kwargs: (200, body))
    gate.DETAIL.clear()

    assert gate.g8_travel_direct("token") is False


def test_g8_travel_direct_fails_on_non_200(monkeypatch):
    monkeypatch.setattr(
        gate, "_request", lambda *_args, **_kwargs: (503, ""),
    )
    gate.DETAIL.clear()

    assert gate.g8_travel_direct("token") is False
    assert "http=503" in gate.DETAIL["g8"]
