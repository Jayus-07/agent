"""缺陷6（2026-09-23）回归：退款/退货动作链四断点收口。

四断点：
  1. 缺订单号自动 fallback "latest"（替用户猜副作用动作的目标对象）
  2. 第二轮纯订单号回复不承接 return/refund intent（落 KB/RAG 拒答）
  3. Query 走 HTTP business service、Action 直查本地 agent_business 的
     split-brain
  4. confirmation 写库早于 conversation row（FK violation）

用例编号对应任务书 5.6 A-J。外部边界（业务网关 / 本地 SQL / DB session）
一律 mock，只验证分支决策与调用序；FK 真实约束与数据源真实性由 5.7
实机验证（真 PG + business-mock）兜底。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.customer_service import confirmation_store as cs_store_module
from backend.customer_service.action import ActionProposal, ActionType
from backend.customer_service.errors import (
    DatabaseError,
    OrderNotFoundError,
)
from backend.customer_service.experts.action import execute_action
from backend.customer_service.risk import RiskLevel
from backend.customer_service.service import after_sales_service
from backend.customer_service.service import refund_service
from backend.customer_service.service.after_sales_service import AfterSalesService
from backend.customer_service.service.refund_service import RefundService


# ── 夹具与桩 ─────────────────────────────────────────────────


class FakeStore:
    """ConfirmationStore 测试替身：记录 save/clear，按脚本回放 load。"""

    def __init__(self, pending=None):
        self.pending = pending
        self.saved = []
        self.cleared = []

    def load(self, user_id, session_id):
        return self.pending

    def save(self, user_id, session_id, pending_action, tenant_id=""):
        self.saved.append(pending_action)
        self.pending = pending_action

    def clear(self, user_id, session_id, *, final_state="cancelled"):
        self.cleared.append(final_state)
        self.pending = None


def _patch_store(monkeypatch, store: FakeStore) -> None:
    monkeypatch.setattr(
        "backend.customer_service.confirmation_store.get_confirmation_store",
        lambda: store,
    )


def _forbid_proposal_services(monkeypatch) -> None:
    """缺槽位场景绝不允许走到 proposal 构建（否则就是又去猜订单了）。"""

    def _boom():
        raise AssertionError("缺槽位时不应调用退款/售后服务构建 proposal")

    monkeypatch.setattr(refund_service, "get_refund_service", _boom)
    monkeypatch.setattr(after_sales_service, "get_after_sales_service", _boom)


def _state(user_id="u1") -> dict:
    return {"user_id": user_id, "session_id": "conv-1"}


def _proposal(action_type: str, order_id: str) -> ActionProposal:
    return ActionProposal(
        action_type=action_type,
        target_type="order",
        target_id=order_id,
        risk_level=RiskLevel.HIGH,
        proposal_text=(
            f"## 退货申请\n\n**订单号:** `{order_id}`\n\n请确认是否提交？"
        ),
    )


def _http_order(order_no="MO-ABC123", status="paid") -> dict:
    return {
        "order_no": order_no,
        "status": status,
        "payment_status": "paid",
        "total_amount": 88.5,
        "item_count": 1,
        "item_summary": "演示商品-A",
        "created_at": "2026-09-01T00:00:00+00:00",
    }


# ── A. 缺槽位：追问，不 latest、不建 proposal ─────────────────


def test_a_missing_order_id_asks_instead_of_latest(monkeypatch):
    store = FakeStore()
    _patch_store(monkeypatch, store)
    _forbid_proposal_services(monkeypatch)

    result = execute_action("我要退货", {"intent": "as_return", "metadata": {}}, _state())

    assert "订单号" in result["response_draft"]
    data = result["data"]
    assert data["pending_action"]["status"] == "need_info"
    assert data["pending_action"]["missing_slots"] == ["order_id"]
    assert store.saved, "need_info 状态必须持久化（跨轮承接）"
    assert store.saved[-1]["action_type"] == "return_request"
    assert "latest" not in result["response_draft"].lower()


def test_a_missing_order_id_never_builds_proposal(monkeypatch):
    """「我要退货」不得产生任何 target_id 非空的 proposal / confirmation。"""
    store = FakeStore()
    _patch_store(monkeypatch, store)
    _forbid_proposal_services(monkeypatch)

    result = execute_action(
        "我要退货", {"intent": "as_refund", "metadata": {}}, _state()
    )

    assert result["data"].get("action_result") is None
    assert result["data"]["pending_action"].get("target_id", "") == ""


# ── B. 补槽承接：第二轮订单号续 return intent ─────────────────


def test_b_slot_fill_resumes_return_intent(monkeypatch):
    need_info = {
        "action_id": "act-1",
        "action_type": "return_request",
        "intent": "as_return",
        "status": "need_info",
        "missing_slots": ["order_id"],
        "collected_slots": {},
        "confirmation_state": "pending_confirmation",
        "retry_count": 0,
    }
    store = FakeStore(pending=need_info)
    _patch_store(monkeypatch, store)
    seen = {}

    class FakeAfterSales:
        def build_return_proposal(self, user_id, order_id, reason=""):
            seen["order_id"] = order_id
            return _proposal(ActionType.RETURN_REQUEST, order_id)

    monkeypatch.setattr(
        after_sales_service, "get_after_sales_service", lambda: FakeAfterSales()
    )

    result = execute_action(
        "MO-1001", {"intent": "k_faq", "metadata": {}}, _state()
    )

    assert seen["order_id"] == "MO-1001", "补槽订单号必须进入 proposal"
    assert "MO-1001" in result["response_draft"]
    upgraded = result["data"]["pending_action"]
    assert upgraded.get("status") != "need_info", "补槽后升级为正式 proposal"
    assert upgraded["target_id"] == "MO-1001"
    assert upgraded["confirmation_state"] == "pending"
    # 覆盖保存（同键升级，不是追加第二条 pending）
    assert store.saved and store.saved[-1]["target_id"] == "MO-1001"


# ── C. 第一轮显式订单：不追问直接校验 ─────────────────────────


def test_c_explicit_order_id_skips_asking(monkeypatch):
    store = FakeStore()
    _patch_store(monkeypatch, store)
    seen = {}

    class FakeAfterSales:
        def build_return_proposal(self, user_id, order_id, reason=""):
            seen["order_id"] = order_id
            return _proposal(ActionType.RETURN_REQUEST, order_id)

    monkeypatch.setattr(
        after_sales_service, "get_after_sales_service", lambda: FakeAfterSales()
    )

    result = execute_action(
        "我要退 MO-1001", {"intent": "as_return", "metadata": {}}, _state()
    )

    assert seen["order_id"] == "MO-1001"
    assert result["data"]["pending_action"].get("status") != "need_info"
    assert not store.saved or store.saved[-1].get("status") != "need_info"


# ── D/E/F. HTTP 网关：action 与 query 同源 ────────────────────


def _patch_http_mode(monkeypatch, mode: str, get_json=None) -> None:
    monkeypatch.setattr(
        "backend.config.customer_service.CS_BUSINESS_GATEWAY_MODE", mode
    )
    # demo 身份映射与网关模式无关，测试中恒等透传
    monkeypatch.setattr(
        "backend.customer_service.service.demo_mode.resolve_user_id",
        lambda user_id: user_id,
    )
    if get_json is not None:
        monkeypatch.setattr(
            "backend.infra.http.business_client.get_json_sync", get_json
        )


def _forbid_local_sql(monkeypatch) -> None:
    def _boom(*a, **k):
        raise AssertionError("http 模式下动作侧不得直查本地 SQL")

    monkeypatch.setattr("backend.sql.executor.execute_sql_struct", _boom)


def test_d_http_gateway_action_hits_business_client(monkeypatch):
    calls = []

    def fake_get_json(path, params=None, **k):
        calls.append((path, params))
        return {"code": 0, "data": {"order": _http_order()}}

    _patch_http_mode(monkeypatch, "http", fake_get_json)
    _forbid_local_sql(monkeypatch)

    order = RefundService()._get_order("u1", "MO-ABC123")
    assert order["order_no"] == "MO-ABC123"
    assert calls and "/business/orders/MO-ABC123" in calls[0][0]

    eligibility = RefundService().check_refund_eligibility("u1", "MO-ABC123")
    assert eligibility.eligible is True
    assert eligibility.order_no == "MO-ABC123"


def test_d_http_gateway_return_eligibility_same_source(monkeypatch):
    def fake_get_json(path, params=None, **k):
        return {"code": 0, "data": {"order": _http_order(status="shipped")}}

    _patch_http_mode(monkeypatch, "http", fake_get_json)
    _forbid_local_sql(monkeypatch)

    eligibility = AfterSalesService().check_return_eligibility("u1", "MO-ABC123")
    assert eligibility.eligible is True
    assert eligibility.order_id == "MO-ABC123"


def test_e_http_404_is_order_not_found_without_db_fallback(monkeypatch):
    from backend.infra.http.business_client import BusinessServiceError

    def fake_get_json(path, params=None, **k):
        raise BusinessServiceError("order not found", 404)

    _patch_http_mode(monkeypatch, "http", fake_get_json)
    _forbid_local_sql(monkeypatch)

    with pytest.raises(OrderNotFoundError):
        RefundService()._get_order("u1", "MO-NOPE")
    with pytest.raises(OrderNotFoundError):
        AfterSalesService()._get_order("u1", "MO-NOPE")


def test_f_http_outage_is_business_unavailable_without_db_fallback(monkeypatch):
    from backend.infra.http.business_client import BusinessServiceError

    def fake_get_json(path, params=None, **k):
        raise BusinessServiceError("ConnectError", 0)

    _patch_http_mode(monkeypatch, "http", fake_get_json)
    _forbid_local_sql(monkeypatch)

    with pytest.raises(DatabaseError):
        RefundService()._get_order("u1", "MO-ABC123")
    with pytest.raises(DatabaseError):
        AfterSalesService()._get_order("u1", "MO-ABC123")


def test_g_sandbox_mode_still_uses_local_repository(monkeypatch):
    http_calls = []

    def fake_get_json(path, params=None, **k):
        http_calls.append(path)
        return {"code": 0, "data": {"order": _http_order()}}

    _patch_http_mode(monkeypatch, "sandbox", fake_get_json)

    def fake_sql(sql, params=None):
        assert params["order_id"] == "MO-LOCAL1"
        row = {
            "id": 7,
            "order_no": "MO-LOCAL1",
            "customer_id": "u1",
            "total_amount": 66.0,
            "status": "paid",
            "payment_status": "paid",
            "created_at": datetime.now(timezone.utc),
        }
        result = MagicMock()
        result.status = "success"
        result.rows = [row]
        return result

    monkeypatch.setattr("backend.sql.executor.execute_sql_struct", fake_sql)

    order = RefundService()._get_order("u1", "MO-LOCAL1")
    assert order["order_no"] == "MO-LOCAL1"
    assert not http_calls, "sandbox 模式不得发 HTTP 请求（本地开发模式）"


# ── H/I. FK 生命周期：confirmation 前必须 ensure conversation ──


class _FakeSessionCtx:
    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def flush(self):
        self._log.append("flush")

    async def commit(self):
        self._log.append("commit")

    def begin_nested(self):
        # STOP D：_async_save 的会话 ensure 走 savepoint（并发双提交
        # 竞争补强）—— fake 会话无真事务，语义等价的穿透 stub。
        return _FakeNested()


class _FakeNested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRepo:
    """load 首次返回 None、之后返回既有行 —— 模拟「首插后升级」生命周期。"""

    def __init__(self, session, existing=None):
        self._existing = existing
        self.saved = []
        self.updated = []

    async def load(self, user_id, conversation_id, *, tenant_id=None):
        if self.saved:
            row = MagicMock()
            row.confirmation_id = "conf-1"
            row.proposal_version = 1
            return row
        return self._existing

    async def save(self, user_id, conversation_id, pending_action, tenant_id="", semantic_fingerprint=None):
        self.saved.append((user_id, conversation_id))
        return MagicMock()

    async def update_proposal(self, confirmation_id, pending_action, tenant_id="", semantic_fingerprint=None, proposal_version=None):
        self.updated.append((confirmation_id, proposal_version))
        return True


def _patch_db_layer(monkeypatch, existing=None):
    log = []
    fake_repo = _FakeRepo(None, existing=existing)

    class _Factory:
        def __call__(self):
            return _FakeSessionCtx(log)

    class FakeConvMgr:
        def __init__(self, db):
            pass

        async def get_or_create(self, conversation_id, user_id, **kw):
            # STOP CS-A P0-2：save 链路现显式传 tenant_id
            log.append(("ensure", conversation_id, user_id, kw.get("tenant_id", "")))
            return object(), True

    monkeypatch.setattr(
        "backend.memory.database.AsyncSessionLocal", _Factory()
    )
    monkeypatch.setattr(
        "backend.customer_service.managers.conversation_manager.ConversationManager",
        FakeConvMgr,
    )
    monkeypatch.setattr(
        "backend.customer_service.repository.ConfirmationRepository",
        lambda session: fake_repo,
    )
    return log, fake_repo


@pytest.mark.asyncio
async def test_h_confirmation_save_ensures_conversation_first(monkeypatch):
    """conversation row 尚未落库时写 confirmation：先幂等 ensure（同事务
    flush），再插 confirmation —— FK 不变量由调用序锁定。"""
    log, fake_repo = _patch_db_layer(monkeypatch, existing=None)

    await cs_store_module.ConfirmationStore._async_save(
        "u1", "conv-1", {"action_id": "act-9", "confirmation_state": "pending"}
    )

    # STOP CS-A P0-2：save 链路 ensure 会话行时显式带 tenant（本链路测试
    # 上下文无租户 → 空串透传，ConversationManager 侧 fail-closed 兜底）
    assert log[0] in (("ensure", "conv-1", "u1"), ("ensure", "conv-1", "u1", "")) or (
        log[0][:3] == ("ensure", "conv-1", "u1")
    )
    assert "flush" in log[: log.index("commit")]
    assert fake_repo.saved == [("u1", "conv-1")]
    assert log.index("commit") > log.index("flush"), "ensure 必须与 insert 同事务且先提交"


@pytest.mark.asyncio
async def test_i_ensure_is_idempotent_and_updates_instead_of_duplicate(monkeypatch):
    """同 (user, conversation) 已有 pending 行：升级走 update_proposal，
    不插入第二行；ensure 幂等先查后插不重复建 conversation。"""
    log, fake_repo = _patch_db_layer(monkeypatch, existing=None)

    payload = {"action_id": "act-1", "confirmation_state": "pending",
               "target_id": "MO-1001"}
    await cs_store_module.ConfirmationStore._async_save("u1", "conv-1", payload)
    await cs_store_module.ConfirmationStore._async_save("u1", "conv-1", payload)

    ensure_calls = [e for e in log if isinstance(e, tuple) and e[0] == "ensure"]
    assert len(ensure_calls) == 2
    assert fake_repo.saved == [("u1", "conv-1")], "首次插入且仅一次"
    assert fake_repo.updated == [("conf-1", 2)], "第二次保存升级既有行而非重复插入"


# ── J. Step6 保护：非订单号回复不补槽（不实现 pronoun 改写）────


def test_j_non_order_reply_reasks_without_swallowing(monkeypatch):
    need_info = {
        "action_id": "act-1",
        "action_type": "return_request",
        "intent": "as_return",
        "status": "need_info",
        "missing_slots": ["order_id"],
        "confirmation_state": "pending_confirmation",
        "retry_count": 0,
    }
    store = FakeStore(pending=need_info)
    _patch_store(monkeypatch, store)
    _forbid_proposal_services(monkeypatch)

    result = execute_action(
        # B6 语义对齐：「算了，帮我查…」含取消词，与 proposal 阶段一致判
        # CANCEL（见 B2 用例）；本用例锁「纯新问题不吞掉→继续追问」，
        # 消息不再携带取消词。
        "帮我查一下保修政策", {"intent": "k_faq", "metadata": {}}, _state()
    )

    assert result["data"]["pending_action"]["retry_count"] == 1
    assert result["data"]["pending_action"]["status"] == "need_info"
    assert "订单号" in result["response_draft"], "无法识别槽位时继续追问，不强行解析"
