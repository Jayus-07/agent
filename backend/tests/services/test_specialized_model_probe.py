"""专项模型协议适配器与探测契约。"""
from __future__ import annotations

import asyncio

from backend.services import specialized_model_adapters as adapters
from backend.services.specialized_model_probe import probe_specialized


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _Client:
    response = _Response(200, {"data": [{"embedding": [0.1, 0.2]}]})
    last_request: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *, headers, json):
        self.last_request = {"url": url, "headers": headers, "json": json}
        return self.response


def test_dashscope_embedding_adapter_builds_openai_compatible_request() -> None:
    request = adapters.get_adapter("dashscope_embedding").build_probe_request(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-test-placeholder",
        model_name="qwen3.7-text-embedding",
        options={"dimensions": 1024},
    )

    assert request.url.endswith("/embeddings")
    assert request.headers["Authorization"] == "Bearer sk-test-placeholder"
    assert request.body == {
        "model": "qwen3.7-text-embedding",
        "input": ["连接测试"],
        "dimensions": 1024,
        "encoding_format": "float",
    }


def test_dashscope_rerank_adapter_builds_native_request() -> None:
    request = adapters.get_adapter("dashscope_rerank").build_probe_request(
        base_url="https://dashscope.aliyuncs.com/api/v1",
        api_key="sk-test-placeholder",
        model_name="qwen3.7-text-rerank",
        options={},
    )

    assert request.url.endswith("/services/rerank/text-rerank/text-rerank")
    assert request.body["model"] == "qwen3.7-text-rerank"
    assert request.body["input"]["query"] == "连接测试"
    assert len(request.body["input"]["documents"]) == 2


def test_jina_rerank_adapter_builds_jina_request() -> None:
    request = adapters.get_adapter("jina_rerank").build_probe_request(
        base_url="https://api.example.com/v1",
        api_key="sk-test-placeholder",
        model_name="reranker-v1",
        options={},
    )

    assert request.url.endswith("/rerank")
    assert request.body["model"] == "reranker-v1"
    assert request.body["documents"] == ["候选文档一", "候选文档二"]


def test_probe_returns_elapsed_time_and_provider_error(monkeypatch) -> None:
    fake_client = _Client()
    monkeypatch.setattr(
        "backend.services.specialized_model_probe.httpx.AsyncClient",
        lambda **kwargs: fake_client,
    )
    fake_client.response = _Response(
        401,
        {"code": "InvalidApiKey", "message": "Invalid API-key provided."},
    )

    result = asyncio.run(
        probe_specialized(
            role="embedding",
            adapter="dashscope_embedding",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-test-placeholder",
            model_name="qwen3.7-text-embedding",
        )
    )

    assert result.ok is False
    assert result.status_code == 401
    assert result.elapsed_ms >= 0
    assert "InvalidApiKey" in result.detail
    assert "sk-test-placeholder" not in result.detail
