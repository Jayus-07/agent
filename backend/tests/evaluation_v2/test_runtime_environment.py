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
