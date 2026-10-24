from backend.config import llm as llm_config
from backend.config import rag as rag_config
from backend.evaluation_v2 import runtime_environment


def test_local_reranker_device_comes_from_rag_configuration(monkeypatch):
    monkeypatch.setattr(runtime_environment, "_model_role_snapshot", lambda **kwargs: {
        "provider_mode": kwargs["provider_mode"],
    })
    monkeypatch.setattr(llm_config, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(llm_config, "EMBEDDING_MODEL_PATH", "/models/embedding")
    monkeypatch.setattr(llm_config, "RERANK_PROVIDER", "local")
    monkeypatch.setattr(llm_config, "RERANKER_MODEL_PATH", "/models/reranker")
    monkeypatch.setattr(rag_config, "RAG_MODE", "local")
    monkeypatch.setattr(rag_config, "RERANKER_DEVICE", "cpu")

    snapshot = runtime_environment.current_runtime_environment()

    assert snapshot["models"]["rerank"]["device"] == "cpu"


def test_generation_model_is_frozen_into_snapshot(monkeypatch):
    import backend.evaluation.generation as generation

    monkeypatch.setattr(runtime_environment, "_model_role_snapshot", lambda **kwargs: {
        "provider_mode": kwargs["provider_mode"],
    })
    monkeypatch.setattr(generation, "peek_eval_model", lambda: {
        "model": "qwen2.5-72b-instruct", "provider": "dashscope",
    })

    snapshot = runtime_environment.current_runtime_environment()

    assert snapshot["models"]["generation"] == {
        "model": "qwen2.5-72b-instruct", "provider": "dashscope",
    }


def test_assert_runtime_environment_accepts_legacy_snapshot_without_generation(monkeypatch):
    """部署前创建的运行快照只固定 embedding/rerank；新增 generation 角色后，
    历史快照重试不应被误判为环境漂移。"""
    frozen_models = {
        "embedding": {"provider_mode": "cloud", "model": "text-embedding-v3"},
        "rerank": {"provider_mode": "cloud", "model": "gte-rerank"},
    }
    monkeypatch.setattr(runtime_environment, "current_runtime_environment", lambda: {
        "rag_mode": "hybrid",
        "models": {
            **frozen_models,
            "generation": {"model": "qwen2.5-72b-instruct", "provider": "dashscope"},
        },
    })

    runtime_environment.assert_runtime_environment({"models": frozen_models})


def test_assert_runtime_environment_rejects_drift_on_frozen_role(monkeypatch):
    monkeypatch.setattr(runtime_environment, "current_runtime_environment", lambda: {
        "rag_mode": "hybrid",
        "models": {
            "embedding": {"provider_mode": "cloud", "model": "text-embedding-v3"},
            "rerank": {"provider_mode": "cloud", "model": "gte-rerank-v2"},
        },
    })

    try:
        runtime_environment.assert_runtime_environment({
            "models": {
                "embedding": {"provider_mode": "cloud", "model": "text-embedding-v3"},
                "rerank": {"provider_mode": "cloud", "model": "gte-rerank"},
            }
        })
    except runtime_environment.RuntimeEnvironmentMismatchError as exc:
        assert exc.reason_code == "runtime_environment_changed"
    else:
        raise AssertionError("冻结角色漂移未拒绝运行")


def test_assert_runtime_environment_still_requires_models_dict():
    try:
        runtime_environment.assert_runtime_environment({"rag_mode": "hybrid"})
    except runtime_environment.RuntimeEnvironmentMismatchError as exc:
        assert exc.reason_code == "runtime_snapshot_missing"
    else:
        raise AssertionError("缺少 models 的快照未拒绝")
