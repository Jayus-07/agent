"""知识生命周期状态机回归（2026-10-03，演进分析 C1/C2）。

强断言口径：
- fail-closed：一切 →active 直跳（除 deprecated 恢复）必须被拒；
- 全链：处理中→pending_review→(approve)→active→deprecated→active 恢复 走通；
- 每次流转有审计记录（ai.knowledge_lifecycle_events，stub 连接捕获 SQL）；
- 检索过滤集 = pending_review ∪ deprecated ∪ 过期 active。
只 mock 外部边界（PG 连接 / registry），不 mock 被测逻辑。
"""

import pytest

from backend.rag.indexing.doc_registry import DOC_STATUSES
from backend.rag.indexing.lifecycle import (
    KnowledgeLifecycleService,
    LifecycleAuditLog,
    can_transition,
)


# ── stub：registry（只实现服务用到的三个方法 + 模拟 approve 直写）──

class StubRegistry:
    def __init__(self, docs: dict[str, dict]):
        self.docs = docs
        self.cas_calls: list[tuple] = []

    def get_by_doc_id(self, doc_id: str) -> dict | None:
        row = self.docs.get(doc_id)
        return dict(row) if row else None

    def transition_status_by_doc_id(self, doc_id: str, new_status: str, allowed_from) -> int:
        self.cas_calls.append((doc_id, new_status, tuple(allowed_from)))
        row = self.docs.get(doc_id)
        if not row or row.get("status") not in allowed_from:
            return 0
        row["status"] = new_status
        return 1

    def set_expire_at(self, doc_id: str, expire_at: str) -> int:
        row = self.docs.get(doc_id)
        if not row or row.get("status") != "active":
            return 0
        row["expire_at"] = expire_at
        return 1

    # 模拟审核通过（review_service.approve 的登记效果，测试里直接驱动）
    def approve(self, doc_id: str) -> None:
        assert self.docs[doc_id]["status"] == "pending_review"
        self.docs[doc_id]["status"] = "active"


# ── stub：PG 连接（捕获 SQL，验证审计写入与惰性建表）──

class FakeCursor:
    def __init__(self, store: list):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self._store.append((sql.strip(), params))

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self, store: list):
        self._store = store
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def cursor(self, cursor_factory=None):
        return FakeCursor(self._store)


def make_audit(store: list) -> LifecycleAuditLog:
    return LifecycleAuditLog(lambda: FakeConn(store))


# ── 状态机纯函数 ──

class TestCanTransition:
    def test_doc_statuses_contains_deprecated(self):
        assert "deprecated" in DOC_STATUSES

    def test_fail_closed_active_direct_jumps_rejected(self):
        # fail-closed 铁律：processing / failed / deleted / pending_review → active 全拒
        for src in ("uploading", "parsing", "embedding", "failed", "deleted", "pending_review"):
            ok, reason = can_transition(src, "active")
            assert not ok, f"{src}→active 不应被允许"
            assert reason, "拒绝必须带原因"

    def test_deprecated_restore_and_deprecate_allowed(self):
        assert can_transition("deprecated", "active") == (True, "")
        assert can_transition("active", "deprecated") == (True, "")

    def test_review_back_allowed(self):
        assert can_transition("active", "pending_review") == (True, "")

    def test_unknown_target_rejected(self):
        ok, reason = can_transition("active", "published")
        assert not ok
        assert "published" in reason

    def test_processing_states_not_operable(self):
        ok, reason = can_transition("parsing", "deprecated")
        assert not ok
        assert "不归生命周期操作管辖" in reason


# ── 服务全链 ──

class TestLifecycleService:
    def _service(self, docs: dict):
        return KnowledgeLifecycleService(StubRegistry(docs), make_audit([])), None

    def test_full_chain_lifecycle(self):
        """C1 验收主链：草稿期→pending_review→(approve)→active→deprecated→恢复。"""
        store: list = []
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "uploading"}})
        svc = KnowledgeLifecycleService(registry, make_audit(store))

        # 草稿期不可操作（管线管辖）
        assert svc.transition("d1", "pending_review")["ok"] is False
        registry.docs["d1"]["status"] = "pending_review"
        # pending_review→active 必须走 approve（fail-closed）
        assert svc.transition("d1", "active")["ok"] is False
        registry.approve("d1")
        # published → deprecated（下线）→ 恢复
        r1 = svc.transition("d1", "deprecated", actor="employee:op1", reason="政策到期下线")
        assert r1["ok"] is True and r1["from"] == "active" and r1["to"] == "deprecated"
        r2 = svc.transition("d1", "active", actor="employee:op1", reason="新版审核通过恢复")
        assert r2["ok"] is True and r2["to"] == "active"
        assert registry.docs["d1"]["status"] == "active"

    def test_every_transition_writes_audit(self):
        store: list = []
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "active"}})
        svc = KnowledgeLifecycleService(registry, make_audit(store))
        svc.transition("d1", "deprecated", actor="employee:a", reason="r1")
        inserts = [p for sql, p in store if sql.startswith("INSERT INTO ai.knowledge_lifecycle_events")]
        assert len(inserts) == 1
        doc_id, from_s, to_s, action, actor, reason = inserts[0]
        assert (doc_id, from_s, to_s) == ("d1", "active", "deprecated")
        assert actor == "employee:a" and reason == "r1"

    def test_audit_table_lazily_created_once(self):
        store: list = []
        audit = make_audit(store)
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "active"}})
        svc = KnowledgeLifecycleService(registry, audit)
        svc.transition("d1", "deprecated")
        svc.transition("d1", "active")
        creates = [sql for sql, _ in store if sql.startswith("CREATE TABLE IF NOT EXISTS ai.knowledge_lifecycle_events")]
        assert len(creates) == 1, "建表语句只应在首次使用时执行一次"

    def test_missing_doc_rejected(self):
        svc, _ = self._service({})
        assert svc.transition("nope", "deprecated") == {"ok": False, "error": "文档不存在"}

    def test_cas_race_reports_retry(self):
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "active"}})
        # 模拟裁决后状态被并发改走：CAS 强制 allowed_from 不含实际状态
        registry.docs["d1"]["status"] = "deleted"
        svc = KnowledgeLifecycleService(registry, make_audit([]))
        # get 时已是 deleted → 状态机直接拒绝（源状态不归管辖）
        r = svc.transition("d1", "deprecated")
        assert r["ok"] is False

    def test_cas_zero_rowcount(self):
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "active"}})
        orig = registry.transition_status_by_doc_id

        def race(doc_id, new_status, allowed_from):
            registry.docs["d1"]["status"] = "pending_review"  # 并发改走
            return orig(doc_id, new_status, allowed_from)

        registry.transition_status_by_doc_id = race
        svc = KnowledgeLifecycleService(registry, make_audit([]))
        r = svc.transition("d1", "deprecated")
        assert r["ok"] is False and "重试" in r["error"]

    def test_expire_at_only_on_active(self):
        store: list = []
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "deprecated"}})
        svc = KnowledgeLifecycleService(registry, make_audit(store))
        r = svc.set_expire_at("d1", "2026-12-31")
        assert r["ok"] is False and "active" in r["error"]

    def test_expire_at_sets_and_audits(self):
        store: list = []
        registry = StubRegistry({"d1": {"doc_id": "d1", "status": "active"}})
        svc = KnowledgeLifecycleService(registry, make_audit(store))
        r = svc.set_expire_at("d1", "2026-12-31", actor="employee:a")
        assert r["ok"] is True
        assert registry.docs["d1"]["expire_at"] == "2026-12-31"
        inserts = [p for sql, p in store if sql.startswith("INSERT INTO ai.knowledge_lifecycle_events")]
        assert len(inserts) == 1 and inserts[0][3] == "expire_at_set"


# ── 检索过滤集（C2：过期知识检索 0 命中的过滤来源）──

class FakeFilterRegistry:
    instances: list["FakeFilterRegistry"] = []

    def __init__(self, *args, **kwargs):
        self.pending = []
        self.deprecated = []
        self.expired = []
        FakeFilterRegistry.instances.append(self)

    def list_by_statuses(self, statuses):
        rows = []
        if "pending_review" in statuses:
            rows += self.pending
        if "deprecated" in statuses:
            rows += self.deprecated
        return rows

    def list_expired(self, now=None):
        return self.expired


class TestRetrievalBlockedSet:
    def setup_method(self):
        from backend.rag.retrieval import hybrid
        hybrid.invalidate_pending_review_cache()
        FakeFilterRegistry.instances = []
        self._hybrid = hybrid

    def test_blocked_set_includes_review_deprecated_expired(self, monkeypatch):
        import backend.rag.indexing.doc_registry as dr_module

        def factory(*args, **kwargs):
            reg = FakeFilterRegistry(*args, **kwargs)
            reg.pending = [{"doc_id": "p1"}]
            reg.deprecated = [{"doc_id": "dep1"}]
            reg.expired = [{"doc_id": "exp1"}]
            return reg

        monkeypatch.setattr(dr_module, "DocumentRegistry", factory)
        blocked = self._hybrid._pending_review_doc_ids()
        assert {"p1", "dep1", "exp1"} <= blocked

    def test_cache_hit_skips_registry(self, monkeypatch):
        import backend.rag.indexing.doc_registry as dr_module
        monkeypatch.setattr(dr_module, "DocumentRegistry", FakeFilterRegistry)
        first = self._hybrid._pending_review_doc_ids()
        second = self._hybrid._pending_review_doc_ids()
        assert first is second
        assert len(FakeFilterRegistry.instances) == 1

    def test_invalidate_forces_refresh(self, monkeypatch):
        import backend.rag.indexing.doc_registry as dr_module
        monkeypatch.setattr(dr_module, "DocumentRegistry", FakeFilterRegistry)
        self._hybrid._pending_review_doc_ids()
        self._hybrid.invalidate_pending_review_cache()
        self._hybrid._pending_review_doc_ids()
        assert len(FakeFilterRegistry.instances) == 2

    def test_filter_drops_blocked_chunks(self, monkeypatch):
        import backend.rag.indexing.doc_registry as dr_module

        def factory(*args, **kwargs):
            reg = FakeFilterRegistry(*args, **kwargs)
            reg.deprecated = [{"doc_id": "dep1"}]
            return reg

        monkeypatch.setattr(dr_module, "DocumentRegistry", factory)

        class Doc:
            def __init__(self, doc_id):
                self.metadata = {"doc_id": doc_id}

        kept, dropped = Doc("ok1"), Doc("dep1")
        result = self._hybrid._filter_review_blocked([kept, dropped])
        assert kept in result and dropped not in result
