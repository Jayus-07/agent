"""tests/security/test_tool_approval.py — 写操作工具审批门测试

覆盖（内存存储路径，不依赖 DB）:
1. auto 模式直接放行
2. required 模式首次调用 → pending 提示 + 建单
3. 同指纹重复调用 → 复用同一审批单（幂等）
4. 批准后重试同指纹 → 放行并标记 executed
5. TTL 过期 → 重新建单
6. 驳回 → 不放行
7. 不同指纹 → 不同审批单
"""
import pytest

from backend.security import tool_approval
from backend.security.tool_approval import (
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_PENDING,
    STATUS_REJECTED,
    _MemoryApprovalStore,
    ensure_approved,
)


@pytest.fixture()
def memory_store():
    """强制使用全新内存存储（隔离 DB，测试可重复）。"""
    store = _MemoryApprovalStore()
    tool_approval._store = store
    yield store
    tool_approval._store = None


@pytest.fixture(autouse=True)
def required_mode(monkeypatch):
    monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_MODE", "required")


class TestAutoMode:
    def test_auto_mode_allows_without_request(self, monkeypatch, memory_store):
        monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_MODE", "auto")
        result = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        assert result is None
        assert memory_store.list(limit=10) == []


class TestRequiredMode:
    def test_first_call_creates_pending(self, memory_store):
        result = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        assert result is not None
        assert "需要人工审批" in result
        assert "审批单号" in result
        pending = memory_store.list(status=STATUS_PENDING, limit=10)
        assert len(pending) == 1
        assert pending[0]["tool_name"] == "send_email"
        assert pending[0]["user_id"] == "u1"

    def test_same_fingerprint_reuses_request(self, memory_store):
        """同指纹重复触发复用单号（不重复建单）。"""
        r1 = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        r2 = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        assert r1 == r2
        assert len(memory_store.list(status=STATUS_PENDING, limit=10)) == 1

    def test_different_fingerprint_creates_new(self, memory_store):
        ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        ensure_approved("send_email", "send", "u1", {"to": "other@b.c"})
        assert len(memory_store.list(status=STATUS_PENDING, limit=10)) == 2

    def test_approve_then_retry_executes(self, memory_store):
        r1 = ensure_approved("data_collection", "write_db", "u1",
                             {"source": "products", "target_table": "stg"})
        rid = r1.split("审批单号: `")[1].split("`")[0]
        rec = tool_approval.decide_request(rid, approve=True, reviewer="boss")
        assert rec["status"] == STATUS_APPROVED

        # 批准后重试同指纹 → 放行
        assert ensure_approved("data_collection", "write_db", "u1",
                               {"source": "products", "target_table": "stg"}) is None
        # 消费后标记 executed
        assert memory_store.list(status=STATUS_EXECUTED, limit=10)[0]["id"] == rid

    def test_rejected_does_not_pass(self, memory_store):
        r1 = ensure_approved("export_csv", "export", "u1", {"question": "q"})
        rid = r1.split("审批单号: `")[1].split("`")[0]
        tool_approval.decide_request(rid, approve=False, reviewer="boss")
        assert memory_store.list(status=STATUS_REJECTED, limit=10)
        result = ensure_approved("export_csv", "export", "u1", {"question": "q"})
        # 驳回后旧单不再生效：再次触发建新单（新 pending），不放行
        assert result is not None
        assert result != r1

    def test_ttl_expiry_creates_new_request(self, memory_store, monkeypatch):
        r1 = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        rid = r1.split("审批单号: `")[1].split("`")[0]
        tool_approval.decide_request(rid, approve=True, reviewer="boss")
        # TTL 缩到 0 → 批准立即过期 → 重新建单
        monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_TTL_SECONDS", 0)
        result = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        assert result is not None

    def test_decide_twice_returns_none(self, memory_store):
        r1 = ensure_approved("send_email", "send", "u1", {"to": "a@b.c"})
        rid = r1.split("审批单号: `")[1].split("`")[0]
        assert tool_approval.decide_request(rid, True, "boss") is not None
        # 已决定的单再次决定 → None
        assert tool_approval.decide_request(rid, False, "boss") is None

    def test_decide_unknown_id(self, memory_store):
        assert tool_approval.decide_request("no-such-id", True, "boss") is None
