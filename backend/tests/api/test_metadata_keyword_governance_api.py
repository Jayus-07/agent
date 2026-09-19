"""元数据关键词治理 API：读兼容、草稿隔离、审批门和管理员鉴权。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.deps import require_admin_user
from backend.app.api.routes import keyword_routes
from backend.rag.preprocessing.metadata_rule_service import MetadataRuleService


class _Cache:
    def __init__(self):
        self.values = {}

    def get_json(self, key):
        return self.values.get(key)

    def set_json(self, key, value, ttl=None):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class _Store:
    def __init__(self):
        self.snapshots = {}
        self.active_version = None
        self.next_version = 1
        self.approvals = {"approval-ok": "reviewer-1"}
        self.cache_invalidations = 0

    def create_rule_snapshot(self, **payload):
        version = self.next_version
        self.next_version += 1
        row = {"version": version, **payload}
        self.snapshots[version] = row
        return row

    def get_active_rule_snapshot(self):
        if self.active_version is None:
            raise LookupError("no published metadata rule snapshot")
        return self.snapshots[self.active_version]

    def is_rule_approval_approved(self, approval_id):
        return self.approvals.get(approval_id)

    def publish_rule_snapshot(self, version, approval_id, actor):
        if not self.is_rule_approval_approved(approval_id):
            raise PermissionError("approved approval_id is required")
        if version not in self.snapshots:
            raise LookupError("version not found")
        if self.active_version is not None:
            self.snapshots[self.active_version]["status"] = "rolled_back"
        row = self.snapshots[version]
        row.update(
            status="published",
            approval_id=approval_id,
            approved_by=self.approvals[approval_id],
            actor=actor,
        )
        self.active_version = version
        return row

    def rollback_rule_snapshot(self, version, actor, reason):
        if version not in self.snapshots:
            raise LookupError("version not found")
        if self.active_version is not None:
            self.snapshots[self.active_version]["status"] = "rolled_back"
        row = self.snapshots[version]
        row.update(status="published", actor=actor, reason=reason)
        self.active_version = version
        return row

    def invalidate_rule_cache(self):
        self.cache_invalidations += 1

    def list_all(self, **kwargs):
        return []

    def list_doc_types(self):
        return ["general", "legal"]

    def list_categories(self):
        return []


class _FakeIdent:
    actor = "user:test-admin"
    role = "admin"
    kind = "user"


def _make_client(monkeypatch):
    store = _Store()
    service = MetadataRuleService(store, cache=_Cache())
    seed = service.create_rule_draft(
        [{"keyword": "合同", "doc_type": "legal", "category": "法务", "weight": 3}],
        actor="migration",
        reason="test seed",
    )
    service.publish_rule_snapshot(seed.version, "approval-ok", "migration")
    monkeypatch.setattr(keyword_routes, "_service", lambda: service)

    app = FastAPI()
    app.include_router(keyword_routes.router)
    app.dependency_overrides[require_admin_user] = lambda: _FakeIdent()
    return TestClient(app), service, store


def test_draft_is_not_visible_until_publish(monkeypatch):
    client, service, store = _make_client(monkeypatch)

    draft = client.post(
        "/rag/keywords",
        json={
            "keyword": "数据留存",
            "doc_type": "legal",
            "weight": 4,
            "reason": "补充数据合规术语",
        },
    )
    assert draft.status_code == 200
    draft_body = draft.json()
    assert draft_body["status"] == "draft"
    assert draft_body["review_required"] is True

    active = client.get("/rag/keywords/versions/active")
    assert active.status_code == 200
    assert active.json()["version"] != draft_body["version"]
    assert all(
        item["keyword"] != "数据留存"
        for item in client.get("/rag/keywords").json()["items"]
    )

    rejected = client.post(
        f"/rag/keywords/versions/{draft_body['version']}/publish",
        json={"approval_id": "not-approved", "reason": "发布"},
    )
    assert rejected.status_code == 403

    published = client.post(
        f"/rag/keywords/versions/{draft_body['version']}/publish",
        json={"approval_id": "approval-ok", "reason": "审核通过后发布"},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    assert service.get_active_snapshot().version == draft_body["version"]
    assert store.cache_invalidations >= 1
    assert any(
        item["keyword"] == "数据留存"
        for item in client.get("/rag/keywords").json()["items"]
    )


def test_write_requires_reason_and_taxonomy_validation(monkeypatch):
    client, _, _ = _make_client(monkeypatch)

    missing_reason = client.post(
        "/rag/keywords",
        json={"keyword": "新词", "doc_type": "legal"},
    )
    assert missing_reason.status_code == 422

    unknown_type = client.post(
        "/rag/keywords",
        json={
            "keyword": "新词",
            "doc_type": "not-in-taxonomy",
            "reason": "验证错误输入",
        },
    )
    assert unknown_type.status_code == 422


def test_write_endpoints_reject_anonymous_operator():
    app = FastAPI()
    app.include_router(keyword_routes.router)
    client = TestClient(app)
    response = client.post(
        "/rag/keywords",
        json={"keyword": "新词", "doc_type": "legal", "reason": "test"},
    )
    # 未配置内部凭据的测试环境会由统一鉴权依赖返回 503（fail closed）。
    assert response.status_code in (401, 403, 503)


def test_rollback_restores_previous_hash(monkeypatch):
    client, _, _ = _make_client(monkeypatch)
    first = client.get("/rag/keywords/versions/active").json()
    draft = client.post(
        "/rag/keywords",
        json={"keyword": "第二版", "doc_type": "legal", "reason": "第二版"},
    ).json()
    client.post(
        f"/rag/keywords/versions/{draft['version']}/publish",
        json={"approval_id": "approval-ok", "reason": "发布第二版"},
    )

    restored = client.post(
        f"/rag/keywords/versions/{first['version']}/rollback",
        json={"reason": "回滚第二版"},
    )
    assert restored.status_code == 200
    assert restored.json()["rules_hash"] == first["rules_hash"]
