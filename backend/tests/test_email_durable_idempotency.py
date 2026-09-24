"""tests/test_email_durable_idempotency.py — Phase3 STOP C：E1/E2 执行级验证。

场景（任务书 §38 矩阵的执行级部分 + §39 R2/R3）：
  C3   duplicate delivery：同 logical key 两次完整调用 → SMTP 实发 = 1
  C6   NOT_SENT（连接失败）→ FAILED 可接管重试，恢复后成功
  C7   UNKNOWN after dispatch（DATA 阶段中断）→ UNCERTAIN 保守阻断，重发 = 0
  C8   SMTP accepted + crash（fn 成功后 BaseException 逃逸 → 行停 running）
       → 过期后同 key 阻断，服务器视角 effect = 1、automatic resend = 0
  C9   "Redis lease 过期重开"场景不存在：链路无 Redis，durable ledger 唯一权威
  E2   无租户直调 → IdempotencyContextMissing（旁路封死）
  R2   SMTP transport-level fault injection（可控 fake transport，非真实
       生产 SMTP——任务书 §39 明确允许并要求如此标注）

真实组件：真 PG（MEMORY_DB_CONFIG，pytest 测试库）+ 真实 email.py 副作用
边界函数（_send_email_effect）+ 独立隔离 ledger 表（pgtest 隔离纪律）。
"""
from __future__ import annotations

import smtplib
import uuid

import psycopg
import pytest

from backend.shared.idempotency import (
    IdempotencyContextMissing,
    IdempotencyExecutor,
    IdempotencyUnavailable,
    PostgresIdempotencyLedgerStore,
    SideEffectOutcomeUnknown,
    canonical_fingerprint,
)
from backend.shared.provider_idempotency import ProviderEffectError

LEDGER_TABLE = "stopc_idempotency_records"

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    operation text NOT NULL,
    client_key text NOT NULL,
    request_hash character(64) NOT NULL,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed')),
    result jsonb,
    error_code text,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_id uuid,
    lease_expires_at timestamptz,
    expires_at timestamptz,
    owner_execution_id varchar(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id, operation, client_key)
)
"""


def _factory():
    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    return psycopg.connect(
        f"postgresql://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['dbname']}")


@pytest.fixture()
def ledger_env():
    pytest.importorskip("psycopg")
    try:
        with _factory() as conn, conn.cursor() as cur:
            cur.execute(_TABLE_DDL.format(table=LEDGER_TABLE))
            cur.execute(f"DELETE FROM {LEDGER_TABLE}")
            conn.commit()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"PG 不可达，跳过 email durable idempotency 测试: {e}")
    yield _factory
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {LEDGER_TABLE}")
        conn.commit()


def _store(**kw):
    return PostgresIdempotencyLedgerStore(_factory, table=LEDGER_TABLE, **kw)


def _run(ledger_env, payload, tenant, actor, key):
    """复刻 send_email_tool 有租户分支的完整调用形态（executor+边界函数+
    租户 ContextVar 与生产 _bind_task_identity 一致）。"""
    from backend.shared.idempotency import IdempotencyKey
    from backend.tools.session import _current_tenant_id

    token = _current_tenant_id.set(tenant)
    try:
        executor = IdempotencyExecutor(_store())
        return executor.execute(
            IdempotencyKey(tenant_id=tenant, actor_id=actor,
                           operation="email.send", client_key=key),
            payload,
            lambda: {"message": _effect(payload)})
    finally:
        _current_tenant_id.reset(token)


_PAYLOAD = {"to": "a@example.com", "cc": "", "subject": "STOP C",
            "body": "idempotency probe"}


def _effect(payload: dict) -> str:
    """email.py 的副作用边界（同 send_email_tool 所用函数）。"""
    from backend.tools.email import _send_email_effect

    return _send_email_effect(payload["to"], payload["subject"],
                              payload["body"], payload["cc"])


# ── 可控 SMTP transport（transport-level fault injection，非真实生产 SMTP）──
class FakeSMTP:
    """服务器视角可数 fake：sent_server_accepted = 服务器已接受的消息数。"""

    mode = "ok"                # ok | disconnect_data | connect_fail | refused
    sent_server_accepted = 0
    sendmail_calls = 0

    def __init__(self, host, port, timeout=None):
        if FakeSMTP.mode == "connect_fail":
            raise ConnectionRefusedError("fake: connection refused")

    def starttls(self, context=None):
        return None

    def login(self, user, password):
        return None

    def sendmail(self, from_addr, to_addrs, msg):
        FakeSMTP.sendmail_calls += 1
        if FakeSMTP.mode == "disconnect_data":
            # 服务器已接受消息（effect 已发生），随后连接丢失——客户端无确定结果
            FakeSMTP.sent_server_accepted += 1
            raise smtplib.SMTPServerDisconnected("lost after DATA")
        if FakeSMTP.mode == "refused":
            raise smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")})
        FakeSMTP.sent_server_accepted += 1
        return {}

    def quit(self):
        return None


@pytest.fixture()
def fake_smtp(monkeypatch):
    FakeSMTP.mode = "ok"
    FakeSMTP.sent_server_accepted = 0
    FakeSMTP.sendmail_calls = 0
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("backend.tools.email._SENT_FINGERPRINTS", {})
    return FakeSMTP


# ── C3：duplicate delivery → 实发 1 ──────────────────────────
def test_c3_duplicate_delivery_effect_once(ledger_env, fake_smtp):
    r1 = _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-1")
    assert "已发送" in r1["message"]
    r2 = _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-1")
    assert r2["message"] == r1["message"]  # ledger 重放同一结果
    assert fake_smtp.sent_server_accepted == 1


# ── C6：NOT_SENT → FAILED 可接管重试 ─────────────────────────
def test_c6_not_sent_retryable_then_recovers(ledger_env, fake_smtp):
    fake_smtp.mode = "connect_fail"
    with pytest.raises(ProviderEffectError):
        _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-2")
    assert fake_smtp.sent_server_accepted == 0
    # broker 恢复：同 key 接管 failed 行重试成功
    fake_smtp.mode = "ok"
    r = _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-2")
    assert "已发送" in r["message"]
    assert fake_smtp.sent_server_accepted == 1


# ── C7：UNKNOWN after dispatch → IN_DOUBT，无自动重发 ─────────
def test_c7_unknown_after_dispatch_blocked_forever(ledger_env, fake_smtp):
    fake_smtp.mode = "disconnect_data"
    with pytest.raises(SideEffectOutcomeUnknown):
        _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-3")
    assert fake_smtp.sent_server_accepted == 1  # 服务器视角 effect 已发生
    # 同 key 再来（retry/重投/Redis 过期后重试——链路无 Redis，一律阻断）
    fake_smtp.mode = "ok"
    with pytest.raises(IdempotencyUnavailable):
        _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-3")
    assert fake_smtp.sent_server_accepted == 1  # 无重复发送
    assert fake_smtp.sendmail_calls == 1        # 自动重试次数 = 0


# ── C8/C9：SMTP accepted + crash → durable uncertain 阻断 ────
def test_c8_c9_accepted_then_crash_no_resend(ledger_env, fake_smtp):
    """fn 内 sendmail 成功后进程崩溃（BaseException 逃逸 executor 的
    except Exception）→ 行停 running（crash 世界态等价注入）；
    租约过期后（原 Redis lease 等价窗口）同 key 仍被 durable ledger 阻断。"""
    from backend.shared.idempotency import IdempotencyKey

    fake_smtp.mode = "ok"

    def _crash_after_send():
        _effect(_PAYLOAD)                 # SMTP 已接受（sent+1）
        raise KeyboardInterrupt("process dies before ledger complete")

    executor = IdempotencyExecutor(_store())
    with pytest.raises(KeyboardInterrupt):
        executor.execute(
            IdempotencyKey(tenant_id="t1", actor_id="u1",
                           operation="email.send", client_key="biz-key-4"),
            _PAYLOAD, _crash_after_send)
    assert fake_smtp.sent_server_accepted == 1

    # 模拟任意长等待（含原 Redis lease 60s 过期）：直接把租约拨到过去
    with _factory() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE {LEDGER_TABLE} SET lease_expires_at = now() - interval '1 hour' "
            "WHERE client_key = 'biz-key-4'")
        conn.commit()

    # crash 恢复后同一 logical email 请求再来：不得再次发送
    with pytest.raises(IdempotencyUnavailable):
        _run(ledger_env, _PAYLOAD, "t1", "u1", "biz-key-4")
    assert fake_smtp.sent_server_accepted == 1  # effect = 1，resend = 0

    # 人工裁决 not_executed（STOP E 的 resolve 原语）显式解锁后才可重试
    from backend.shared.idempotency import resolve_stale_side_effect

    resolved = resolve_stale_side_effect(
        tenant_id="t1", actor_id="u1", operation="email.send",
        client_key="biz-key-4", decision="not_executed",
        connection_factory=_factory, table=LEDGER_TABLE)
    assert resolved, "人工裁决应解锁 stale 行"


# ── E2：无租户直调封死 ────────────────────────────────────────
def test_e2_direct_bypass_fail_closed(monkeypatch):
    import backend.config as cfg
    from backend.tools import email as email_mod

    # 无租户 ContextVar + SMTP 配置齐全（smtp 引擎）→ 必须在副作用前拒绝
    monkeypatch.setattr(cfg, "SMTP_USER", "u")
    monkeypatch.setattr(cfg, "SMTP_PASSWORD", "p")
    monkeypatch.setattr(cfg, "EMAIL_ENGINE", "smtp")

    def _no_tenant():
        return ""

    monkeypatch.setattr("backend.tools.session.get_tool_tenant_id", _no_tenant)
    monkeypatch.setattr("backend.tools.session.get_tool_user_id", lambda: "")
    monkeypatch.setattr("backend.tools.session.get_tool_idempotency_key",
                        lambda: "")
    monkeypatch.setattr("backend.security.tool_approval.ensure_approved",
                        lambda *a, **k: None)

    with pytest.raises(IdempotencyContextMissing):
        email_mod.send_email_tool.invoke(
            {"to": "a@example.com", "subject": "s", "body": "b"})


# ── 租户隔离：同 client_key 不同租户 = 不同 logical effect ────
def test_tenant_isolation_on_logical_key(ledger_env, fake_smtp):
    r1 = _run(ledger_env, _PAYLOAD, "tenant-a", "u1", "shared-key")
    r2 = _run(ledger_env, _PAYLOAD, "tenant-b", "u1", "shared-key")
    assert "已发送" in r1["message"] and "已发送" in r2["message"]
    assert fake_smtp.sent_server_accepted == 2  # 互不串扰，各自执行一次
