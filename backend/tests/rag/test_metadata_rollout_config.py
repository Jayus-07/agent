"""元数据级联灰度与回滚配置契约。"""
from __future__ import annotations

import pytest

from backend.rag.preprocessing import metadata_rule_service as rule_service_module
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


def test_route_pointer_prefers_shared_cache_for_other_workers(monkeypatch):
    class _PointerCache:
        def __init__(self):
            self.value = None

        def get_json(self, key):
            return self.value

        def set_json(self, key, value, ttl=None):
            self.value = value

    cache = _PointerCache()
    monkeypatch.setattr(rule_service_module, "get_cache", lambda *args, **kwargs: cache)
    monkeypatch.setattr(
        rule_service_module,
        "_route_pointer",
        {"rules_version": "", "model_version": "", "history_preserved": True},
    )

    rollback_metadata_route("local-rules", "local-model")
    cache.value = {
        "rules_version": "shared-rules",
        "model_version": "shared-model",
        "history_preserved": True,
        "changed_at": "2026-09-20T00:00:00+00:00",
    }

    pointer = rule_service_module.get_metadata_route_pointer()

    assert pointer["rules_version"] == "shared-rules"
    assert pointer["model_version"] == "shared-model"


def test_route_pointer_uses_configured_rollback_versions_when_cache_is_empty(monkeypatch):
    class _EmptyCache:
        def get_json(self, key):
            return None

    monkeypatch.setattr(rule_service_module, "get_cache", lambda *args, **kwargs: _EmptyCache())
    monkeypatch.setattr(
        rule_service_module,
        "_route_pointer",
        {"rules_version": "", "model_version": "", "history_preserved": True},
    )
    monkeypatch.setattr(
        "backend.config.rag.METADATA_ROLLBACK_RULES_VERSION", "configured-rules"
    )
    monkeypatch.setattr(
        "backend.config.rag.METADATA_ROLLBACK_MODEL_VERSION", "configured-model"
    )

    pointer = rule_service_module.get_metadata_route_pointer()

    assert pointer["rules_version"] == "configured-rules"
    assert pointer["model_version"] == "configured-model"
    assert pointer["source"] == "config"
