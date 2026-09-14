"""tests/selection_decision/test_decision_log.py — 批次3 决策留痕存储测试

核心验收：快照不可变（触发器 RAISE ABORT）、版本自增、
user_decision/actual_metrics 专用回填通道不触碰快照。
"""
import json

import pytest

from backend.selection_decision.store import SelectionDecisionStore


@pytest.fixture
def store(tmp_path):
    return SelectionDecisionStore(db_path=str(tmp_path / "sd_test.db"))


EV = {"evidence_verdict": "sufficient", "metrics": {"candidate_count": 4}}
SCORE = {"finance": {"margin_rate": 0.35}, "panel": "pass"}


def _record(store, candidate_id="category:蓝牙耳机", recommendation="recommend"):
    return store.record_decision(
        task_id="t1", candidate_id=candidate_id, category="蓝牙耳机",
        evidence_snapshot=EV, score_snapshot=SCORE, recommendation=recommendation)


class TestDecisionLog:
    def test_record_and_get(self, store):
        did = _record(store)
        d = store.get_decision(did)
        assert d["recommendation"] == "recommend"
        assert d["evidence_snapshot"]["evidence_verdict"] == "sufficient"
        assert d["score_snapshot"]["finance"]["margin_rate"] == 0.35
        assert d["decision_version"] == 1
        assert d["user_decision"] is None and d["feedback_at"] is None

    def test_version_increments_same_candidate(self, store):
        d1 = _record(store)
        d2 = _record(store)
        d3 = _record(store, recommendation="reject")
        assert store.get_decision(d1)["decision_version"] == 1
        assert store.get_decision(d2)["decision_version"] == 2
        assert store.get_decision(d3)["recommendation"] == "reject"

    def test_version_isolated_per_candidate(self, store):
        a = _record(store, candidate_id="category:A")
        b = _record(store, candidate_id="category:B")
        assert store.get_decision(a)["decision_version"] == 1
        assert store.get_decision(b)["decision_version"] == 1

    def test_snapshot_immutable_by_trigger(self, store):
        """核心验收：直接 UPDATE 快照列 → 触发器 ABORT。"""
        did = _record(store)
        conn = store._conn()
        try:
            with pytest.raises(Exception, match="快照不可变"):
                conn.execute(
                    "UPDATE decision_log SET evidence_snapshot = ? WHERE decision_id = ?",
                    (json.dumps({"tampered": True}), did),
                )
                conn.commit()
        finally:
            conn.close()
        # 原快照未被改动
        assert store.get_decision(did)["evidence_snapshot"] == EV

    def test_set_user_decision_only_touches_that_field(self, store):
        did = _record(store)
        assert store.set_user_decision(did, "adopted") is True
        d = store.get_decision(did)
        assert d["user_decision"] == "adopted"
        assert d["evidence_snapshot"] == EV  # 快照未动
        assert d["actual_metrics"] == {}

    def test_set_feedback(self, store):
        did = _record(store)
        assert store.set_feedback(did, {"monthly_sales": 320, "return_rate": 0.02}) is True
        d = store.get_decision(did)
        assert d["actual_metrics"]["monthly_sales"] == 320
        assert d["feedback_at"] is not None
        assert d["evidence_snapshot"] == EV

    def test_set_on_missing_id_returns_false(self, store):
        assert store.set_user_decision("nope", "adopted") is False
        assert store.set_feedback("nope", {"x": 1}) is False

    def test_list_decisions_filtered_and_ordered(self, store):
        _record(store, candidate_id="category:A")
        _record(store, candidate_id="category:A")
        _record(store, candidate_id="category:B")
        a = store.list_decisions(candidate_id="category:A")
        assert len(a) == 2 and a[0]["decision_version"] == 2  # 倒序
        assert len(store.list_decisions()) == 3
