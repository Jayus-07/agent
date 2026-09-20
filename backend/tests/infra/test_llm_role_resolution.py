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


def test_invoke_metadata_llm_passes_explicit_role(monkeypatch):
    fake = MagicMock()
    fake.model = "metadata-role-model"
    fake.invoke.return_value = "ok"
    seen = []
    monkeypatch.setattr(
        proxy,
        "get_llm_for_role",
        lambda role: seen.append(role) or fake,
    )

    invoke_metadata_llm("prompt", role="table_describe")

    assert seen == ["table_describe"]
    fake.invoke.assert_called_once()


def test_invoke_metadata_llm_records_direct_role_result(monkeypatch):
    """专用角色拿到底层实例时仍必须进入统一用量记录器。"""
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
    monkeypatch.setattr(proxy, "get_llm_for_role", lambda role: fake)
    monkeypatch.setattr(
        proxy,
        "record_llm_result",
        lambda result, **kwargs: recorded.append((result, kwargs)),
    )

    invoke_metadata_llm("prompt")

    assert len(recorded) == 1
    assert recorded[0][1]["model_name"] == "metadata-role-model"
