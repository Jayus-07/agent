"""infra/llm/proxy.py —— 构建路径的凭据传参 + provider 判定统一（P1a-2 收口）

本文件守一条**结构性契约**：proxy 里每一个 `build_xxx` 调用都必须把凭据传下去。

为什么值得用 AST 单独守：这条链路被**静默丢弃过一次** —— `proxy._build_llm_for`
调 `build_xxx(model_name)` 不传凭据，而 `factory._build_instance` 传了。两条构建
路径分叉没有任何报错，症状是「管理端配了供应商与密钥、线上仍用 `.env`」，而
`infra/llm/__init__.py` 的 `get_llm` 恰好来自 proxy → 线上聊天走的正是那条无凭据
路径。排查成本极高（见 docs/model-config-admin-ui-design.md §15.5 结论 1）。

AST 断言的价值在于：未来任何人给分发表加分支时忘了传凭据，本测试立刻变红；
而普通的「打桩调用一次」测试只覆盖当时存在的分支。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from backend.infra.llm import proxy as proxy_mod

_PROXY_SRC = Path(inspect.getfile(proxy_mod))


def _provider_build_calls() -> list[ast.Call]:
    """proxy 模块里所有 `build_xxx(...)` 调用节点。"""
    tree = ast.parse(_PROXY_SRC.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("build_")
    ]


# ── 结构性契约 ────────────────────────────────────────────────────────


def test_every_provider_build_call_passes_credentials():
    """所有 `build_xxx(model_name, credentials)` 必须是两参调用。

    少传凭据 = 该 provider 的 DB 覆盖层（管理端配置）被静默忽略。
    """
    calls = _provider_build_calls()
    assert calls, "未找到任何 build_* 调用 —— 选择器失效，请先修测试自身"
    missing = [
        f"  line {c.lineno}: {c.func.id}(...) 只传了 {len(c.args)} 个位置参数"
        for c in calls
        if len(c.args) < 2
    ]
    assert not missing, (
        "proxy 的 provider 构建调用丢失了凭据入参（会导致管理端配置的密钥失效）:\n"
        + "\n".join(missing)
    )


def test_proxy_dispatch_covers_every_known_provider():
    """分发表覆盖 `models.PROVIDERS` 里的每个 provider。

    历史事故：`qwen_tp`(2026-09-17) 与 `vllm`(2026-09-19) 都被漏在分发表外，
    选中后落到末尾的 ollama 兜底、报一个与真因无关的 Ollama 连接错误。
    """
    from backend.infra.llm import models as models_mod

    src = _PROXY_SRC.read_text(encoding="utf-8")
    absent = [p for p in models_mod.PROVIDERS if f'provider == "{p}"' not in src]
    # ollama 走末尾兜底分支，不写显式 `provider == "ollama"` 判断
    absent = [p for p in absent if p != "ollama"]
    assert not absent, f"proxy 分发表缺少 provider 分支: {absent}（会落到 ollama 兜底）"


# ── 凭据确实被解析并转发 ──────────────────────────────────────────────


def test_build_llm_for_forwards_resolved_credentials(monkeypatch):
    """`_build_llm_for` 必须把 resolve_credentials 的结果**原样**传给构建器。"""
    sentinel = object()
    seen: dict = {}

    def fake_build(model_name, credentials=None):
        seen["model"] = model_name
        seen["credentials"] = credentials
        return "FAKE_LLM"

    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _m: "deepseek")
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials",
        lambda provider, *, model_name=None: sentinel,
    )
    monkeypatch.setattr(
        "backend.infra.llm.providers.deepseek.build_deepseek", fake_build
    )

    assert proxy_mod._build_llm_for("any-model") == "FAKE_LLM"
    assert seen["credentials"] is sentinel, "凭据未透传 → DB 覆盖层会被静默忽略"
    assert seen["model"] == "any-model"


def test_credentials_resolution_uses_resolved_provider(monkeypatch):
    """凭据按「解析出的 provider」取，而不是按模型名猜。"""
    seen: dict = {}

    def fake_resolve(provider, *, model_name=None):
        seen["provider"] = provider
        seen["model_name"] = model_name
        return None

    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _m: "siliconflow")
    monkeypatch.setattr(
        "backend.infra.llm.credentials.resolve_credentials", fake_resolve
    )
    monkeypatch.setattr(
        "backend.infra.llm.providers.siliconflow.build_siliconflow",
        lambda model_name, credentials=None: "SF",
    )

    proxy_mod._build_llm_for("some-model")
    assert seen["provider"] == "siliconflow"
    assert seen["model_name"] == "some-model"


def test_credentials_resolution_failure_falls_back_to_none(monkeypatch):
    """凭据解析异常 → 回落 None（= 与改造前逐位一致），不在热路径新增崩溃点。"""
    captured: dict = {}

    def boom(provider, *, model_name=None):
        raise RuntimeError("注册表不可用")

    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _m: "deepseek")
    monkeypatch.setattr("backend.infra.llm.credentials.resolve_credentials", boom)
    monkeypatch.setattr(
        "backend.infra.llm.providers.deepseek.build_deepseek",
        lambda model_name, credentials=None: captured.setdefault(
            "credentials", credentials
        )
        or "DEEPSEEK",
    )

    assert proxy_mod._build_llm_for("m") == "DEEPSEEK"
    assert captured["credentials"] is None


# ── vllm 分支（2026-09-19 补）─────────────────────────────────────────


def test_vllm_provider_dispatches_to_build_vllm(monkeypatch):
    """`provider == "vllm"` 必须走 build_vllm，不能落到 ollama 兜底。"""
    seen: dict = {}

    def fake_vllm(model_name, credentials=None):
        seen["model"] = model_name
        return "VLLM_LLM"

    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _m: "vllm")
    monkeypatch.setattr(
        "backend.infra.llm.providers.vllm.build_vllm", fake_vllm
    )
    monkeypatch.setattr(
        "backend.infra.llm.providers.ollama.build_ollama",
        lambda *a, **k: pytest.fail("vllm 模型不应落到 ollama 兜底"),
    )

    assert proxy_mod._build_llm_for("Qwen/Qwen3-32B-AWQ") == "VLLM_LLM"
    assert seen["model"] == "Qwen/Qwen3-32B-AWQ"


def test_vllm_is_a_selectable_registered_model():
    """回归锁：models 层确实注册了 vllm provider 的可选模型（否则上面的分支没意义）。"""
    from backend.infra.llm import models as models_mod

    # §B.15 起注册表 DB-only：注入迁移 0023 的 vllm 条目模拟注册表已加载
    models_mod.set_dynamic_models([
        {"name": "Qwen/Qwen3-32B-AWQ", "provider": "vllm", "source": "db"},
    ])
    vllm_models = [
        m for m in models_mod.get_available_models() if m.get("provider") == "vllm"
    ]
    assert vllm_models, "models 已无 vllm 模型 —— 请同步删除 proxy 的 vllm 分支与本节测试"


# ── ollama 兜底复用共享构建器 ─────────────────────────────────────────


def test_ollama_fallback_uses_shared_builder(monkeypatch):
    """兜底分支复用 `providers/ollama.build_ollama`（与 factory 同源）。"""
    seen: dict = {}

    def fake_ollama(model_name, credentials=None):
        seen["model"] = model_name
        seen["credentials"] = credentials
        return "OLLAMA_LLM"

    monkeypatch.setattr(proxy_mod, "_get_provider_for", lambda _m: "ollama")
    monkeypatch.setattr(
        "backend.infra.llm.providers.ollama.build_ollama", fake_ollama
    )

    assert proxy_mod._build_llm_for("llama3") == "OLLAMA_LLM"
    assert seen["model"] == "llama3"


# ── provider 判定与 factory 同源 ──────────────────────────────────────


def test_get_provider_for_delegates_to_resolve_provider(monkeypatch):
    """`_get_provider_for` 委托 `models.resolve_provider`（判定口径单点）。"""
    calls: list = []
    monkeypatch.setattr(
        proxy_mod,
        "resolve_provider",
        lambda name, **kw: calls.append(name) or "qwen",
    )
    assert proxy_mod._get_provider_for("anything") == "qwen"
    assert calls == ["anything"]


def test_get_provider_for_sees_dynamic_layer():
    """DB 覆盖层登记的模型也判得出 provider。

    旧实现只遍历代码层 `AVAILABLE_MODELS` → 自建模型一律误判成 ollama，
    而同一个值还会写进 `_last_call_meta["provider"]` 供计价链读取 →
    **费用归属记到错误的 provider 上**。
    """
    from backend.infra.llm import models as models_mod

    models_mod.set_dynamic_models(
        [
            {
                "name": "self-hosted-llm",
                "provider": "vllm",
                "display": "自建实例",
                "source": "db",
            }
        ]
    )
    try:
        assert proxy_mod._get_provider_for("self-hosted-llm") == "vllm"
    finally:
        models_mod.reset_dynamic_models_for_tests()
