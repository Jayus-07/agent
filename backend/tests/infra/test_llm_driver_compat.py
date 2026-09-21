"""infra/llm/providers/driver_compat.py + proxy/factory 分发表收口（2026-09-22）

回归背景：custom-* 自建供应商模型此前在 proxy 落 ollama 兜底 —— 拿 OpenAI
兼容地址打 /api/chat（Ollama 协议路径）→ 上游 404 → 健康页归因「模型不存在」
（实测 ``ResponseError: (status code: 404)``）；factory 则直接 raise「未知
provider」。表现为「供应商页测试通过、角色页健康失败、线上调用必挂」。

收口后：provider 不在内置分发表时按登记 driver（openai | anthropic | ollama）
构建，与供应商页测试（provider_probe.build_probe_client）同一口径。
"""
from __future__ import annotations

import pytest

from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.infra.llm import proxy as proxy_mod
from backend.infra.llm.credentials import ProviderCredentials
from backend.infra.llm.factory import get_llm_factory
from backend.infra.llm.providers.driver_compat import build_by_driver

PROVIDER_ID = "custom-ws-test"
BASE_URL = "https://ws-test.example/compatible-mode/v1"

PROVIDERS = [
    {"id": PROVIDER_ID, "driver": "openai", "display_name": "测试 MaaS"},
]
MODELS = [
    {"name": "qwen3.5-ocr", "provider": PROVIDER_ID, "model_kind": "chat", "source": "db"},
]


@pytest.fixture(autouse=True)
def _seeded():
    """注入自建供应商 + 模型 + DB 凭据（模拟 registry_store 刷新后的进程内状态）。"""
    models_mod.reset_dynamic_models_for_tests()
    models_mod.set_dynamic_models([dict(m) for m in MODELS])
    models_mod.set_dynamic_providers([dict(p) for p in PROVIDERS])
    credentials_mod.reset_credentials_for_tests()
    credentials_mod.set_db_credentials({
        PROVIDER_ID: ProviderCredentials(
            provider=PROVIDER_ID, api_key="sk-test",
            base_url=BASE_URL, source="db", version=1,
        ),
    })
    factory = get_llm_factory()
    factory.invalidate()
    yield
    models_mod.reset_dynamic_models_for_tests()
    credentials_mod.reset_credentials_for_tests()
    factory.invalidate()


# ── proxy：真实调用链（健康探测 / 线上聊天同路径）───────────────────────


def test_proxy_builds_openai_client_for_custom_provider():
    """custom-* + driver=openai → ChatOpenAI，地址与 Key 来自 DB 凭据。"""
    from langchain_openai import ChatOpenAI

    llm = proxy_mod._build_llm_for("qwen3.5-ocr")

    assert isinstance(llm, ChatOpenAI)
    assert BASE_URL in str(getattr(llm, "openai_api_base", "") or llm.root_client.base_url)


def test_unregistered_model_still_falls_back_to_ollama():
    """注册表未登记的模型名（本地 ollama 语义）维持历史兜底，不回归。"""
    models_mod.reset_dynamic_models_for_tests()

    llm = proxy_mod._build_llm_for("some-local-model:3b")

    assert type(llm).__name__ == "ChatOllama"


# ── factory：缓存构建路径 ────────────────────────────────────────────────


def test_factory_builds_openai_client_for_custom_provider():
    from langchain_openai import ChatOpenAI

    llm = get_llm_factory()._build_instance("qwen3.5-ocr")

    assert isinstance(llm, ChatOpenAI)
    assert BASE_URL in str(getattr(llm, "openai_api_base", "") or llm.root_client.base_url)


def test_factory_unknown_provider_without_driver_still_raises():
    """driver 未登记的未知 provider 维持 raise（fail-fast 语义不放宽）。

    注意 premise：模型必须**已登记**但其 provider 没有元数据行 —— 未登记的
    模型名走 resolve_provider 宽松模式会判成 ollama，根本到不了 raise 分支。
    """
    models_mod.set_dynamic_models([
        {"name": "m-orphan", "provider": "custom-nodriver", "model_kind": "chat", "source": "db"},
    ])
    models_mod.set_dynamic_providers([
        {"id": "custom-nodriver", "display_name": "无驱动登记的供应商"},
    ])
    credentials_mod.set_db_credentials({
        "custom-nodriver": ProviderCredentials(provider="custom-nodriver", source="db"),
    })
    get_llm_factory().invalidate()
    try:
        with pytest.raises(ValueError, match="未知 provider"):
            get_llm_factory()._build_instance("m-orphan")
    finally:
        get_llm_factory().invalidate()


# ── build_by_driver 单元 ─────────────────────────────────────────────────


def test_build_by_driver_openai_carries_credentials_and_extras():
    from langchain_openai import ChatOpenAI

    creds = ProviderCredentials(
        provider=PROVIDER_ID, api_key="sk-x", base_url=BASE_URL,
        extra_headers={"X-Custom": "1"},
        extra_body={"enable_thinking": False},
        source="db",
    )
    llm = build_by_driver("openai", "m1", creds)

    assert isinstance(llm, ChatOpenAI)
    assert llm.extra_body == {"enable_thinking": False}


def test_build_by_driver_anthropic():
    pytest.importorskip("langchain_anthropic")
    from langchain_anthropic import ChatAnthropic

    creds = ProviderCredentials(
        provider="mini-x", api_key="sk-a",
        base_url="https://api.example.com/anthropic", source="db",
    )
    llm = build_by_driver("anthropic", "MiniMax-M3", creds)

    assert isinstance(llm, ChatAnthropic)


def test_build_by_driver_unsupported_raises():
    with pytest.raises(ValueError, match="不支持的协议驱动"):
        build_by_driver("specialized", "m1", None)


def test_build_by_driver_ollama():
    llm = build_by_driver("ollama", "qwen2.5:3b", None)

    assert type(llm).__name__ == "ChatOllama"
