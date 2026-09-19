"""元数据级联灰度与回滚配置契约。"""
from __future__ import annotations

import pytest

from backend.rag.preprocessing.metadata_rule_service import rollback_metadata_route


def test_defaults_keep_cascade_and_classifier_disabled():
    from backend.config import rag as config

    assert config.METADATA_CASCADE_ENABLED is False
    assert config.METADATA_CLASSIFIER_ENABLED is False


def test_rollout_percentage_is_bounded():
    from backend.config import rag as config

    assert config.METADATA_CASCADE_ROLLOUT_PERCENT in range(0, 101)


def test_invalid_rollout_percentage_fails_closed(monkeypatch):
    monkeypatch.setenv("METADATA_CASCADE_ROLLOUT_PERCENT", "101")
    with pytest.raises(ValueError, match="0-100"):
        config_path = "backend.config.rag"
        __import__(config_path)
        # 配置模块已加载时，直接调用专用解析器仍必须拒绝越界值。
        from backend.config.rag import _parse_rollout_percent
        _parse_rollout_percent("METADATA_CASCADE_ROLLOUT_PERCENT", "101")


def test_rollback_target_is_versioned(monkeypatch):
    result = rollback_metadata_route("rules-v1", "model-v0")

    assert result["rules_version"] == "rules-v1"
    assert result["model_version"] == "model-v0"
    assert result["history_preserved"] is True
