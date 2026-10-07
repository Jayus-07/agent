"""OCR DB 模型用途与运行时凭据解析回归测试。"""

from backend.rag.preprocessing.parser import ocr


def test_db_ocr_model_uses_registered_provider_credentials(monkeypatch):
    monkeypatch.setattr(
        ocr.model_roles,
        "resolve_effective",
        lambda role: {"source": ocr.model_roles.SOURCE_DB} if role == "ocr" else {},
    )
    monkeypatch.setattr(
        ocr,
        "_configured_ocr_model",
        lambda: "qwen-vl-max",
    )
    monkeypatch.setattr(
        ocr.models_mod,
        "get_model_entry",
        lambda name: {"provider": "dashscope", "model_kind": "ocr"}
        if name == "qwen-vl-max" else None,
    )
    monkeypatch.setattr(
        ocr.models_mod,
        "get_provider_entry",
        lambda provider: {"base_url": "https://example.invalid/v1"}
        if provider == "dashscope" else None,
    )
    monkeypatch.setattr(
        ocr.credentials_mod,
        "resolve_credentials",
        lambda provider, model_name=None: type(
            "Credentials", (), {"api_key": "configured", "base_url": ""}
        )(),
    )

    runtime = ocr._resolve_ocr_runtime_config()

    assert runtime == {
        "provider": "dashscope",
        "model": "qwen-vl-max",
        "api_key": "configured",
        "base_url": "https://example.invalid/v1",
    }
