"""SelectionStore 单测 — 评分缓存与权重配置"""
import pytest

from backend.selection.store import SelectionStore, DEFAULT_WEIGHTS


@pytest.fixture
def store(tmp_path):
    return SelectionStore(db_path=str(tmp_path / "selection_test.db"))


class TestScoreCache:
    def test_save_and_get_score(self, store):
        store.save_score("https://a.com", {"total": 82.5, "breakdown": {}, "notes": []}, snapshot_id=7)
        row = store.get_score("https://a.com")
        assert row is not None
        assert row["snapshot_id"] == 7
        assert row["score_json"]["total"] == 82.5
        assert row["computed_at"]

    def test_get_missing_score_returns_none(self, store):
        assert store.get_score("https://none.com") is None

    def test_save_score_upsert(self, store):
        store.save_score("https://a.com", {"total": 60.0, "breakdown": {}, "notes": []}, snapshot_id=1)
        store.save_score("https://a.com", {"total": 70.0, "breakdown": {}, "notes": []}, snapshot_id=2)
        row = store.get_score("https://a.com")
        assert row["score_json"]["total"] == 70.0
        assert row["snapshot_id"] == 2

    def test_all_scores(self, store):
        store.save_score("https://a.com", {"total": 60.0, "breakdown": {}, "notes": []}, None)
        store.save_score("https://b.com", {"total": 80.0, "breakdown": {}, "notes": []}, None)
        assert len(store.all_scores()) == 2


class TestWeights:
    def test_default_weights_when_empty(self, store):
        assert store.get_weights() == DEFAULT_WEIGHTS

    def test_set_and_get_weights(self, store):
        store.set_weights({"reputation": 0.5, "heat": 0.5})
        w = store.get_weights()
        assert w["reputation"] == 0.5
        assert w["heat"] == 0.5
        # 未覆盖的 key 保留默认值
        assert w["stability"] == DEFAULT_WEIGHTS["stability"]
