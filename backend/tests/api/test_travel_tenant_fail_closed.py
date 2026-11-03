"""travel 全域租户 fail-closed 回归（audit P3 补遗：其余 17 处回退收口）。

口径对齐 cs_admin._resolve_tenant_scope / plan_revisions._tenant：
- header/strict 源只认 identity.tenant_id（网关注入）；
- legacy body 源读 X-Tenant-Id 信任头；
- 皆空 → 403，绝不静默回落 'default'。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[2]
TRAVEL = BACKEND / "app/api/routes/travel.py"


class _Ident:
    def __init__(self, source: str = "header", tenant_id: str = "acme"):
        self.source = source
        self.tenant_id = tenant_id
        self.user_id = "u1"


class _Req:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


# ═══════════════ _travel_tenant 助手 ═══════════════

def test_travel_tenant_header_empty_fails_closed():
    from backend.app.api.routes.travel import _travel_tenant

    with pytest.raises(HTTPException) as ei:
        _travel_tenant(_Req(), _Ident("header", ""))
    assert ei.value.status_code == 403


def test_travel_tenant_header_valid_passthrough():
    from backend.app.api.routes.travel import _travel_tenant

    assert _travel_tenant(_Req(), _Ident("header", "acme")) == "acme"


def test_travel_tenant_body_uses_trusted_header():
    from backend.app.api.routes.travel import _travel_tenant

    assert _travel_tenant(_Req({"X-Tenant-Id": "acme"}), _Ident("body", "")) == "acme"


def test_travel_tenant_body_without_header_fails_closed():
    from backend.app.api.routes.travel import _travel_tenant

    with pytest.raises(HTTPException) as ei:
        _travel_tenant(_Req({}), _Ident("body", ""))
    assert ei.value.status_code == 403


# ═══════════════ memory 键助手（共享 memory 表的租户隔离键） ═══════════════

def test_travel_message_session_id_empty_tenant_rejected():
    from backend.app.api.routes.travel import _travel_message_session_id

    with pytest.raises(HTTPException) as ei:
        _travel_message_session_id("c1", "u1", "  ")
    assert ei.value.status_code == 403


def test_travel_message_owner_id_empty_tenant_rejected():
    from backend.app.api.routes.travel import _travel_message_owner_id

    with pytest.raises(HTTPException) as ei:
        _travel_message_owner_id("u1", "")
    assert ei.value.status_code == 403


# ═══════════════ 路由级功能断言（活跃面） ═══════════════

def test_messages_route_rejects_empty_tenant(monkeypatch):
    from backend.app.api.routes import travel as travel_mod

    monkeypatch.setattr(travel_mod, "require_identity", lambda req: _Ident("header", ""))
    with pytest.raises(HTTPException) as ei:
        travel_mod.travel_conversation_messages("c1", _Req())
    assert ei.value.status_code == 403


def test_messages_route_accepts_valid_tenant(monkeypatch):
    """合法租户下路由继续走 memory 链路（此处 memory 不可用 → 503 语义，
    证明已越过租户闸而不是被 403 挡住）。"""
    from backend.app.api.routes import travel as travel_mod

    monkeypatch.setattr(travel_mod, "require_identity", lambda req: _Ident("header", "acme"))
    monkeypatch.setattr(
        travel_mod, "_run_travel_memory", lambda op: {"error": "内部错误"})
    with pytest.raises(HTTPException) as ei:
        travel_mod.travel_conversation_messages("c1", _Req())
    assert ei.value.status_code == 503


# ═══════════════ 源码级防回退 ═══════════════

def test_travel_source_has_no_tenant_default_fallback():
    src = TRAVEL.read_text(encoding="utf-8")
    assert 'or "default"' not in src, "travel.py 不得再出现租户回落 default"
    assert "_travel_tenant(" in src


def test_travel_stream_entry_guards_before_worker():
    src = TRAVEL.read_text(encoding="utf-8")
    impl_pos = src.find("async def _travel_plan_stream_impl(")
    guard_pos = src.find("_travel_tenant(request, identity)", impl_pos)
    worker_pos = src.find("def worker(control) -> None:", impl_pos)
    assert -1 not in (impl_pos, guard_pos, worker_pos) and guard_pos < worker_pos, (
        "v2 流必须在 worker 启动前完成租户 fail-closed")
