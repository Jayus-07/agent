"""动态元数据规则的版本、审批与回滚测试。"""

import threading

import pytest

from backend.rag.preprocessing.metadata_rule_service import (
    MetadataRuleService,
)


class _FakeRuleStore:
    def __init__(self):
        self.snapshots = {}
        self.next_version = 1
        self.active_version = None
        self.approvals = {"approved-1": "reviewer-1"}
        self.publish_calls = 0
        self.lock = threading.Lock()

    def create_rule_snapshot(self, **payload):
        version = self.next_version
        self.next_version += 1
        row = {
            "version": version,
            "status": "draft",
            **payload,
        }
        self.snapshots[version] = row
        return row

    def is_rule_approval_approved(self, approval_id):
        return self.approvals.get(approval_id)

    def publish_rule_snapshot(self, version, approval_id, actor):
        with self.lock:
            self.publish_calls += 1
            if not self.is_rule_approval_approved(approval_id):
                raise PermissionError("approval required")
            row = self.snapshots[version]
            if self.active_version is not None:
                self.snapshots[self.active_version]["status"] = "rolled_back"
            row.update(
                status="published",
                approval_id=approval_id,
                approved_by=self.approvals[approval_id],
                actor=actor,
            )
            self.active_version = version
            return row

    def rollback_rule_snapshot(self, version, actor, reason):
        with self.lock:
            row = self.snapshots[version]
            if self.active_version is not None:
                self.snapshots[self.active_version]["status"] = "rolled_back"
            row.update(status="published", actor=actor, reason=reason)
            self.active_version = version
            return row


@pytest.fixture
def rule_service():
    return MetadataRuleService(_FakeRuleStore())


def test_unknown_doc_type_is_rejected_before_insert(rule_service):
    with pytest.raises(ValueError, match="doc_type"):
        rule_service.create_rule_draft(
            [{"keyword": "新词", "doc_type": "not_in_taxonomy", "weight": 3}],
            actor="admin-1",
            reason="test",
        )


def test_weight_is_bounded(rule_service):
    with pytest.raises(ValueError, match="weight"):
        rule_service.create_rule_draft(
            [{"keyword": "词", "doc_type": "legal", "weight": 11}],
            actor="admin-1",
            reason="test",
        )


def test_unapproved_snapshot_cannot_become_active(rule_service):
    draft = rule_service.create_rule_draft(
        [{"keyword": "新词", "doc_type": "legal", "weight": 3}],
        actor="admin-1",
        reason="test",
    )
    with pytest.raises(PermissionError):
        rule_service.publish_rule_snapshot(
            draft.version, "missing-approval", "admin-1"
        )


def test_publish_and_rollback_preserve_versioned_hash(rule_service):
    first = rule_service.create_rule_draft(
        [{"keyword": "第一版", "doc_type": "legal", "weight": 3}],
        actor="admin-1",
        reason="first",
    )
    rule_service.publish_rule_snapshot(first.version, "approved-1", "admin-1")
    second = rule_service.create_rule_draft(
        [{"keyword": "第二版", "doc_type": "legal", "weight": 4}],
        actor="admin-1",
        reason="second",
    )
    rule_service.publish_rule_snapshot(second.version, "approved-1", "admin-1")

    restored = rule_service.rollback_rule_snapshot(first.version, "admin-1")

    assert restored.version == first.version
    assert restored.rules_hash == first.rules_hash
    assert restored.status == "published"
