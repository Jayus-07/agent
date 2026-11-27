"""admin_mcp_subroutes/semantic_routes.py — MCP 工具 AI 语义契约候选生成与审核路由。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import Json as PgJson

from backend.app.api.deps import require_admin_user, run_governance_mutation
from backend.app.api.routes.admin_mcp_subroutes.common import (
    _actor_of,
    _audit,
    _bad_request,
    _conflict,
    _current_input_schema,
    _execute,
    _load_tool,
    _normalize_semantic_draft,
    _not_found,
    _now_iso,
    _refresh_runtime,
    _render_semantic_prompt,
    _tenant_of,
    _validate_semantic_contract,
)
from backend.app.api.routes.admin_mcp_subroutes.dynamic import (
    current_audit,
    current_current_input_schema,
    current_execute,
    current_load_tool,
    current_run_governance_mutation,
)
from backend.app.api.routes.admin_mcp_subroutes.models import (
    SemanticDraftRequest,
    _MutationConflict,
)
from backend.shared.admin_llm import current_llm_model, invoke_admin_llm

from backend.app.api.routes.admin_mcp_subroutes.routes import router


@router.post("/tools/{tool_id}/semantic/suggest")
async def suggest_semantic(request: Request, tool_id: int):
    """AI 生成语义契约候选：仅读工具元数据（不含凭据与 URL），产物只写
    独立列 semantic_draft（候选，不生效）；管理员「审核通过并入」后才更新
    正式 description。LLM 不可用/调用失败 → 503；解析失败重试 1 次仍失败
    → 502，绝不伪造草稿。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    current_input_schema_fn = current_current_input_schema(_current_input_schema)

    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row["status"] not in ("approved", "pending"):
        return _conflict(
            f"工具当前状态为 {row['status']}，仅 approved/pending 工具可生成语义建议")

    schema = await asyncio.to_thread(current_input_schema_fn, tool_id)
    try:
        prompt_text = _render_semantic_prompt(row, schema)
    except Exception as exc:  # noqa: BLE001 — prompt 渲染失败显式暴露
        return JSONResponse(
            status_code=503,
            content={"code": "PROMPT_RENDER_FAILED",
                     "message": f"语义建议 prompt 渲染失败: {exc}"})

    draft: dict[str, Any] | None = None
    last_reason = ""
    for _attempt in range(2):  # 解析失败重试 1 次
        try:
            content = await invoke_admin_llm(prompt_text)
        except Exception as exc:  # noqa: BLE001 — LLM 失败不伪造结果
            return JSONResponse(
                status_code=503,
                content={"code": "LLM_UNAVAILABLE",
                         "message": f"LLM 不可用或调用失败: {str(exc)[:300]}"})
        draft = _normalize_semantic_draft(content)
        if draft is not None:
            break
        last_reason = content.strip()[:200]

    if draft is None:
        return JSONResponse(
            status_code=502,
            content={"code": "SEMANTIC_PARSE_FAILED",
                     "message": "语义解析失败（重试后仍无法得到合法 JSON）"
                                f"：{last_reason}"})

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        record = {**draft, "status": "suggested",
                  "generated_at": _now_iso(), "generated_by": actor}
        execute_fn(
            "UPDATE ai.mcp_tools SET semantic_draft = %(draft)s, "
            "updated_at = now() WHERE id = %(tid)s",
            {"draft": PgJson(record), "tid": tool_id})
        audit_fn("mcp_tool_semantic_suggest", {
            "tool_id": tool_id, "capability": f"mcp.{row['capability_slug']}",
            "summary_chars": len(record["summary"]),
            "scenarios": len(record["scenarios"]),
            "exclusions": len(record["exclusions"]),
            # 模型名留痕（与 tool_failure_analysis 同口径；软失败留空）
            "model": current_llm_model(),
        }, user_id=actor, tenant_id=tenant)
        return record

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator,
        "mcp_tool_semantic_suggest", {"tool_id": tool_id}, _write)
    return {"tool_id": tool_id, "draft": result}


@router.put("/tools/{tool_id}/semantic/draft")
async def save_semantic_draft(request: Request, tool_id: int,
                              body: SemanticDraftRequest):
    """保存管理员编辑后的语义契约草稿（status=edited）：候选行为，
    不影响运行时；审核通过并入请走 /semantic/approve。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    reason = _validate_semantic_contract(
        body.summary, body.scenarios, body.exclusions, body.capability_hint)
    if reason:
        return _bad_request(reason, code="SEMANTIC_CONTRACT_INVALID")

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        existing = row.get("semantic_draft") or {}
        record = {
            "summary": body.summary.strip(),
            "scenarios": [s.strip() for s in body.scenarios if s.strip()],
            "exclusions": [s.strip() for s in body.exclusions if s.strip()],
            "capability_hint": (body.capability_hint or "").strip()
            or None,
            "status": "edited",
            # 保留首次生成留痕（AI 建议 → 人工编辑的审计链）
            "generated_at": existing.get("generated_at"),
            "generated_by": existing.get("generated_by"),
            "edited_at": _now_iso(),
            "edited_by": actor,
        }
        execute_fn(
            "UPDATE ai.mcp_tools SET semantic_draft = %(draft)s, "
            "updated_at = now() WHERE id = %(tid)s",
            {"draft": PgJson(record), "tid": tool_id})
        audit_fn("mcp_tool_semantic_draft_save", {
            "tool_id": tool_id, "capability": f"mcp.{row['capability_slug']}",
            "status": "edited",
        }, user_id=actor, tenant_id=tenant)
        return record

    return await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator,
        "mcp_tool_semantic_draft_save", {"tool_id": tool_id}, _write)


@router.post("/tools/{tool_id}/semantic/approve")
async def approve_semantic(request: Request, tool_id: int):
    """审核通过并入：草稿 summary 并入正式 description（平台可信描述，
    经 prompt 供 Planner/路由使用），完整契约存 semantic_approved 留痕，
    草稿置 applied。幂等：无草稿或已并入 → 409。

    边界：只更新平台描述列，不动 content_hash/approved_hash——它们绑定
    上游发现内容（漂移检测的事实源），平台描述变化不构成上游漂移。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    load_tool_fn = current_load_tool(_load_tool)
    row = await asyncio.to_thread(load_tool_fn, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row["status"] == "retired":
        return _conflict("上游已消失（retired）的工具不能并入语义契约")
    draft = row.get("semantic_draft") or {}
    summary = str(draft.get("summary") or "").strip()
    if not summary:
        return _conflict("无待审语义草稿，请先生成或编辑草稿")
    if draft.get("status") == "applied":
        return _conflict("草稿已并入正式描述，无待审草稿（可重新生成）")
    new_description = summary[:spec_lib.MAX_DESCRIPTION_CHARS]

    def _write() -> dict[str, Any]:
        execute_fn = current_execute(_execute)
        audit_fn = current_audit(_audit)

        approved = {
            "summary": summary,
            "scenarios": draft.get("scenarios") or [],
            "exclusions": draft.get("exclusions") or [],
            "capability_hint": draft.get("capability_hint"),
            "approved_at": _now_iso(),
            "approved_by": actor,
            "description_after": new_description,
        }
        updated = execute_fn(
            """
            UPDATE ai.mcp_tools
            SET description = %(desc)s,
                semantic_approved = %(approved)s,
                semantic_draft = semantic_draft || %(applied)s,
                updated_at = now()
            WHERE id = %(tid)s
            """,
            {"desc": new_description, "approved": PgJson(approved),
             "applied": PgJson({"status": "applied"}), "tid": tool_id})
        if updated == 0:
            raise _MutationConflict("工具不存在或已被删除")
        audit_fn("mcp_tool_semantic_approve", {
            "tool_id": tool_id, "capability": f"mcp.{row['capability_slug']}",
            "description_chars": len(new_description),
            "scenarios": len(approved["scenarios"]),
            "exclusions": len(approved["exclusions"]),
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_id, "approved": True,
                "description": new_description,
                "semantic_approved": approved}

    result = await asyncio.to_thread(
        current_run_governance_mutation(run_governance_mutation), request, operator,
        "mcp_tool_semantic_approve", {"tool_id": tool_id}, _write)
    # 描述已变：刷新运行时快照使新描述即时进入 Planner/路由 prompt
    await _refresh_runtime()
    return result
