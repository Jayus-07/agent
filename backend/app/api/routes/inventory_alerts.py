"""app/api/routes/inventory_alerts.py — 库存预警 API 路由

端点：
- GET    /api/inventory/thresholds              列出所有阈值规则
- POST   /api/inventory/thresholds              新增阈值规则
- DELETE /api/inventory/thresholds/{id}         删除阈值规则
- GET    /api/inventory/cases                   列出所有 alert case
- GET    /api/inventory/cases/{case_id}         单个 case 详情（含 events）
- POST   /api/inventory/cases/{case_id}/resolve  人工 resolve（不发邮件）
- POST   /api/inventory/cases/{case_id}/reopen   手动 reopen（实际由状态机自动处理）
- GET    /api/inventory/policies                列出所有通知策略
- POST   /api/inventory/policies                新增通知策略
- DELETE /api/inventory/policies/{id}           删除通知策略
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from backend.orchestration.inventory import (
    InventoryStore,
    get_inventory_store,
)


router = APIRouter(prefix="/inventory", tags=["库存预警"])


def get_store() -> InventoryStore:
    """FastAPI 依赖：获取 InventoryStore 单例"""
    return get_inventory_store()


# ─────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────

@router.get("/thresholds")
async def list_thresholds(store: InventoryStore = Depends(get_store)) -> dict[str, Any]:
    """列出所有阈值规则"""
    return {"thresholds": store.list_thresholds(enabled_only=False)}


@router.post("/thresholds")
async def create_threshold(
    body: dict = Body(...),
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """新增阈值规则"""
    required = ["rule_type", "min_qty"]
    for k in required:
        if k not in body:
            raise HTTPException(status_code=400, detail=f"missing {k}")
    rule_id = store.save_threshold(body)
    return {"id": rule_id, "rule": body}


@router.delete("/thresholds/{rule_id}")
async def delete_threshold(
    rule_id: int,
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """删除阈值规则（软删：enabled=false）"""
    existing = next(
        (r for r in store.list_thresholds(enabled_only=False) if r["id"] == rule_id),
        None,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="threshold not found")
    existing["enabled"] = False
    store.save_threshold(existing)
    return {"deleted": True, "id": rule_id}


# ─────────────────────────────────────────────────────────────
# Cases
# ─────────────────────────────────────────────────────────────

@router.get("/cases")
async def list_cases(
    status: str = "",
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """列出所有 alert case（可按 status 过滤）"""
    if status == "open":
        cases = store.list_open_cases()
    else:
        # 全列：直接查所有 status
        from backend.orchestration.inventory.store import InventoryStore as _S
        with store._lock, store._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_alert_cases ORDER BY id DESC"
            ).fetchall()
        cases = [dict(r) for r in rows]
    return {"cases": cases, "total": len(cases)}


@router.get("/cases/{case_id}")
async def get_case(
    case_id: int,
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """单个 case 详情（含 events 事件链）"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    events = store.list_events_by_case(case_id)
    return {
        "case": case,
        "events": events,
    }


@router.post("/cases/{case_id}/resolve")
async def manual_resolve_case(
    case_id: int,
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """人工标记 case 为已解决（不发邮件）"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    if case.get("status") != "open":
        raise HTTPException(status_code=400, detail="case is not open")

    store.update_case_status(
        case_id,
        "resolved",
        resolution_type="MANUAL_RESOLVED",
    )

    # 记录事件
    store.insert_event({
        "case_id": case_id,
        "event_type": "resolved",
        "from_state": case.get("current_state"),
        "to_state": case.get("current_state"),
        "qty": None,
        "stock_days": None,
        "reason": ["人工标记为已解决，不发邮件"],
        "notified": False,
    })

    return {"resolved": True, "case_id": case_id, "resolution_type": "MANUAL_RESOLVED"}


# ─────────────────────────────────────────────────────────────
# Policies
# ─────────────────────────────────────────────────────────────

@router.get("/policies")
async def list_policies(store: InventoryStore = Depends(get_store)) -> dict[str, Any]:
    """列出所有通知策略"""
    return {"policies": store.list_policies(enabled_only=False)}


@router.post("/policies")
async def create_policy(
    body: dict = Body(...),
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """新增通知策略"""
    if "policy_name" not in body or "notify_email" not in body:
        raise HTTPException(
            status_code=400,
            detail="missing policy_name or notify_email",
        )
    policy_id = store.save_policy(body)
    return {"id": policy_id, "policy": body}


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: int,
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """删除通知策略（软删：enabled=false）"""
    all_p = store.list_policies(enabled_only=False)
    existing = next((p for p in all_p if p["id"] == policy_id), None)
    if not existing:
        raise HTTPException(status_code=404, detail="policy not found")
    existing["enabled"] = 0
    store.save_policy(existing)
    return {"deleted": True, "id": policy_id}