"""关键词规则管理 API。

读接口保留旧响应形状；所有写接口只创建规则草稿，发布和回滚单独走管理员
审批流程，避免一次误操作直接改变线上路由。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2 import errors as psycopg2_errors

from backend.app.api.deps import OperatorIdentity, require_admin_user

router = APIRouter(prefix="/rag/keywords", tags=["关键词管理"])


def _service():
    from backend.rag.preprocessing.metadata_rule_service import get_metadata_rule_service

    return get_metadata_rule_service()


def _draft_response(snapshot) -> dict[str, Any]:
    return {
        "status": snapshot.status,
        "version": snapshot.version,
        "rules_hash": snapshot.rules_hash,
        "taxonomy_version": snapshot.taxonomy_version,
        "review_required": True,
        "reason": snapshot.reason,
    }


def _require_reason(body: dict[str, Any]) -> str:
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    return reason


def _legacy_items(store) -> list[dict[str, Any]]:
    return [dict(item) for item in store.list_all()]


def _active_entries_for_mutation(service, store) -> list[dict[str, Any]]:
    try:
        return [dict(item) for item in service.get_active_snapshot().entries]
    except psycopg2_errors.UndefinedTable:
        # 迁移窗口内仍可创建草稿：旧表只作为一次性输入，不作为线上活动读路径。
        return _legacy_items(store)
    except LookupError:
        # 治理表存在但暂时没有活动版本：从空集创建草稿，禁止回读未版本化表。
        return []


def _merge_entry(entries: list[dict[str, Any]], new_entry: dict[str, Any]) -> list[dict[str, Any]]:
    target = str(new_entry.get("keyword") or "").strip().casefold()
    result = [
        item for item in entries
        if str(item.get("keyword") or "").strip().casefold() != target
    ]
    result.append(new_entry)
    return result


@router.get("")
async def list_keywords(doc_type: str = "", category: str = "", search: str = "", enabled: str = ""):
    service = _service()
    store = service.store
    try:
        items = await asyncio.to_thread(service.list_active_entries)
    except psycopg2_errors.UndefinedTable:
        items = await asyncio.to_thread(_legacy_items, store)
    except LookupError:
        items = []
    if doc_type:
        items = [item for item in items if item.get("doc_type") == doc_type]
    if category:
        items = [item for item in items if item.get("category") == category]
    if enabled != "":
        items = [item for item in items if str(item.get("enabled")) == enabled]
    if search:
        needle = search.casefold()
        items = [item for item in items if needle in str(item.get("keyword", "")).casefold()]
    return {"items": items}


@router.get("/doc-types")
async def list_doc_types():
    service = _service()
    try:
        entries = await asyncio.to_thread(lambda: service.get_active_snapshot().entries)
        return {"doc_types": sorted({str(item.get("doc_type", "general")) for item in entries})}
    except psycopg2_errors.UndefinedTable:
        return {"doc_types": await asyncio.to_thread(service.store.list_doc_types)}
    except LookupError:
        return {"doc_types": []}


@router.get("/categories")
async def list_keyword_categories():
    service = _service()
    try:
        entries = await asyncio.to_thread(lambda: service.get_active_snapshot().entries)
        return {"categories": sorted({str(item.get("category", "")) for item in entries if item.get("category")})}
    except psycopg2_errors.UndefinedTable:
        return {"categories": await asyncio.to_thread(service.store.list_categories)}
    except LookupError:
        return {"categories": []}


@router.get("/versions/active")
async def get_active_keyword_version():
    """管理面读取活动快照状态；不改变旧 GET 接口的响应形状。"""
    service = _service()
    try:
        snapshot = await asyncio.to_thread(service.get_active_snapshot)
    except psycopg2_errors.UndefinedTable:
        return {
            "status": "legacy",
            "version": None,
            "rules_hash": "",
            "taxonomy_version": "",
            "review_required": True,
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": snapshot.status,
        "version": snapshot.version,
        "rules_hash": snapshot.rules_hash,
        "taxonomy_version": snapshot.taxonomy_version,
        "effective_at": snapshot.effective_at,
        "review_required": False,
    }


@router.post("")
async def create_keyword_draft(
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    reason = _require_reason(body)
    service = _service()
    store = service.store
    entries = _active_entries_for_mutation(service, store)
    entries = _merge_entry(entries, {
        "keyword": str(body.get("keyword") or "").strip(),
        "doc_type": body.get("doc_type", "general"),
        "category": body.get("category", ""),
        "weight": body.get("weight", 1),
        "enabled": body.get("enabled", 1),
    })
    try:
        snapshot = await asyncio.to_thread(
            service.create_rule_draft, entries, operator.actor, reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _draft_response(snapshot)


@router.post("/batch")
async def create_keyword_batch_draft(
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    reason = _require_reason(body)
    items = body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="items must be a list")
    remove_keywords = body.get("remove_keywords") or []
    if not isinstance(remove_keywords, list) or any(
        not isinstance(item, str) for item in remove_keywords
    ):
        raise HTTPException(status_code=422, detail="remove_keywords must be a list of strings")
    service = _service()
    store = service.store
    entries = _active_entries_for_mutation(service, store)
    remove_set = {item.strip().casefold() for item in remove_keywords}
    entries = [
        entry for entry in entries
        if str(entry.get("keyword") or "").strip().casefold() not in remove_set
    ]
    for item in items:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="each item must be an object")
        entries = _merge_entry(entries, item)
    try:
        snapshot = await asyncio.to_thread(
            service.create_rule_draft, entries, operator.actor, reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _draft_response(snapshot)


@router.get("/versions")
async def list_keyword_versions(limit: int = 20):
    """返回最近规则版本，供管理面展示发布/回滚状态。"""
    service = _service()
    try:
        snapshots = await asyncio.to_thread(service.list_rule_snapshots, limit)
    except psycopg2_errors.UndefinedTable:
        snapshots = []
    return {
        "items": [
            {
                "status": snapshot.status,
                "version": snapshot.version,
                "rules_hash": snapshot.rules_hash,
                "taxonomy_version": snapshot.taxonomy_version,
                "actor": snapshot.actor,
                "reason": snapshot.reason,
                "approval_id": snapshot.approval_id,
                "approved_by": snapshot.approved_by,
                "effective_at": snapshot.effective_at,
                "created_at": snapshot.created_at,
            }
            for snapshot in snapshots
        ]
    }


@router.post("/versions/{version}/publish")
async def publish_keyword_version(
    version: int,
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    _require_reason(body)
    approval_id = str(body.get("approval_id") or "").strip()
    if not approval_id:
        raise HTTPException(status_code=422, detail="approval_id is required")
    try:
        snapshot = await asyncio.to_thread(
            _service().publish_rule_snapshot, version, approval_id, operator.actor
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_draft_response(snapshot), "review_required": False}


@router.post("/versions/{version}/rollback")
async def rollback_keyword_version(
    version: int,
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    reason = _require_reason(body)
    try:
        snapshot = await asyncio.to_thread(
            _service().rollback_rule_snapshot, version, operator.actor, reason
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**_draft_response(snapshot), "review_required": False}


@router.delete("/{keyword}")
async def delete_keyword(
    keyword: str,
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    reason = _require_reason(body)
    service = _service()
    entries = [
        item for item in _active_entries_for_mutation(service, service.store)
        if str(item.get("keyword") or "").casefold() != keyword.casefold()
    ]
    try:
        snapshot = await asyncio.to_thread(
            service.create_rule_draft, entries, operator.actor, reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _draft_response(snapshot)


@router.put("/{keyword}/toggle")
async def toggle_keyword(
    keyword: str,
    req: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
):
    body = await req.json()
    reason = _require_reason(body)
    service = _service()
    entries = _active_entries_for_mutation(service, service.store)
    found = False
    for item in entries:
        if str(item.get("keyword") or "").casefold() == keyword.casefold():
            item["enabled"] = int(body.get("enabled", 1))
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="keyword not found")
    try:
        snapshot = await asyncio.to_thread(
            service.create_rule_draft, entries, operator.actor, reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _draft_response(snapshot)
