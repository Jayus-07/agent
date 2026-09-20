"""OCR 从供应商模型目录读取 URL 与独立凭据的回归测试。"""
from __future__ import annotations

from backend.infra.llm.credentials import ProviderCredentials
from backend.rag.preprocessing.parser import ocr


def test_registered_ocr_uses_db_provider_credentials(monkeypatch):
    monkeypatch.setattr(
        ocr.model_roles,
        "resolve_effective",
        lambda role: {"role": role, "source": "db", "value": "ocr-model"},
    )
    monkeypatch.setattr(
        ocr.models_mod,
        "get_model_entry",
        lambda name: {
            "name": name,
            "provider": "ocr-provider",
            "model_kind": "chat",
        },
    )
    monkeypatch.setattr(
        ocr.models_mod,
        "get_provider_entry",
        lambda provider: {
            "id": provider,
            "driver": "openai",
            "base_url": "https://ocr.example/v1",
        },
    )
    monkeypatch.setattr(
        ocr.credentials_mod,
        "resolve_credentials",
        lambda provider, model_name=None: ProviderCredentials(
            provider=provider,
            api_key="ocr-db-key",
            base_url="https://ocr.example/v1",
            source="db",
            version=3,
        ),
    )

    runtime = ocr._resolve_ocr_runtime_config()

    assert runtime["model"] == "ocr-model"
    assert runtime["provider"] == "ocr-provider"
    assert runtime["api_key"] == "ocr-db-key"
    assert runtime["base_url"] == "https://ocr.example/v1"


def test_db_ocr_role_takes_precedence_over_rapidocr(monkeypatch):
    monkeypatch.setattr(ocr.rag_cfg, "RAG_OCR_PROVIDER", "rapidocr")
    monkeypatch.setattr(
        ocr.model_roles,
        "resolve_effective",
        lambda role: {"role": role, "source": "db", "value": "ocr-model"},
    )
    monkeypatch.setattr(
        ocr,
        "_resolve_ocr_runtime_config",
        lambda: {
            "provider": "ocr-provider",
            "model": "ocr-model",
            "api_key": "ocr-db-key",
            "base_url": "https://ocr.example/v1",
        },
    )
    monkeypatch.setattr(ocr, "_ocr_image_cloud_cached", lambda payload: "db-ocr")
    monkeypatch.setattr(
        ocr,
        "_ocr_image_rapidocr",
        lambda payload: (_ for _ in ()).throw(AssertionError("不应走 RapidOCR")),
    )

    assert ocr.ocr_image(b"png") == "db-ocr"
