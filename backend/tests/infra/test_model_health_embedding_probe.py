"""回归：定时健康扫描须能探测任意已登记 embedding 模型。

_probe_embedding 原实现只解析 embedding 角色绑定并拒绝非绑定模型
（RuntimeError → 一律误报 degraded），导致每 5 分钟的扫描把所有
非绑定 embedding 模型标成 degraded（2026-10-29 实测 qwen3.7-text-
embedding 连续 20h+ 假 degraded）。改为 registered_binding 按模型
派生绑定，与手动探测路径（specialized_model_probe）能力对齐。
"""
import pytest

import backend.infra.llm.health as health


class _FakeBinding:
    provider_id = "p1"
    model_name = "some-embedding"
    base_url = "http://fake/v1"


class _FakeCreds:
    api_key = "sk-test-placeholder"


class _FakeEmbeddings:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakeEmbeddings.last_kwargs = kwargs

    def embed_query(self, text):
        assert text == "ping"
        return [0.0]


def test_probe_embedding_supports_non_bound_registered_model(monkeypatch):
    """非绑定但已登记的模型走真实探测，不再抛 RuntimeError。"""
    captured = {}

    def fake_registered_binding(role, model_name):
        captured["role"], captured["model"] = role, model_name
        return _FakeBinding()

    monkeypatch.setattr(
        "backend.infra.llm.specialized.registered_binding", fake_registered_binding)
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials",
        lambda *a, **k: _FakeCreds())
    monkeypatch.setattr("langchain_openai.OpenAIEmbeddings", _FakeEmbeddings)

    health._probe_embedding("some-embedding")

    assert captured == {"role": "embedding", "model": "some-embedding"}
    assert _FakeEmbeddings.last_kwargs["model"] == "some-embedding"
    assert _FakeEmbeddings.last_kwargs["base_url"] == "http://fake/v1"


def test_probe_embedding_rejects_unregistered_model(monkeypatch):
    """未登记/供应商配置不完整的模型抛 ValueError（分类后仍为 degraded）。"""

    def fake_registered_binding(role, model_name):
        raise ValueError("模型未登记、用途不匹配或供应商配置不完整")

    monkeypatch.setattr(
        "backend.infra.llm.specialized.registered_binding", fake_registered_binding)

    with pytest.raises(ValueError):
        health._probe_embedding("no-such-model")
