"""数据库模型配置唯一来源的回归测试。"""
from __future__ import annotations

import pytest

from backend.config import model_roles
from backend.infra.llm import credentials, models
from backend.infra.llm.credentials import ProviderCredentials
from backend.services import model_config


@pytest.fixture(autouse=True)
def _clean_runtime_layers():
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    credentials.reset_credentials_for_tests()
    yield
    model_roles.reset_overrides()
    models.reset_dynamic_models_for_tests()
    credentials.reset_credentials_for_tests()


def test_model_role_does_not_read_legacy_env_without_db_override(monkeypatch):
    """删除 env 后，角色不能重新从环境变量复活旧模型。"""
    monkeypatch.setenv("LLM_MODEL", "env-only-model")

    resolved = model_roles.resolve_raw("main")

    assert resolved["value"] == model_roles.MODEL_ROLES["main"].default
    assert resolved["source"] == model_roles.SOURCE_DEFAULT


def test_db_provider_without_key_does_not_fallback_to_env(monkeypatch):
    """数据库供应商缺 Key 时必须报未配置，不能偷偷使用旧 env Key。"""
    monkeypatch.setattr(
        "backend.config.llm.QWEN_API_KEY", "env-only-secret"
    )
    credentials.set_db_credentials(
        {
            "qwen": ProviderCredentials(
                provider="qwen",
                base_url="https://db.example/v1",
                source="db",
                version=4,
            )
        }
    )

    resolved = credentials.resolve_credentials("qwen")

    assert resolved.api_key is None
    assert resolved.base_url == "https://db.example/v1"
    assert resolved.source == "db"


def test_model_validation_uses_database_credential_only(monkeypatch):
    """模型可用性校验必须检查数据库凭据，而不是检查同名 env。"""
    monkeypatch.setenv("QWEN_API_KEY", "env-only-secret")
    models.set_dynamic_models(
        [
            {
                "name": "db-chat-model",
                "provider": "qwen",
                "model_kind": "chat",
                "source": "db",
            }
        ]
    )
    credentials.set_db_credentials(
        {
            "qwen": ProviderCredentials(
                provider="qwen",
                base_url="https://db.example/v1",
                source="db",
            )
        }
    )

    reason = model_config._model_validation_issue("db-chat-model", role="main")

    assert reason is not None
    assert "未配置 API Key" in reason


def test_embedding_config_env_fallback_when_no_db_binding_transitional(monkeypatch):
    """过渡期行为（2026-09-21 拍板保留，见设计文档 §B.16.3）：

    无 DB embedding 绑定时允许回退旧 env（provider == "env"），保证无 DB
    绑定的开发环境仍具备云端 embedding 能力；DB 绑定存在时仍是唯一来源
    （由 _resolve_cloud_embedding_config 分支保证）。待 DB 绑定成为强制
    配置后，本用例应翻转回「env 不再生效」的目标态断言。
    """
    from backend.rag import embedding_singleton

    # 屏蔽模块导入期快照的常量，确保 monkeypatch.setenv 生效
    monkeypatch.setattr(embedding_singleton, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(embedding_singleton, "EMBEDDING_API_BASE", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "env-only-secret")
    monkeypatch.setenv("EMBEDDING_API_BASE", "https://env-fallback.example/v1")
    monkeypatch.setattr(
        embedding_singleton.specialized_mod,
        "resolve_binding",
        lambda _role: None,
    )

    runtime = embedding_singleton._resolve_cloud_embedding_config()

    assert runtime["api_key"] == "env-only-secret"
    assert runtime["base_url"] == "https://env-fallback.example/v1"
    assert runtime["provider"] == "env"


def test_rerank_config_ignores_legacy_env_without_db_binding(monkeypatch):
    """没有 DB rerank 绑定时，旧 rerank Key 不得继续生效。"""
    from backend.rag import reranker

    monkeypatch.setattr(reranker, "RERANK_API_FORMAT", "jina")
    monkeypatch.setattr(
        reranker.specialized_mod,
        "resolve_binding",
        lambda _role: None,
    )
    monkeypatch.setenv("RERANK_API_KEY", "env-only-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env-only-secret")

    runtime = reranker._resolve_rerank_runtime_config()

    assert runtime["api_key"] == ""
    assert runtime["base_url"] == ""


def test_ocr_key_env_fallback_when_no_db_binding_transitional(monkeypatch):
    """过渡期行为（2026-09-21 拍板保留，见设计文档 §B.16.3）：

    无 DB OCR 角色绑定时允许按优先级回退旧 env（OCR_DASHSCOPE →
    DASHSCOPE → EMBEDDING）；DB 绑定存在时由 _resolve_ocr_runtime_config
    走数据库凭据（source==SOURCE_DB 分支）。待 DB 绑定成为强制配置后，
    本用例应翻转回「env 不再生效」的目标态断言。
    """
    from backend.rag.preprocessing.parser import ocr

    monkeypatch.setenv("OCR_DASHSCOPE_API_KEY", "ocr-env-secret")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-env-secret")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-env-secret")

    # 命中第一优先级
    assert ocr._resolve_dashscope_key() == "ocr-env-secret"

    monkeypatch.delenv("OCR_DASHSCOPE_API_KEY")
    assert ocr._resolve_dashscope_key() == "dashscope-env-secret"

    monkeypatch.delenv("DASHSCOPE_API_KEY")
    assert ocr._resolve_dashscope_key() == "embedding-env-secret"

    monkeypatch.delenv("EMBEDDING_API_KEY")
    assert ocr._resolve_dashscope_key() == ""
