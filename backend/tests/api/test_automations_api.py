# -*- coding: utf-8 -*-
"""用户自动任务 + Webhook 触发 API（决策 #27）。

真实依赖：PostgreSQL（迁移 123 已落库，-m pg）。覆盖：
  - webhook：签名正确过 / 错签 401（统一文案）/ X-Event-Id 重放去重
    （skipped_duplicate，不重复派发）/ 租户绑定不匹配 404（与不存在同文案，
    不泄露存在性）/ 停用 404
  - cron：到期派发 + 同窗口不重复 + 非到期跳过（last_run_at 控制）
  - 用户 CRUD 隔离（A 看不到/改不了/删不了 B 的行）+ 同名冲突 409 +
    confirm_policy 非 auto 400 + webhook 密钥只返回一次
  - run_user_automation：执行链 mock 成功/失败 → 事件状态正确
  - admin：overview 计数 + 停用开关 + 非管理员 403

密钥：SECRETS_ENCRYPTION_KEY 由 fixture 注入测试专用 Fernet key（crypto
缓存同步失效）；执行链派发（create_task/enqueue_task/apply_async）一律
monkeypatch 打桩——本文件不真发 LLM / broker。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import automations as automations_routes
from backend.services import automations

pytestmark = pytest.mark.pg

TENANT = "e2e-automations-tenant"
TENANT_B = "e2e-automations-tenant-b"
USER_A = "u-automations-a"
USER_B = "u-automations-b"

USER_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_A,
    "X-User-Roles": "editor",
    "X-Tenant-Id": TENANT,
}
USER_B_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_B,
    "X-User-Roles": "editor",
    "X-Tenant-Id": TENANT_B,
}
ADMIN_HEADERS = {
    "X-Auth-Type": "jwt",
    "X-User-Id": "u-automations-admin",
    "X-User-Roles": "admin",
    "X-Tenant-Id": TENANT,
}
EDITOR_HEADERS = {  # 非管理员对照
    "X-Auth-Type": "jwt",
    "X-User-Id": USER_A,
    "X-User-Roles": "editor",
    "X-Tenant-Id": TENANT,
}


def _db_exec(sql: str, params: dict | None = None) -> None:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(sql, params or {})
        raw.commit()
    finally:
        raw.close()


class _FakeRecord:
    """task_service.create_task 返回的 TaskRecord 最小替身（run 链只读 .id）。"""

    def __init__(self, task_id: str):
        self.id = task_id


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    """测试专用加密主密钥（crypto 进程内缓存同步失效）。"""
    from backend.shared import crypto

    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto.invalidate_fernet_cache()
    yield
    crypto.invalidate_fernet_cache()


@pytest.fixture(autouse=True)
def _clean():
    """按租户清理（FK 级联：删 automation 连带删事件）。"""
    for tenant in (TENANT, TENANT_B):
        _db_exec("DELETE FROM ai.user_automations WHERE tenant_id = %(t)s",
                 {"t": tenant})
    yield
    for tenant in (TENANT, TENANT_B):
        _db_exec("DELETE FROM ai.user_automations WHERE tenant_id = %(t)s",
                 {"t": tenant})


@pytest.fixture()
def dispatch_stub(monkeypatch):
    """打桩执行链派发：记录 (automation_id, event_id) 调用，不真发 broker。"""
    calls: list[tuple[str, int]] = []

    def _fake_apply_async(args, **_kwargs):
        calls.append(tuple(args))
        return None

    monkeypatch.setattr(
        "backend.tasks.automation_tasks.run_user_automation.apply_async",
        lambda args, **kw: _fake_apply_async(args, **kw))
    return calls


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(automations_routes.webhook_router)
    app.include_router(automations_routes.router)
    app.include_router(automations_routes.admin_router)
    return TestClient(app, raise_server_exceptions=False)


def _make_cron(*, user_id: str = USER_A, tenant_id: str = TENANT,
               name: str = "nightly", cron_expr: str = "* * * * *",
               enabled: bool = True) -> dict:
    record, _secret = automations.create_automation(
        user_id=user_id, tenant_id=tenant_id, name=name,
        trigger_type="cron", cron_expr=cron_expr,
        payload={"question": "今天的经营日报"}, )
    if not enabled:
        automations.set_enabled(record["id"], enabled=False,
                                user_id=user_id, tenant_id=tenant_id)
        record = automations.get_automation(record["id"], user_id=user_id,
                                            tenant_id=tenant_id)
    return record


def _make_webhook(*, user_id: str = USER_A, tenant_id: str = TENANT,
                  name: str = "hook", enabled: bool = True,
                  event_id: str = "") -> tuple[dict, str]:
    record, secret = automations.create_automation(
        user_id=user_id, tenant_id=tenant_id, name=name,
        trigger_type="webhook", payload={"question": "查一下库存"})
    if not enabled:
        automations.set_enabled(record["id"], enabled=False,
                                user_id=user_id, tenant_id=tenant_id)
        record = automations.get_automation(record["id"], user_id=user_id,
                                            tenant_id=tenant_id)
    return record, secret


def _signed_headers(secret: str, body: bytes, event_id: str = "") -> dict:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-Signature": sig, "Content-Type": "application/json"}
    if event_id:
        headers["X-Event-Id"] = event_id
    return headers


# ═══════════════════════════════════════════════════
# cron 匹配器（纯函数）
# ═══════════════════════════════════════════════════

def test_cron_matcher_basic_and_ranges():
    utc = timezone.utc
    assert automations.cron_matches("* * * * *",
                                    datetime(2026, 10, 28, 8, 30, tzinfo=utc))
    assert automations.cron_matches("30 8 * * *",
                                    datetime(2026, 10, 28, 8, 30, tzinfo=utc))
    assert not automations.cron_matches("30 8 * * *",
                                        datetime(2026, 10, 28, 8, 31, tzinfo=utc))
    # 列表/区间/步长
    assert automations.cron_matches("0,15,30 8-10 * * *",
                                    datetime(2026, 10, 28, 9, 15, tzinfo=utc))
    assert not automations.cron_matches("*/10 * * * *",
                                        datetime(2026, 10, 28, 8, 7, tzinfo=utc))
    assert automations.cron_matches("*/10 * * * *",
                                    datetime(2026, 10, 28, 8, 20, tzinfo=utc))


def test_cron_matcher_dow_and_validation():
    utc = timezone.utc
    # 2026-10-28 是周三（cron dow=3）；周日=0
    assert automations.cron_matches("0 8 * * 3",
                                    datetime(2026, 10, 28, 8, 0, tzinfo=utc))
    assert not automations.cron_matches("0 8 * * 3",
                                        datetime(2026, 10, 27, 8, 0, tzinfo=utc))
    # 7 归一化为周日（0）
    assert automations.cron_matches("0 8 * * 7",
                                    datetime(2026, 11, 1, 8, 0, tzinfo=utc))
    # 日/周双受限 → OR（vixie 语义）
    assert automations.cron_matches("0 8 1 * 3",
                                    datetime(2026, 11, 4, 8, 0, tzinfo=utc))  # 周三
    assert automations.cron_matches("0 8 1 * 3",
                                    datetime(2026, 11, 1, 8, 0, tzinfo=utc))  # 1 号
    assert not automations.cron_matches("0 8 1 * 3",
                                        datetime(2026, 11, 3, 8, 0, tzinfo=utc))
    # 非法表达式
    for bad in ("", "60 * * * *", "* * *", "a * * * *", "* * * * 9", "*/0 * * * *"):
        with pytest.raises(automations.AutomationValidationError):
            automations.parse_cron(bad)


# ═══════════════════════════════════════════════════
# cron 扫描（到期派发 / 同窗口不重复 / 非到期跳过）
# ═══════════════════════════════════════════════════

def test_scan_due_dispatches_and_dedups_window(dispatch_stub):
    record = _make_cron()
    now = datetime(2026, 10, 28, 8, 30, 5, tzinfo=timezone.utc)

    claimed = automations.scan_due_cron_automations(now=now)
    assert [c["automation_id"] for c in claimed] == [record["id"]]
    assert claimed[0]["event_id"] > 0

    # 同一分钟窗口内再扫：last_run_at 已被认领 → 不重复派发
    again = automations.scan_due_cron_automations(now=now)
    assert again == []

    # 下一分钟窗口：重新到期
    nxt = automations.scan_due_cron_automations(
        now=now + timedelta(minutes=1))
    assert [c["automation_id"] for c in nxt] == [record["id"]]

    # 事件行：同窗口唯一（cron:<epoch_minute> 去重键）
    events = automations.list_events(record["id"])
    assert all(e["source"] == "cron" for e in events)
    assert len([e for e in events if e["status"] == "queued"]) == 2


def test_scan_skips_not_due_and_disabled(dispatch_stub):
    # 非到期：last_run_at = 当前窗口 → 跳过
    now = datetime(2026, 10, 28, 8, 30, 0, tzinfo=timezone.utc)
    record = _make_cron(name="already-ran")
    _db_exec(
        "UPDATE ai.user_automations SET last_run_at = %(lr)s WHERE id = %(id)s",
        {"lr": now, "id": record["id"]})
    assert automations.scan_due_cron_automations(now=now) == []

    # 停用：即使到期也跳过
    disabled = _make_cron(name="off", enabled=False)
    assert automations.scan_due_cron_automations(now=now) == []
    row = automations.get_automation(disabled["id"])
    assert row["last_status"] == ""

    # 非法表达式：跳过不抛
    broken = _make_cron(name="broken")
    _db_exec("UPDATE ai.user_automations SET cron_expr = 'bad expr' "
             "WHERE id = %(id)s", {"id": broken["id"]})
    assert automations.scan_due_cron_automations(now=now) == []


# ═══════════════════════════════════════════════════
# Webhook：签名 / 去重 / 租户绑定 / 停用
# ═══════════════════════════════════════════════════

def test_webhook_signature_and_dispatch(client, dispatch_stub):
    record, secret = _make_webhook()
    url = f"/webhooks/automations/{TENANT}/{record['id']}"
    body = b'{"order_id": "o-1"}'

    # 错签 → 401 统一文案（不回显细节）
    bad = client.post(url, content=body,
                      headers={"X-Signature": "deadbeef"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "签名校验失败"
    # 缺签名头 → 401 同文案
    assert client.post(url, content=body).status_code == 401

    # 正确签名 + X-Event-Id → queued + 派发一次
    ok = client.post(url, content=body,
                     headers=_signed_headers(secret, body, event_id="evt-1"))
    assert ok.status_code == 200
    assert ok.json()["status"] == "queued"
    assert len(dispatch_stub) == 1

    # 重放（同 event_id）→ 200 + skipped_duplicate，不再派发
    replay = client.post(url, content=body,
                         headers=_signed_headers(secret, body, event_id="evt-1"))
    assert replay.status_code == 200
    assert replay.json()["status"] == "skipped_duplicate"
    assert len(dispatch_stub) == 1

    # 不同 event_id → 再派发
    ok2 = client.post(url, content=body,
                      headers=_signed_headers(secret, body, event_id="evt-2"))
    assert ok2.json()["status"] == "queued"
    assert len(dispatch_stub) == 2

    events = automations.list_events(record["id"])
    assert len(events) == 2  # evt-1 与 evt-2 各一条（重放不新增行）
    assert all(e["source"] == "webhook" for e in events)


def test_webhook_tenant_binding_and_missing_404(client, dispatch_stub):
    record, secret = _make_webhook()
    body = b"{}"
    headers = _signed_headers(secret, body, event_id="evt-ten")

    # 租户段不匹配 → 404（同"不存在"文案，不泄露存在性）
    wrong = client.post(f"/webhooks/automations/{TENANT_B}/{record['id']}",
                        content=body, headers=headers)
    assert wrong.status_code == 404
    missing = client.post(
        f"/webhooks/automations/{TENANT}/auto_{uuid.uuid4().hex}",
        content=body, headers=headers)
    assert missing.status_code == 404
    assert wrong.json()["detail"] == missing.json()["detail"]
    assert len(dispatch_stub) == 0

    # 停用 → 404（不确认存在性）
    record_off, secret_off = _make_webhook(name="hook-off", enabled=False)
    off = client.post(
        f"/webhooks/automations/{TENANT}/{record_off['id']}",
        content=body,
        headers=_signed_headers(secret_off, body, event_id="evt-off"))
    assert off.status_code == 404


def test_webhook_503_when_broker_down(client, monkeypatch):
    record, secret = _make_webhook()
    url = f"/webhooks/automations/{TENANT}/{record['id']}"
    body = b"{}"

    def _boom(*_a, **_kw):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "backend.tasks.automation_tasks.run_user_automation.apply_async",
        _boom)
    resp = client.post(url, content=body,
                       headers=_signed_headers(secret, body, event_id="evt-503"))
    assert resp.status_code == 503
    events = automations.list_events(record["id"])
    assert events[0]["status"] == "failed"


# ═══════════════════════════════════════════════════
# 用户 CRUD 与隔离
# ═══════════════════════════════════════════════════

def test_user_crud_and_isolation(client):
    created = client.post(
        "/automations", headers=USER_HEADERS,
        json={"name": "daily", "trigger_type": "cron",
              "cron_expr": "0 8 * * *",
              "payload": {"question": "今天的日报"}})
    assert created.status_code == 201
    body = created.json()
    auto_id = body["id"]

    # webhook 密钥字段永不出现在 cron 类型
    assert "webhook_secret" not in body

    # 列表 / 更新
    listed = client.get("/automations", headers=USER_HEADERS).json()
    assert listed["total"] == 1
    updated = client.put(f"/automations/{auto_id}", headers=USER_HEADERS,
                         json={"enabled": False})
    assert updated.json()["enabled"] is False

    # B 用户看不到（列表为空）也改不了/删不了（404，不泄露存在性）
    assert client.get("/automations", headers=USER_B_HEADERS).json()["total"] == 0
    assert client.put(f"/automations/{auto_id}", headers=USER_B_HEADERS,
                      json={"enabled": True}).status_code == 404
    assert client.delete(f"/automations/{auto_id}",
                         headers=USER_B_HEADERS).status_code == 404
    assert client.get(f"/automations/{auto_id}/events",
                      headers=USER_B_HEADERS).status_code == 404

    # 同租户同名冲突 → 409；B 租户同名不冲突
    dup = client.post("/automations", headers=USER_HEADERS,
                      json={"name": "daily", "trigger_type": "cron",
                            "cron_expr": "0 9 * * *",
                            "payload": {"question": "x"}})
    assert dup.status_code == 409
    dup_b = client.post("/automations", headers=USER_B_HEADERS,
                        json={"name": "daily", "trigger_type": "cron",
                              "cron_expr": "0 9 * * *",
                              "payload": {"question": "x"}})
    assert dup_b.status_code == 201

    # 自己删除成功
    assert client.delete(f"/automations/{auto_id}",
                         headers=USER_HEADERS).json()["deleted"] is True


def test_user_create_validation(client):
    # confirm_policy 非 auto → 400（v1 边界）
    r1 = client.post("/automations", headers=USER_HEADERS,
                     json={"name": "c1", "trigger_type": "cron",
                           "cron_expr": "0 8 * * *",
                           "payload": {"question": "x"},
                           "confirm_policy": "require_confirmation"})
    assert r1.status_code == 400
    # payload 多余键 / 空 question → 400
    r2 = client.post("/automations", headers=USER_HEADERS,
                     json={"name": "c2", "trigger_type": "cron",
                           "cron_expr": "0 8 * * *",
                           "payload": {"question": "x", "cmd": "rm"}})
    assert r2.status_code == 400
    r3 = client.post("/automations", headers=USER_HEADERS,
                     json={"name": "c3", "trigger_type": "cron",
                           "cron_expr": "0 8 * * *", "payload": {"question": " "}})
    assert r3.status_code == 400
    # cron 类型非法表达式 → 400
    r4 = client.post("/automations", headers=USER_HEADERS,
                     json={"name": "c4", "trigger_type": "cron",
                           "cron_expr": "99 * * * *",
                           "payload": {"question": "x"}})
    assert r4.status_code == 400
    # 未认证 → 401
    assert client.get("/automations").status_code == 401


def test_webhook_create_returns_secret_once(client):
    created = client.post(
        "/automations", headers=USER_HEADERS,
        json={"name": "hook-once", "trigger_type": "webhook",
              "payload": {"question": "查库存"}})
    assert created.status_code == 201
    body = created.json()
    # 明文密钥只在创建响应出现一次；列表/更新不再返回
    assert body["webhook_secret"].startswith("whsec_")
    assert body["webhook_url"].startswith(f"/webhooks/automations/{TENANT}/")
    listed = client.get("/automations", headers=USER_HEADERS).json()
    assert "webhook_secret" not in listed["automations"][0]
    assert "webhook_secret_cipher" not in listed["automations"][0]


# ═══════════════════════════════════════════════════
# run_user_automation（执行链 mock：成功 / 失败）
# ═══════════════════════════════════════════════════

def test_run_user_automation_success_and_failure(monkeypatch):
    from backend.tasks import automation_tasks

    record = _make_cron(name="run-ok")
    event_id = automations.record_event(record["id"], source="cron",
                                        external_event_id="")  # 空键不去重

    created = []

    def _fake_create(user_id, query, *, tenant_id="default", **_kw):
        created.append((user_id, query, tenant_id))
        return _FakeRecord("task-123")

    def _fake_enqueue(_record):
        return "celery-abc"

    monkeypatch.setattr("backend.services.task_service.create_task", _fake_create)
    monkeypatch.setattr("backend.tasks.task_manager.enqueue_task", _fake_enqueue)

    result = automation_tasks.run_user_automation(record["id"], event_id)
    assert result["status"] == "succeeded"
    assert result["task_id"] == "task-123"
    assert created == [(USER_A, "今天的经营日报", TENANT)]  # 身份/租户来自 automation 行
    events = automations.list_events(record["id"])
    assert events[0]["status"] == "succeeded"
    assert events[0]["payload"]["task_id"] == "task-123"
    row = automations.get_automation(record["id"])
    assert row["last_status"] == "succeeded"

    # 失败路径：enqueue 抛 → 事件 failed + automation last_status=failed
    def _boom(_record):
        raise RuntimeError("broker down")

    monkeypatch.setattr("backend.tasks.task_manager.enqueue_task", _boom)
    event_id2 = automations.record_event(record["id"], source="cron",
                                         external_event_id="")
    result2 = automation_tasks.run_user_automation(record["id"], event_id2)
    assert result2["status"] == "failed"
    events = automations.list_events(record["id"])
    assert events[0]["status"] == "failed"
    assert "broker down" in events[0]["detail"]


def test_run_user_automation_guard_paths(monkeypatch):
    from backend.tasks import automation_tasks

    # 停用 → failed(disabled)，不触达执行链
    def _fail_create(*_a, **_kw):
        raise AssertionError("不应触达执行链")

    monkeypatch.setattr("backend.services.task_service.create_task", _fail_create)
    disabled = _make_cron(name="guard-off", enabled=False)
    event_id = automations.record_event(disabled["id"], source="cron",
                                        external_event_id="")
    result = automation_tasks.run_user_automation(disabled["id"], event_id)
    assert result == {"status": "failed", "reason": "disabled"}

    # payload 损坏 → failed(payload)
    broken = _make_cron(name="guard-payload")
    _db_exec("UPDATE ai.user_automations SET payload = %s::jsonb WHERE id = %s",
             (json.dumps({"cmd": "not-allowed"}), broken["id"]))
    event_id2 = automations.record_event(broken["id"], source="cron",
                                         external_event_id="")
    result2 = automation_tasks.run_user_automation(broken["id"], event_id2)
    assert result2 == {"status": "failed", "reason": "payload"}

    # automation 已删除 → failed(missing)
    result3 = automation_tasks.run_user_automation("auto_missing", 999999)
    assert result3 == {"status": "failed", "reason": "missing"}


# ═══════════════════════════════════════════════════
# 管理端：总览与开关
# ═══════════════════════════════════════════════════

def test_admin_overview_and_disable(client):
    record = _make_cron(name="admin-target")
    # 制造一条失败事件（供 recent_failures）
    event_id = automations.record_event(record["id"], source="cron",
                                        external_event_id="")
    automations.mark_event(event_id, status="failed", detail="演练失败")

    resp = client.get("/admin/automations/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["by_trigger"]["cron"] >= 1
    assert any(f["automation_id"] == record["id"] for f in body["recent_failures"])
    # 清单不含密钥字段
    assert all("webhook_secret_cipher" not in a for a in body["automations"])

    # 停用开关
    off = client.patch(f"/admin/automations/{record['id']}",
                       headers=ADMIN_HEADERS, json={"enabled": False})
    assert off.json()["enabled"] is False
    row = automations.get_automation(record["id"])
    assert row["enabled"] is False
    # 参数校验
    assert client.patch(f"/admin/automations/{record['id']}",
                        headers=ADMIN_HEADERS,
                        json={"enabled": "yes"}).status_code == 400
    assert client.patch(f"/admin/automations/auto_{uuid.uuid4().hex}",
                        headers=ADMIN_HEADERS,
                        json={"enabled": True}).status_code == 404
    # 非管理员 → 403
    assert client.get("/admin/automations/overview",
                      headers=EDITOR_HEADERS).status_code == 403
