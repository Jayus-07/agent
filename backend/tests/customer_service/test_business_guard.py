"""Phase3 STOP D — Business Entity Unique Guard 验收（D1-D23 + R1-R5）。

任务书 §61/§81/§62：真 PostgreSQL（5433 权威库）+ 全真 guard 写入路径
（ConfirmationStore._async_save → repo → 051 partial unique index）。

外部依赖边界：真实 PG；并发用多线程各自独立 event loop/连接
（§19：并发必须由 PostgreSQL 决胜，禁止进程内锁假象）。
"""
from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
from sqlalchemy import text

from backend.config.database import MEMORY_DB_CONFIG
from backend.customer_service.business_guard import (
    BusinessOperationAlreadyActive,
    BusinessOperationAlreadyCompleted,
    BusinessOperationConflict,
    BusinessOperationIdentityMissing,
    compute_identity,
    compute_semantic_fingerprint,
)
from backend.customer_service.confirmation_store import ConfirmationStore

pytestmark = pytest.mark.asyncio

_PREFIX = "stopd-bg-"
_TENANT = "default"


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT to_regclass('customer_service.confirmations'), "
                    "to_regclass('ai.idempotency_records')")
                if any(v is None for v in cursor.fetchone()):
                    pytest.skip("customer_service/ai 表未迁移")
                cursor.execute(
                    "SELECT 1 FROM pg_indexes WHERE indexname="
                    "'uq_cs_confirmations_active_biz_op'")
                if cursor.fetchone() is None:
                    pytest.skip("051 迁移未应用（缺 uq_cs_confirmations_active_biz_op）")
    except Exception as exc:
        pytest.skip(f"PostgreSQL 不可达: {exc}")


@pytest.fixture(autouse=True)
async def _env():
    _require_pg()
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup() -> None:
    async def _run():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _cleanup_sync)
    await _run()


def _cleanup_sync() -> None:
    cfg = dict(MEMORY_DB_CONFIG)
    with psycopg2.connect(**cfg, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM customer_service.confirmations WHERE user_id LIKE %s",
                        (_PREFIX + "%",))
            cur.execute("DELETE FROM ai.idempotency_records WHERE actor_id LIKE %s",
                        (_PREFIX + "%",))
            cur.execute("DELETE FROM customer_service.conversations WHERE conversation_id LIKE %s",
                        (_PREFIX + "%",))
        conn.commit()


def _pending(*, action_id=None, action_type="refund_request", target_type="order",
             target_id="ORDER123", amount=None, risk="high",
             target_status="refund_requested") -> dict:
    pending = {
        "action_id": action_id or str(uuid.uuid4()),
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "risk_level": risk,
        "proposal_text": "请确认是否提交退款申请？（回复「确认」提交）",
        "before_state": {"order_id": target_id, "status": "pending_payment"},
        "after_state": {"order_id": target_id, "status": target_status},
        "requires_confirmation": True,
        "confirmation_state": "pending",
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    if amount is not None:
        pending["before_state"]["amount"] = amount
    return pending


def _save(user_id: str, session_id: str, pending: dict, tenant: str = _TENANT):
    """独立线程 + 独立 event loop/连接执行一次 guard 写入（真并发语义）。"""
    def _run():
        return asyncio.run(ConfirmationStore._async_save(
            user_id, session_id, pending, tenant_id=tenant))
    return _run()


def _save_threaded(user_id: str, session_id: str, pending: dict,
                   tenant: str = _TENANT):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_save, user_id, session_id, pending, tenant).result()


def _db_rows(user_id: str, **filters) -> list[dict]:
    cfg = dict(MEMORY_DB_CONFIG)
    where = "user_id = %(u)s"
    params: dict = {"u": user_id}
    for col, val in filters.items():
        where += f" AND {col} = %({col})s"
        params[col] = val
    with psycopg2.connect(**cfg, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT confirmation_id, state, tenant_id, semantic_fingerprint, "
                f"target_id FROM customer_service.confirmations WHERE {where} "
                f"ORDER BY id", params)
            return [
                {"confirmation_id": r[0], "state": r[1], "tenant_id": r[2],
                 "fingerprint": r[3], "target_id": r[4]}
                for r in cur.fetchall()
            ]


def _set_state(confirmation_id: str, state: str) -> None:
    cfg = dict(MEMORY_DB_CONFIG)
    with psycopg2.connect(**cfg, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE customer_service.confirmations SET state=%s "
                "WHERE confirmation_id=%s", (state, confirmation_id))
        conn.commit()


def _insert_uncertain_ledger(user_id: str, confirmation_id: str) -> None:
    cfg = dict(MEMORY_DB_CONFIG)
    with psycopg2.connect(**cfg, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ai.idempotency_records "
                "(tenant_id, actor_id, operation, client_key, request_hash, "
                " status, error_code) VALUES (%s, %s, 'cs.action.execute', %s, "
                "%s, 'failed', 'IDEMPOTENCY_UNCERTAIN') "
                "ON CONFLICT DO NOTHING",
                (_TENANT, user_id, f"cs_action:{confirmation_id}", "0" * 64))
        conn.commit()


# ── 指纹规范化（无 DB）───────────────────────────────────────────

def test_fingerprint_json_key_order_irrelevant():
    a = _pending(target_id="ORDER123")
    b = dict(reversed(list(a.items())))
    assert compute_semantic_fingerprint(a) == compute_semantic_fingerprint(b)


def test_fingerprint_money_normalization():
    assert (compute_semantic_fingerprint(_pending(amount=100))
            == compute_semantic_fingerprint(_pending(amount="100.00")))
    # §8：语义变化 → 指纹变化（100 vs 50 不得误判同一操作）
    assert (compute_semantic_fingerprint(_pending(amount=100))
            != compute_semantic_fingerprint(_pending(amount=50)))


def test_fingerprint_excludes_technical_identity():
    """§49/§50/D2/D3：confirmation_id（action_id）/措辞不参与指纹。"""
    a = _pending()
    b = _pending()
    b["proposal_text"] = "换个说法：请确认退款。"
    assert a["action_id"] != b["action_id"]
    assert compute_semantic_fingerprint(a) == compute_semantic_fingerprint(b)


def test_fingerprint_different_payload_differs():
    """D7：不同语义操作不得被误杀。"""
    assert (compute_semantic_fingerprint(_pending(amount=100))
            != compute_semantic_fingerprint(_pending(target_id="ORDER456")))
    assert (compute_semantic_fingerprint(_pending(target_status="refund_requested"))
            != compute_semantic_fingerprint(_pending(target_status="returned")))


def test_identity_placeholder_returns_none_and_missing_fail_closed():
    # need_info 占位行（target_id=''）→ 不参与守卫
    assert compute_identity(_pending(target_id=""), _TENANT) is None
    # 正式业务写缺 action_type → fail closed
    broken = _pending()
    broken["action_type"] = ""
    with pytest.raises(BusinessOperationIdentityMissing):
        compute_identity(broken, _TENANT)


# ── DB 守卫路径 ──────────────────────────────────────────────────

async def test_d1_d2_d4_same_semantic_different_confirmation_dedup():
    """D1/D2/D4：同语义、不同 confirmation_id → 只允许一个 active。"""
    user = _PREFIX + "d1"
    session = user + "-conv"
    _save_threaded(user, session, _pending(action_id="conf-A"))
    with pytest.raises(BusinessOperationAlreadyActive) as exc:
        _save_threaded(user, session, _pending(action_id="conf-B"))
    assert exc.value.existing_confirmation_id == "conf-A"
    rows = _db_rows(user)
    active = [r for r in rows if r["state"] in ("pending", "confirmed", "executing", "verifying")]
    assert len(active) == 1 and active[0]["confirmation_id"] == "conf-A"


async def test_d3_recovery_execution_id_keeps_identity():
    """D3/Gate D5：换 execution 语境（重复保存同 action_id）→ 同一业务身份，
    仍被去重（action_id 相同=同 confirmation 的 recovery 重复写）。"""
    user = _PREFIX + "d3"
    session = user + "-conv"
    pending = _pending(action_id="conf-same")
    _save_threaded(user, session, pending)
    # Phase2 recovery 会重放同一 confirmation_id 的创建：同 action_id 幂等
    # 语义下仍只允许一行 active
    with pytest.raises(BusinessOperationAlreadyActive):
        _save_threaded(user, session + "-x", _pending(action_id="conf-same"))
    assert len(_db_rows(user)) == 1


async def test_d4_d6_cross_tenant_no_dedupe():
    """R3/Gate D6：同 entity/action/payload，不同 tenant → 两行均成功。"""
    user = _PREFIX + "d6"
    _save_threaded(user, user + "-a", _pending(action_id="conf-TA"), tenant="tenant-A")
    _save_threaded(user, user + "-b", _pending(action_id="conf-TB"), tenant="tenant-B")
    rows = _db_rows(user)
    assert len(rows) == 2
    assert {r["tenant_id"] for r in rows} == {"tenant-A", "tenant-B"}


async def test_d5_d7_different_entity_action_payload_allowed():
    """D5/D6/D7：不同实体/不同动作/不同金额 → 共存。

    产品语义：同一会话只允许一个 pending（后到覆盖），因此共存断言
    使用不同会话（等价于真实世界的多个并发请求，R2 同口径）。
    """
    user = _PREFIX + "d7"
    _save_threaded(user, user + "-s1", _pending(action_id="c1", target_id="ORDER123",
                                                amount=100))
    _save_threaded(user, user + "-s2", _pending(action_id="c2", target_id="ORDER456",
                                                amount=100))
    _save_threaded(user, user + "-s3", _pending(action_id="c3", target_id="ORDER123",
                                                action_type="return_request", amount=100))
    _save_threaded(user, user + "-s4", _pending(action_id="c4", target_id="ORDER123",
                                                amount=50))
    pending_rows = [r for r in _db_rows(user) if r["state"] == "pending"]
    assert len(pending_rows) == 4


def test_d10_r1_concurrent_create_one_wins():
    """D10/R1/Gate D2+D3：10 个并发连接创建同一语义操作 → 恰好 1 行 active。

    按 §62-R1 原文「10 concurrent connections」直连真实 PG：先建好 10 个
    会话行（FK 前置，不参与竞争），随后每个线程独立 psycopg2 连接执行与
    repo.save 完全同构的 INSERT（完整身份 + pending），并发由 051 partial
    unique index 决胜 —— 1 成功 + 9 unique_violation。
    （不经 asyncio engine：共享 pool 具事件循环亲和性，进程内多 loop 并发
    是测试基建限制，不是 guard 语义的一部分。）
    """
    user = _PREFIX + "d10"
    fingerprint = compute_semantic_fingerprint(_pending())
    # 前置：FK 所需的会话行（幂等预建，不参与竞争）
    cfg = dict(MEMORY_DB_CONFIG)
    with psycopg2.connect(**cfg, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            for i in range(10):
                cur.execute(
                    "INSERT INTO customer_service.conversations "
                    "(conversation_id, user_id, conversation_status) "
                    "VALUES (%s, %s, 'open') "
                    "ON CONFLICT (conversation_id) DO NOTHING",
                    (f"{user}-sess-{i}", user))
        conn.commit()
    insert_sql = (
        "INSERT INTO customer_service.confirmations "
        "(confirmation_id, conversation_id, user_id, action_type, target_type, "
        " target_id, tenant_id, semantic_fingerprint, proposal, state, expires_at) "
        "VALUES (%s, %s, %s, 'refund_request', 'order', 'ORDER123', %s, %s, "
        "%s::jsonb, 'pending', '2099-01-01T00:00:00+00:00')"
    )

    def _insert_one(i: int):
        conn = psycopg2.connect(**cfg, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    insert_sql,
                    (f"conf-r1-{i}", f"{user}-sess-{i}", user,
                     _TENANT, fingerprint,
                     '{"action_type":"refund_request"}'))
            conn.commit()
            return True
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_insert_one, range(10)))
    assert sum(1 for r in results if r) == 1, f"恰好 1 个创建成功，实际 {results}"
    assert sum(1 for r in results if not r) == 9
    active = [r for r in _db_rows(user)
              if r["state"] in ("pending", "confirmed", "executing", "verifying")]
    assert len(active) == 1, f"active 行必须恰好 1，实际 {len(active)}"
    assert active[0]["fingerprint"] == fingerprint
    assert active[0]["tenant_id"] == _TENANT


async def test_d11_d12_r5_failed_releases_guard():
    """D12/R5：definite no-effect failure（无 ledger 记录）→ 释放，可重发。"""
    user = _PREFIX + "d12"
    session = user + "-conv"
    _save_threaded(user, session, _pending(action_id="conf-A"))
    _set_state("conf-A", "failed")
    _save_threaded(user, session, _pending(action_id="conf-B"))
    states = [r["state"] for r in _db_rows(user)]
    assert states.count("pending") == 1 and states.count("failed") == 1


async def test_d13_r4_in_doubt_ledger_blocks():
    """D13/R4/Gate D10：failed 但 ledger UNCERTAIN → 守卫持续占用。"""
    user = _PREFIX + "d13"
    session = user + "-conv"
    _save_threaded(user, session, _pending(action_id="conf-A"))
    _set_state("conf-A", "failed")
    _insert_uncertain_ledger(user, "conf-A")
    with pytest.raises(BusinessOperationAlreadyActive) as exc:
        _save_threaded(user, session, _pending(action_id="conf-B"))
    assert "in_doubt" in exc.value.existing_state


async def test_d14_d15_d16_active_states_block():
    """D14/D15/D16：pending/confirmed/executing/verifying 全部阻止。"""
    user = _PREFIX + "d14"
    session = user + "-conv"
    for state in ("pending", "confirmed", "executing", "verifying"):
        _save_threaded(user, session, _pending(action_id=f"conf-{state}"))
        _set_state(f"conf-{state}", state)
        with pytest.raises(BusinessOperationAlreadyActive):
            _save_threaded(user, session, _pending(action_id=f"conf-new-{state}"))
        _set_state(f"conf-{state}", "expired")  # 释放，供下一态测试


async def test_d17_expired_allows_reissue():
    """D17：expired 释放守卫 → 允许重新发起。"""
    user = _PREFIX + "d17"
    session = user + "-conv"
    _save_threaded(user, session, _pending(action_id="conf-A"))
    _set_state("conf-A", "expired")
    _save_threaded(user, session, _pending(action_id="conf-B"))
    assert len(_db_rows(user)) == 2


async def test_d18_success_terminal_policy():
    """D18/Gate D11：refund_request success 后同语义拒绝；非终态锁定动作可重发。"""
    user = _PREFIX + "d18"
    session = user + "-conv"
    _save_threaded(user, session, _pending(action_id="conf-A"))
    _set_state("conf-A", "success")
    with pytest.raises(BusinessOperationAlreadyCompleted):
        _save_threaded(user, session, _pending(action_id="conf-B"))
    # 非终态锁定动作（return_request）：success 后允许再次发起
    _save_threaded(user, session, _pending(action_id="conf-C",
                                           action_type="return_request"))
    _set_state("conf-C", "success")
    _save_threaded(user, session, _pending(action_id="conf-D",
                                           action_type="return_request"))
    assert len([r for r in _db_rows(user) if r["state"] == "success"]) == 2


async def test_d19_proposal_upgrade_atomic_fingerprint():
    """D19：need_info 占位升级为正式 proposal → 指纹原子补全。"""
    user = _PREFIX + "d19"
    session = user + "-conv"
    proposal_id = f"conf-ph-{uuid.uuid4().hex}"
    placeholder = {
        "action_id": proposal_id, "action_type": "refund_request", "intent": "as_refund",
        "status": "need_info", "missing_slots": ["order_id"], "collected_slots": {},
        "target_type": "order", "target_id": "", "risk_level": "high",
        "requires_confirmation": False, "confirmation_state": "pending",
        "retry_count": 0, "expires_at": "2099-01-01T00:00:00+00:00",
    }
    _save_threaded(user, session, placeholder)
    rows = _db_rows(user)
    assert rows[0]["fingerprint"] is None  # 占位不参与守卫
    assert rows[0]["tenant_id"] == _TENANT

    # 兼容旧版已落库的 need_info 占位记录：租户列可能为空，但父会话仍
    # 必须绑定当前用户与租户；升级时应识别并原子补齐租户与业务指纹。
    with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE customer_service.confirmations SET tenant_id=NULL "
                "WHERE confirmation_id=%s",
                (proposal_id,),
            )

    upgraded = _pending(action_id=proposal_id, target_id="ORDER123", amount=100)
    _save_threaded(user, session, upgraded)
    rows = _db_rows(user)
    assert len(rows) == 1 and rows[0]["target_id"] == "ORDER123"
    assert rows[0]["fingerprint"] == compute_semantic_fingerprint(upgraded)
    assert rows[0]["tenant_id"] == _TENANT


async def test_d20_proposal_update_collision():
    """D20/Gate D12：升级后与另一 active 操作语义等价 → 两层拦截都成立。

    层 1（预检）：B 已 active，A 的升级保存被 BusinessOperationAlreadyActive
    拦截（确定性规则，用户得到明确语义）。
    层 2（DB 决胜）：绕过预检直接驱动同一条 UPDATE（模拟预检后 B 才创建
    的竞态窗口），051 唯一索引在 UPDATE flush 时拒绝 → savepoint 转译为
    BusinessOperationConflict，外层事务（conversation ensure）不被污染。
    """
    user = _PREFIX + "d20"
    session_a = user + "-a"
    session_b = user + "-b"
    placeholder = {
        "action_id": "conf-A", "action_type": "refund_request", "intent": "as_refund",
        "status": "need_info", "missing_slots": ["order_id"], "collected_slots": {},
        "target_type": "order", "target_id": "", "risk_level": "high",
        "requires_confirmation": False, "confirmation_state": "pending",
        "retry_count": 0, "expires_at": "2099-01-01T00:00:00+00:00",
    }
    _save_threaded(user, session_a, placeholder)
    # B 先占用 ORDER123/100 的语义身份
    _save_threaded(user, session_b, _pending(action_id="conf-B", target_id="ORDER123",
                                             amount=100))
    # 层 1：升级保存被预检拦截（不存在两行等价 active）
    with pytest.raises(BusinessOperationAlreadyActive):
        _save_threaded(user, session_a, _pending(action_id="conf-A",
                                                 target_id="ORDER123", amount=100))
    # 层 2：直接驱动 UPDATE 决胜路径（race window 内的 DB 拒绝）
    from backend.customer_service.confirmation_store import _update_with_guard
    from backend.customer_service.repository import ConfirmationRepository
    from backend.memory.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        repo = ConfirmationRepository(db)
        with pytest.raises(BusinessOperationConflict):
            await _update_with_guard(
                db, repo, "conf-A",
                _pending(action_id="conf-A", target_id="ORDER123", amount=100),
                tenant_id=_TENANT,
                fingerprint=compute_semantic_fingerprint(
                    _pending(target_id="ORDER123", amount=100)))
        await db.rollback()
    # 两行等价 active 依然不存在
    active = [r for r in _db_rows(user)
              if r["state"] in ("pending", "confirmed", "executing", "verifying")]
    assert len([r for r in active if r["target_id"] == "ORDER123"]) == 1


async def test_d21_d22_missing_identity_fail_closed(monkeypatch):
    """D21/D22：正式业务写缺身份 → fail closed（严格模式）。"""
    from backend.config import customer_service as cs_config
    monkeypatch.setattr(cs_config, "CS_STORE_STRICT_WRITES", True)
    user = _PREFIX + "d21"
    session = user + "-conv"
    broken = _pending()
    broken["action_type"] = ""
    with pytest.raises(BusinessOperationIdentityMissing):
        _save_threaded(user, session, broken)
    assert _db_rows(user) == []


async def test_r2_two_requests_one_operation():
    """R2：两个独立请求（不同会话/不同 confirmation_id）→ 一个 active 操作。"""
    user = _PREFIX + "r2"
    _save_threaded(user, user + "-s1", _pending(action_id="conf-1"))
    with pytest.raises(BusinessOperationAlreadyActive) as exc:
        _save_threaded(user, user + "-s2", _pending(action_id="conf-2"))
    assert exc.value.existing_confirmation_id == "conf-1"


def test_d23_bypass_scan():
    """D23/Gate D13：production 直接构造 CSConfirmation 的入口只能是 repo。"""
    import pathlib
    backend_dir = pathlib.Path(__file__).resolve().parents[2] / "customer_service"
    offenders = []
    for py in backend_dir.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        rel = str(py)
        if rel.endswith(("repository" + chr(92) + "confirmation_repo.py",
                         "repository/confirmation_repo.py",
                         "models" + chr(92) + "confirmation.py",
                         "models/confirmation.py")):
            continue
        content = py.read_text(encoding="utf-8")
        if "CSConfirmation(" in content:
            offenders.append(rel)
    assert offenders == [], f"存在绕过 guard 的直接写入口: {offenders}"
