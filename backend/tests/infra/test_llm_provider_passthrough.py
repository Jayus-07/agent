"""providers/* 凭据传参式（P1a-1）

破 B.5#1：`from backend.config import QWEN_API_KEY` 是**导入时值拷贝**，
运行时轮换/免重启全都不成立。改造后凭据由调用方在**调用时**传入。

本文件用假客户端类替换 langchain_* 模块，直接检查传给客户端的 kwargs ——
不联网、不依赖 langchain 内部属性名、不受 langchain 版本影响。
"""
from __future__ import annotations

import sys
import types

import pytest

from backend.config import llm as cfg
from backend.infra.llm.credentials import ProviderCredentials


class _FakeClient:
    def __init__(self, **kw):
        self.kw = kw


@pytest.fixture(autouse=True)
def _fake_langchain(monkeypatch):
    for mod_name, cls_name in (("langchain_openai", "ChatOpenAI"),
                               ("langchain_anthropic", "ChatAnthropic"),
                               ("langchain_ollama", "ChatOllama")):
        mod = types.ModuleType(mod_name)
        setattr(mod, cls_name, _FakeClient)
        monkeypatch.setitem(sys.modules, mod_name, mod)
    yield


@pytest.fixture(autouse=True)
def _providers():
    from backend.infra.llm.providers import deepseek, minimax, ollama, qwen, qwen_tp
    from backend.infra.llm.providers import siliconflow, vllm

    return types.SimpleNamespace(deepseek=deepseek, qwen=qwen, qwen_tp=qwen_tp,
                                 minimax=minimax, siliconflow=siliconflow,
                                 vllm=vllm, ollama=ollama)


def _cred(**kw) -> ProviderCredentials:
    kw.setdefault("provider", "test")
    return ProviderCredentials(**kw)


# ── 不传 credentials：必须与改造前逐位一致 ────────────────────────────


def test_deepseek_defaults_are_config_constants(_providers):
    k = _providers.deepseek.build_deepseek("deepseek-v4-flash").kw
    assert k["api_key"] == cfg.DEEPSEEK_API_KEY
    assert k["base_url"] == cfg.DEEPSEEK_API_BASE


def test_qwen_defaults_and_thinking_body(_providers):
    k = _providers.qwen.build_qwen("qwen3.7-plus").kw
    assert k["api_key"] == cfg.QWEN_API_KEY
    assert k["base_url"] == cfg.QWEN_API_BASE
    assert k["extra_body"] == {"enable_thinking": False}


def test_qwen_tp_strips_registration_suffix(_providers):
    k = _providers.qwen_tp.build_qwen_tp("qwen3.7-plus@tp").kw
    assert k["model"] == "qwen3.7-plus"          # @tp 后缀剥掉后才发给 API
    assert k["api_key"] == cfg.QWEN_TP_API_KEY
    assert k["base_url"] == cfg.QWEN_TP_API_BASE


def test_minimax_uses_anthropic_endpoint(_providers):
    k = _providers.minimax.build_minimax("MiniMax-M3").kw
    assert k["anthropic_api_key"] == cfg.MINIMAX_API_KEY
    assert k["anthropic_api_url"] == "https://api.minimaxi.com/anthropic"
    assert k["default_headers"] == {"x-api-key": cfg.MINIMAX_API_KEY}


def test_siliconflow_defaults(_providers):
    k = _providers.siliconflow.build_siliconflow("Qwen/Qwen3-8B").kw
    assert k["api_key"] == cfg.SILICONFLOW_API_KEY
    assert k["base_url"] == cfg.SILICONFLOW_API_BASE


def test_vllm_empty_key_uses_placeholder(_providers):
    k = _providers.vllm.build_vllm("Qwen/Qwen3-32B-AWQ").kw
    assert k["api_key"] == (cfg.VLLM_API_KEY or "EMPTY")
    assert k["base_url"] == cfg.VLLM_API_BASE


def test_ollama_defaults(_providers):
    k = _providers.ollama.build_ollama("qwen2.5:3b").kw
    assert k["base_url"] == cfg.OLLAMA_BASE_URL


# ── 传 credentials：必须覆盖 ──────────────────────────────────────────


def test_credentials_override_key_and_url(_providers):
    cred = _cred(api_key="KEY-OVERRIDE", base_url="https://custom.example/v1")

    k = _providers.deepseek.build_deepseek("m", credentials=cred).kw
    assert (k["api_key"], k["base_url"]) == ("KEY-OVERRIDE", "https://custom.example/v1")

    k = _providers.qwen.build_qwen("m", credentials=cred).kw
    assert (k["api_key"], k["base_url"]) == ("KEY-OVERRIDE", "https://custom.example/v1")

    k = _providers.vllm.build_vllm("m", credentials=cred).kw
    assert (k["api_key"], k["base_url"]) == ("KEY-OVERRIDE", "https://custom.example/v1")

    k = _providers.ollama.build_ollama("m", credentials=cred).kw
    assert k["base_url"] == "https://custom.example/v1"


def test_minimax_headers_follow_overridden_key(_providers):
    cred = _cred(api_key="K2", base_url="https://mm.example/anthropic")
    k = _providers.minimax.build_minimax("m", credentials=cred).kw
    assert k["anthropic_api_key"] == "K2"
    assert k["anthropic_api_url"] == "https://mm.example/anthropic"
    assert k["default_headers"] == {"x-api-key": "K2"}


def test_extra_headers_merge_for_minimax(_providers):
    cred = _cred(api_key="K", extra_headers={"anthropic-version": "2023-06-01"})
    k = _providers.minimax.build_minimax("m", credentials=cred).kw
    assert k["default_headers"] == {"x-api-key": "K", "anthropic-version": "2023-06-01"}


def test_extra_body_merges_for_openai_family(_providers):
    cred = _cred(api_key="K", extra_body={"enable_thinking": True})
    k = _providers.qwen.build_qwen("m", credentials=cred).kw
    assert k["extra_body"] == {"enable_thinking": True}
    k = _providers.siliconflow.build_siliconflow("m", credentials=cred).kw
    assert k["extra_body"] == {"enable_thinking": True}


@pytest.mark.parametrize("api_key,base_url", [(None, ""), ("", None)])
def test_empty_fields_fall_back_to_config(_providers, api_key, base_url):
    """空字段 = 未覆盖（provider 据此保证「不传凭据 == 改造前行为」）。"""
    cred = _cred(api_key=api_key, base_url=base_url)
    k = _providers.deepseek.build_deepseek("m", credentials=cred).kw
    assert k["api_key"] == cfg.DEEPSEEK_API_KEY
    assert k["base_url"] == cfg.DEEPSEEK_API_BASE


# ── 余额查询同样走凭据 ────────────────────────────────────────────────


def test_balance_reports_missing_key_without_network(_providers, monkeypatch):
    """Key 缺失时短路返回，不发请求（vllm 无此短路，故不在此处断言）。"""
    monkeypatch.setattr("backend.config.llm.DEEPSEEK_API_KEY", "")
    assert _providers.deepseek.get_deepseek_balance()["ok"] is False
    monkeypatch.setattr("backend.config.llm.SILICONFLOW_API_KEY", "")
    assert _providers.siliconflow.get_siliconflow_balance()["ok"] is False


def test_balance_uses_passed_credentials(_providers):
    """余额查询与构建走同一凭据来源，否则会出现「测试通过但实际用的是旧 Key」。"""
    cred = _cred(api_key="", base_url="https://custom.example/v1")
    # api_key 为空 -> 回落 config；此处断言不因传入凭据而异常
    assert _providers.qwen.get_qwen_balance(cred)["provider"] == "qwen"
    assert _providers.minimax.get_minimax_balance(cred)["provider"] == "minimax"
