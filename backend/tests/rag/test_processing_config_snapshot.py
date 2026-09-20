"""处理运行配置快照的模型身份测试。"""
from __future__ import annotations

from backend.rag.indexing import indexer
from backend.rag.indexing.processing_lineage import ModelIdentity


def test_processing_snapshot_uses_runtime_ocr_identity(monkeypatch):
    """快照中的 OCR 身份必须来自实际运行时解析，而不是旧 env 默认值。"""
    monkeypatch.setattr(
        "backend.rag.preprocessing.parser.ocr.get_ocr_model_identity",
        lambda: ModelIdentity(
            role="ocr",
            engine_type="ocr",
            provider="custom-ocr-provider",
            model_name="ocr-db-model",
            model_revision="rev-1",
            config_source="db",
            config_revision="cfg-1",
            artifact_fingerprint=None,
        ),
    )

    snapshot = indexer._processing_config_snapshot()

    assert snapshot["ocr_provider"] == "custom-ocr-provider"
    assert snapshot["ocr_model"] == "ocr-db-model"
    assert snapshot["ocr_model_revision"] == "rev-1"
    assert snapshot["ocr_config_source"] == "db"
    assert snapshot["ocr_config_revision"] == "cfg-1"
