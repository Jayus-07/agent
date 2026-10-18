"""入库专用模型角色解析测试。"""

from unittest.mock import MagicMock

import pytest

from backend.config import model_roles
from backend.infra.llm import proxy
from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm


@pytest.fixture(autouse=True)
def reset_roles():
    model_roles.reset_overrides()
    yield
    model_roles.reset_overrides()


def test_ingestion_roles_are_registered_and_inherit_main():
    assert {"metadata_extract", "question_gen", "table_describe"} <= set(
        model_roles.MODEL_ROLES
    )
    assert model_roles.resolve_effective("question_gen")["value"]


def test_get_llm_for_role_uses_effective_role_model(monkeypatch):
    model_roles.set_override("question_gen", "question-model")
    sentinel = object()
    seen = []
    monkeypatch.setattr(
        proxy,
        "_get_override_llm",
        lambda model_name: seen.append(model_name) or sentinel,
    )

    assert proxy.get_llm_for_role("question_gen") is sentinel
    assert seen == ["question-model"]


def test_invoke_metadata_llm_uses_governed_role_proxy(monkeypatch):
    fake = MagicMock()
    fake.model = "metadata-role-model"
    fake.invoke.return_value = "ok"
    seen = []
    monkeypatch.setattr(
        proxy,
        "bind_llm_for_role",
        lambda role: seen.append(role) or fake,
    )
    monkeypatch.setattr(
        proxy,
        "get_llm_for_role",
        lambda _role: pytest.fail("裸模型入口不得用于元数据角色调用"),
    )

    invoke_metadata_llm("prompt", role="table_describe")

    assert seen == ["table_describe"]
    fake.invoke.assert_called_once()


def test_invoke_metadata_llm_disables_thinking_through_role_proxy(monkeypatch):
    """能力策略生成的供应商专属关闭参数必须经统一角色代理透传到底层调用。"""
    fake = MagicMock()
    fake.model = "qwen3.7-plus"
    fake.invoke.return_value = "ok"
    monkeypatch.setattr(proxy, "bind_llm_for_role", lambda _role: fake)
    monkeypatch.setattr(
        "backend.infra.llm.models.supports_thinking_flag",
        lambda model_name: model_name == "qwen3.7-plus",
    )
    monkeypatch.setattr(
        "backend.infra.llm.models.thinking_disabled_extra_body",
        lambda _model: {"thinking": {"type": "disabled"}},
    )

    invoke_metadata_llm("prompt")

    fake.invoke.assert_called_once_with(
        "prompt", extra_body={"thinking": {"type": "disabled"}},
    )


def test_invoke_metadata_llm_plain_invoke_when_policy_declines(monkeypatch):
    """策略判定不注入参数（如无法识别供应商）时回落普通调用。"""
    fake = MagicMock()
    fake.model = "qwen3.7-plus"
    fake.invoke.return_value = "ok"
    monkeypatch.setattr(proxy, "bind_llm_for_role", lambda _role: fake)
    monkeypatch.setattr(
        "backend.infra.llm.models.supports_thinking_flag",
        lambda _model: True,
    )
    monkeypatch.setattr(
        "backend.infra.llm.models.thinking_disabled_extra_body",
        lambda _model: None,
    )

    invoke_metadata_llm("prompt")

    fake.invoke.assert_called_once_with("prompt")


def test_invoke_metadata_llm_keeps_telemetry_for_explicit_raw_client(monkeypatch):
    """兼容显式注入的原始客户端，同时记录用量遥测。"""
    fake = MagicMock()
    fake.model = "metadata-role-model"
    fake.invoke.return_value = MagicMock(
        response_metadata={
            "model": "metadata-role-model",
            "token_usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )
    recorded = []
    monkeypatch.setattr(
        proxy,
        "record_llm_result",
        lambda result, **kwargs: recorded.append((result, kwargs)),
    )

    invoke_metadata_llm("prompt", llm_obj=fake)

    assert len(recorded) == 1
    assert recorded[0][1]["model_name"] == "metadata-role-model"
