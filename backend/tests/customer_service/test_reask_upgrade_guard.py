# -*- coding: utf-8 -*-
"""test_reask_upgrade_guard.py — 补槽升级/reask 守卫排除自身回归（2026-10-05）

缺陷：升级分支 `_raise_on_active_conflict(exclude_confirmation_id="")` 未排除
自身 confirmation_id——reask/补槽升级时 find_by_identity 命中本行自己
（identity 含升级后 target_id，本行 state 仍 pending），守卫把「更新自己」
误判为重复提交，一切追问/补槽保存失败（I1/I5/II-P4-2 端到端复现）。

本测试真 PG（跟随 test_p3_concurrency_pg 模式）锁三个语义：
  1. reask 更新自身不再误报 Active；
  2. need_info 补槽升级（target_id 从空到有）成功出 proposal；
  3. 升级撞**他人**活跃操作仍被正确拦截（守卫语义不放宽）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from backend.config.database import MEMORY_DB_CONFIG

pytestmark = pytest.mark.asyncio


def _require_pg() -> None:
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"customer_service PG 不可达: {exc}")


def _pending(target: str | None) -> dict:
    """构造 pending_action；target=None 模拟 need_info 占位行。"""
    return {
        "action_id": str(uuid.uuid4()),
        "action_type": "refund_request",
        "target_type": "order",
        "target_id": target or "",
        "proposal_text": (f"退款申请：订单 {target}" if target
                          else "请提供需要办理退款的订单号"),
        "risk_level": "medium",
        "status": "pending_confirmation",
        "expires_at": (datetime.now(timezone.utc)
                       + timedelta(minutes=30)).isoformat(),
    }


async def test_reask_update_self_not_blocked() -> None:
    """reask 更新自身：守卫排除自身后放行（缺陷回归主断言）。"""
    _require_pg()
    from backend.customer_service.confirmation_store import (
        get_confirmation_store,
    )
    store = get_confirmation_store()
    user = f"rg-{uuid.uuid4().hex[:8]}"
    conv = f"rg-conv-{uuid.uuid4().hex[:8]}"
    pending = _pending(f"ORD-RG-{uuid.uuid4().hex[:6]}")
    store.save(user, conv, pending, tenant_id="default")

    from backend.customer_service.confirmation_flow import process_confirmation
    outcome = process_confirmation(pending, "我今天不太方便", user, conv,
                                   tenant_id="default")
    # 修复前：save 撞活跃约束被误翻 Active；修复后 reask 正常（retry 升级）
    assert outcome.kind in ("reask", "ambiguous"), (
        f"reask 被误判: kind={outcome.kind} answer={outcome.answer[:80]}")
    fresh = store.load(user, conv)
    assert fresh is not None, "reask 后 pending 行丢失"


async def test_slot_upgrade_produces_proposal() -> None:
    """need_info 补槽升级：target 从空到有，save 成功出 proposal。"""
    _require_pg()
    from backend.customer_service.confirmation_store import (
        get_confirmation_store,
    )
    store = get_confirmation_store()
    user = f"rg-{uuid.uuid4().hex[:8]}"
    conv = f"rg-conv-{uuid.uuid4().hex[:8]}"
    placeholder = _pending(None)          # need_info 占位（target 空，不入守卫）
    store.save(user, conv, placeholder, tenant_id="default")

    upgraded = _pending(f"ORD-RG-{uuid.uuid4().hex[:6]}")
    upgraded["action_id"] = placeholder["action_id"]  # 同一 confirmation 升级
    # 修复前：升级 save 撞 051 索引/守卫 → BusinessOperationAlreadyActive
    store.save(user, conv, upgraded, tenant_id="default")
    fresh = store.load(user, conv)
    assert fresh is not None and fresh.get("target_id") == upgraded["target_id"], (
        f"补槽升级失败: {str(fresh)[:120]}")


async def test_upgrade_still_blocks_other_active() -> None:
    """升级撞他人活跃操作仍拦截（守卫语义不放宽）。"""
    _require_pg()
    from backend.customer_service.business_guard import (
        BusinessOperationAlreadyActive,
    )
    from backend.customer_service.confirmation_store import (
        get_confirmation_store,
    )
    store = get_confirmation_store()
    target = f"ORD-RG-{uuid.uuid4().hex[:6]}"
    user_a = f"rg-{uuid.uuid4().hex[:8]}"
    user_b = f"rg-{uuid.uuid4().hex[:8]}"
    conv_a = f"rg-conv-a-{uuid.uuid4().hex[:6]}"
    conv_b = f"rg-conv-b-{uuid.uuid4().hex[:6]}"
    # A 先建活跃 proposal（同 tenant=default 同 target）
    pa = _pending(target)
    store.save(user_a, conv_a, pa, tenant_id="default")
    # B 升级出同 target proposal → 应被守卫拦截
    pb = _pending(target)
    store.save(user_b, conv_b, _pending(None), tenant_id="default")
    pb["action_id"] = store.load(user_b, conv_b)["action_id"]
    with pytest.raises(BusinessOperationAlreadyActive):
        store.save(user_b, conv_b, pb, tenant_id="default")
