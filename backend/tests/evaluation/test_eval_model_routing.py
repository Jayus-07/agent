"""评测生成模型的角色绑定与供应商路由契约。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.config import model_roles
from backend.evaluation import generation
from backend.infra.llm import credentials, models, proxy
from backend.services import model_config


@pytest.fixture(autouse=True)
def _clean_roles():
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    credentials.reset_credentials_for_tests()
    yield
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    credentials.reset_credentials_for_tests()


def test_eval_gen_has_no_implicit_local_default(monkeypatch):
    """未配置评测角色时不能再隐式落到不可用的本地 qwen2.5:3b。"""
    monkeypatch.delenv("EVAL_GEN_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:3b")

    resolved = model_roles.resolve_raw("eval_gen")

    assert resolved["value"] == ""
    assert model_roles.MODEL_ROLES["eval_gen"].default == ""
    assert model_roles.MODEL_ROLES["eval_gen"].env_key == "EVAL_GEN_MODEL"


def test_eval_gen_rejects_unregistered_or_disabled_local_model(monkeypatch):
    """评测角色只能绑定已登记且当前可用的模型。"""
    from backend.infra.llm import models as llm_models

    # §B.15 起注册表 DB-only：注入 ollama 条目使 qwen2.5:3b 成为已登记模型
    llm_models.set_dynamic_models([
        {"name": "qwen2.5:3b", "provider": "ollama", "source": "db"},
    ])
    monkeypatch.setattr(model_config, "OLLAMA_ENABLED", False)

    assert model_config._model_validation_issue(
        "model-that-is-not-registered", role="eval_gen"
    ) is not None
    assert "Ollama" in (model_config._model_validation_issue(
        "qwen2.5:3b", role="eval_gen"
    ) or "")


def test_eval_gen_can_be_explicitly_cleared():
    """管理员可以清空旧绑定，明确停用评测生成。"""
    assert model_config._model_validation_issue("", role="eval_gen") is None


def test_cloud_eval_generation_uses_the_bound_provider(monkeypatch):
    """云端评测模型必须走统一角色路由，而不是固定 QWEN 环境变量。"""
    model_roles.set_override("eval_gen", "qwen3.7-plus")
    credentials.set_db_credentials({
        "qwen": credentials.ProviderCredentials(
            provider="qwen",
            api_key="db-test-key",
            base_url="https://db.example/v1",
            source="db",
            version=1,
        ),
    })
    monkeypatch.setattr(generation, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(generation, "_build_prompt", lambda *_args: "prompt")

    seen_bindings: list[tuple[str, float | None]] = []

    class _FakeChat:
        def invoke(self, _messages):
            return SimpleNamespace(content="来自角色绑定的云端模型")

    monkeypatch.setattr(
        proxy,
        "bind_llm_for_role",
        lambda role, **kwargs: seen_bindings.append(
            (role, kwargs.get("temperature"))) or _FakeChat(),
    )

    result = generation.generate_answer("问题", ["上下文"], allow_cloud=True)

    assert result == "来自角色绑定的云端模型"
    assert seen_bindings == [("eval_gen", 0.1)]


def test_cloud_eval_generation_checks_input_price_tier_before_provider(monkeypatch):
    """云端评测生成必须在发请求前执行当前模型的价格档位门禁。"""
    monkeypatch.setattr(generation, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(
        generation, "resolve_eval_model",
        lambda: ("doubao-seed-2.0-mini", "volcengine"),
    )
    monkeypatch.setattr(generation, "_build_prompt", lambda *_args: "x" * 32_001)
    provider_calls = []
    guarded = []

    class _FakeChat:
        def invoke(self, _messages):
            provider_calls.append(True)
            return SimpleNamespace(content="不应调用")

    def reject_over_limit(args, *, model_name, kwargs=None):
        guarded.append((args, model_name, kwargs))
        raise RuntimeError("超过已登记价格档位")

    monkeypatch.setattr(
        proxy, "bind_llm_for_role", lambda _role, **_kwargs: _FakeChat(),
    )
    monkeypatch.setattr(proxy, "enforce_model_input_price_tier", reject_over_limit)

    with pytest.raises(RuntimeError, match="超过已登记价格档位"):
        generation.generate_answer("问题", ["上下文"], allow_cloud=True)

    assert guarded and guarded[0][1] == "doubao-seed-2.0-mini"
    assert provider_calls == []
