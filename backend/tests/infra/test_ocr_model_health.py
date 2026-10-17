from __future__ import annotations

import pytest

from backend.infra.llm import health, models, registry_store
from backend.infra.llm.credentials import ProviderCredentials
from backend.services import provider_probe


@pytest.fixture
def ocr_registry(monkeypatch):
    entry = {
        "name": "ocr-model",
        "upstream_name": "vendor-ocr-model",
        "provider": "test-provider",
        "model_kind": "ocr",
    }
    provider = {
        "id": "test-provider",
        "driver": "openai",
        "base_url": "https://models.example/v1",
        "network_scope": "public",
        "extra_headers": {"x-default": "default-value"},
    }
    credentials = ProviderCredentials(
        provider="test-provider",
        api_key="test-secret",
        extra_headers={"x-custom": "custom-value"},
        source="db",
    )
    monkeypatch.setattr(models, "get_model_entry", lambda _name: entry)
    monkeypatch.setattr(models, "model_kind_of", lambda _entry: "ocr")
    monkeypatch.setattr(models, "get_provider_entry", lambda _provider: provider)
    monkeypatch.setattr(models, "get_provider_driver", lambda _provider: "openai")
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials",
        lambda _provider, model_name=None: credentials,
    )
    return entry, provider, credentials


@pytest.mark.asyncio
async def test_ocr_manual_probe_uses_registered_provider_and_safe_summary(
    monkeypatch, ocr_registry,
):
    _, _, _ = ocr_registry
    recorded = []
    calls = []

    async def fake_probe_provider(**kwargs):
        calls.append(kwargs)
        return provider_probe.ProbeResult(
            ok=True,
            blocked_at=None,
            steps=[
                provider_probe.ProbeStep(
                    level="L2",
                    status=provider_probe.STATUS_PASS,
                    summary="模型名可用",
                    detail="provider response",
                    elapsed_ms=21,
                ),
            ],
            summary="模型连通性通过",
        )

    monkeypatch.setattr(provider_probe, "probe_provider", fake_probe_provider)
    monkeypatch.setattr(health, "_record", lambda *args: recorded.append(args))

    result = await health.probe_ocr_model("ocr-model")

    assert result == {
        "status": "healthy",
        "latencyMs": 21,
        "error": "",
        "probeSource": "manual",
        "probeMode": "live_call",
    }
    assert calls == [{
        "driver": "openai",
        "base_url": "https://models.example/v1",
        "api_key": "test-secret",
        "model_name": "ocr-model",
        "upstream_model_name": "vendor-ocr-model",
        "network_scope": "public",
        "extra_headers": {
            "x-default": "default-value",
            "x-custom": "custom-value",
        },
        "model_kind": "ocr",
    }]
    assert recorded[0][3:7] == (
        "healthy", 21, "", "manual",
    )
    assert "test-secret" not in repr(result)


@pytest.mark.asyncio
async def test_ocr_manual_probe_maps_upstream_failure_without_persisting_raw_body(
    monkeypatch, ocr_registry,
):
    recorded = []

    async def fake_probe_provider(**_kwargs):
        return provider_probe.ProbeResult(
            ok=False,
            blocked_at="L2",
            steps=[
                provider_probe.ProbeStep(
                    level="L2",
                    status=provider_probe.STATUS_FAIL,
                    summary="Key 被上游拒绝；请核对 Key、额度和模型权限",
                    detail="raw response included test-secret",
                    reason=provider_probe.REASON_API_KEY,
                    elapsed_ms=34,
                ),
            ],
            summary="未通过",
        )

    monkeypatch.setattr(provider_probe, "probe_provider", fake_probe_provider)
    monkeypatch.setattr(health, "_record", lambda *args: recorded.append(args))

    result = await health.probe_ocr_model("ocr-model")

    assert result["status"] == "auth_failed"
    assert result["probeSource"] == "manual"
    assert "额度和模型权限" in result["error"]
    assert "raw response" not in result["error"]
    assert "test-secret" not in repr(result)
    assert "raw response" not in recorded[0][5]
    assert recorded[0][-1] == "manual"


def test_scheduled_probe_does_not_report_ocr_credentials_as_healthy(monkeypatch):
    entry = {
        "name": "ocr-model",
        "provider": "test-provider",
        "model_kind": "ocr",
    }
    recorded = []
    monkeypatch.setattr(models, "get_model_entry", lambda _name: entry)
    monkeypatch.setattr(models, "model_kind_of", lambda _entry: "ocr")
    monkeypatch.setattr(health, "_record", lambda *args: recorded.append(args))

    result = health.probe_model("ocr-model")

    assert result["status"] == "unknown"
    assert "不代表模型可调用" in result["error"]
    assert recorded[0][3] == "unknown"


def test_non_ocr_probe_returns_and_persists_safe_status_message(monkeypatch):
    recorded = []

    def raise_probe_error(_name):
        raise RuntimeError("404 upstream body includes bearer test-secret")

    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {
            "name": "chat-model",
            "provider": "provider-a",
            "model_kind": "chat",
        },
    )
    monkeypatch.setattr(models, "model_kind_of", lambda _entry: "chat")
    monkeypatch.setattr(health, "_probe_chat", raise_probe_error)
    monkeypatch.setattr(health, "_record", lambda *args: recorded.append(args))

    result = health.probe_model("chat-model", probe_source="manual")

    assert result["status"] == "model_not_found"
    assert "上游未找到" in result["error"]
    assert "test-secret" not in repr(result)
    assert recorded[0][5] == result["error"]
    assert "test-secret" not in repr(recorded)


@pytest.mark.parametrize("model_kind", ["vision", "speech"])
def test_vision_speech_probe_is_labeled_availability_only(monkeypatch, model_kind):
    recorded = []
    monkeypatch.setattr(
        models,
        "get_model_entry",
        lambda _name: {"provider": "provider-a", "model_kind": model_kind},
    )
    monkeypatch.setattr(models, "model_kind_of", lambda _entry: model_kind)
    monkeypatch.setattr(
        health,
        "_probe_availability_only",
        lambda _provider: None,
    )
    monkeypatch.setattr(health, "_record", lambda *args: recorded.append(args))

    result = health.probe_model("availability-model", probe_source="manual")

    assert result["status"] == "healthy"
    assert result["probeMode"] == "availability_only"
    assert recorded[0][3] == "healthy"


def test_periodic_scan_skips_ocr_models(monkeypatch):
    async def refresh_registry():
        return None

    monkeypatch.setattr(registry_store, "refresh_registry", refresh_registry)
    monkeypatch.setattr(models, "get_available_models", lambda: [{
        "name": "ocr-model",
        "provider": "test-provider",
        "model_kind": "ocr",
    }])
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials",
        lambda *_args, **_kwargs: pytest.fail("周期扫描不应读取 OCR 凭据"),
    )

    result = health.scan_all_models()

    assert result["results"] == {"ocr-model": "skip(manual-only)"}
