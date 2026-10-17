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
        lambda name: {
            "provider": "dashscope",
            "model_kind": "ocr",
            "upstream_name": "qwen-vl-max-upstream",
        }
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
        "upstream_model": "qwen-vl-max-upstream",
        "api_key": "configured",
        "base_url": "https://example.invalid/v1",
    }


def test_db_ocr_request_uses_upstream_name_but_keeps_registry_name_for_billing(
    monkeypatch,
):
    calls = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type(
                "Response",
                (),
                {
                    "model": "upstream-ocr-b",
                    "usage": type(
                        "Usage", (), {"prompt_tokens": 2, "completion_tokens": 3}
                    )(),
                    "choices": [
                        type(
                            "Choice",
                            (),
                            {"message": type("Message", (), {"content": "text"})()},
                        )(),
                    ],
                },
            )()

    class _Client:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setitem(
        __import__("sys").modules,
        "openai",
        type("OpenAIModule", (), {"OpenAI": _Client}),
    )
    monkeypatch.setattr(
        ocr,
        "_resolve_ocr_runtime_config",
        lambda: {
            "provider": "provider-a",
            "model": "registry-model-a",
            "upstream_model": "upstream-ocr-b",
            "api_key": "test-key",
            "base_url": "https://provider.example/v1",
        },
    )
    usage = []
    monkeypatch.setattr(ocr, "_record_ocr_usage", lambda *args, **kwargs: usage.append((args, kwargs)))

    assert ocr._ocr_image_dashscope(b"png") == "text"
    assert calls[0]["model"] == "upstream-ocr-b"
    assert usage[0][0][0] == "registry-model-a"
