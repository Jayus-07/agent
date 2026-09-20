"""用户预算摘要与管理员预算治理 API。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    resolve_operator_role,
    run_governance_mutation,
)
from backend.app.api.identity import require_identity
from backend.infra.llm.quota import PostgresQuotaStore, QuotaConfigurationError


router = APIRouter(tags=["预算"])


class BudgetPolicyUpdate(BaseModel):
    daily_limit_usd: str = Field(..., min_length=1)
    monthly_limit_usd: str = Field(..., min_length=1)
    enforcement: Literal["hard", "soft", "audit"]
    audit_exempt: bool = False
    reason: str = Field(..., min_length=1, max_length=1000)
    expected_updated_at: datetime | None = None


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value.quantize(Decimal('0.000001')):.6f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _decimal(value: str, field_name: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, f"{field_name} 必须是六位以内的小数字符串") from exc
    if number <= 0 or number.as_tuple().exponent < -6:
        raise HTTPException(422, f"{field_name} 必须大于 0 且最多 6 位小数")
    return number


def _store() -> PostgresQuotaStore:
    return PostgresQuotaStore()


@router.get("/budgets/me")
async def get_my_budget(request: Request) -> dict[str, Any]:
    identity = require_identity(request)
    if not identity.tenant_id:
        raise HTTPException(401, "预算查询需要可信租户身份")
    try:
        return _json_value(_store().get_budget_status(
            user_id=identity.user_id,
            tenant_id=identity.tenant_id,
        ))
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算状态暂不可用") from exc


@router.get("/admin/budgets/summary")
async def get_budget_summary(
    operator: OperatorIdentity = Depends(resolve_operator_role),
) -> dict[str, Any]:
    if operator.role not in {"viewer", "editor", "admin"}:
        raise HTTPException(403, "无权查看预算汇总")
    try:
        return _json_value(_store().summary())
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算汇总暂不可用") from exc


@router.get("/admin/budgets/subjects")
async def get_budget_subjects(
    scope: str = Query("", pattern="^(|user|tenant)$"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    del operator
    try:
        items = _store().list_subjects()
        if scope:
            items = [item for item in items if item["scope"] == scope]
        return {"items": _json_value(items)}
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算主体暂不可用") from exc


@router.get("/admin/budgets/policies")
async def get_budget_policies(
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    del operator
    try:
        return {"items": _json_value(_store().list_policies())}
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算策略暂不可用") from exc


@router.put("/admin/budgets/policies/{scope_type}/{scope_id}")
async def update_budget_policy(
    scope_type: str,
    scope_id: str,
    body: BudgetPolicyUpdate,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    if scope_type not in {"user", "tenant", "tenant_default", "platform"}:
        raise HTTPException(422, "预算作用域非法")
    daily = _decimal(body.daily_limit_usd, "daily_limit_usd")
    monthly = _decimal(body.monthly_limit_usd, "monthly_limit_usd")
    def persist() -> dict[str, Any]:
        policy = _store().upsert_policy(
                scope_type=scope_type,
                scope_id=scope_id,
                daily_limit_usd=daily,
                monthly_limit_usd=monthly,
                enforcement=body.enforcement,
                audit_exempt=body.audit_exempt,
                updated_by=operator.actor,
                reason=body.reason,
                expected_updated_at=body.expected_updated_at,
            )
        return {"policy": _json_value(policy)}

    try:
        return run_governance_mutation(
            request,
            operator,
            "budget.policy.update",
            {
                "scope_type": scope_type,
                "scope_id": scope_id,
                **body.model_dump(mode="json"),
            },
            persist,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算策略写入暂不可用") from exc


@router.get("/admin/budgets/events")
async def get_budget_events(
    limit: int = Query(200, ge=1, le=1000),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    del operator
    try:
        return {"items": _json_value(_store().list_events(limit))}
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算事件暂不可用") from exc


@router.get("/admin/budgets/audit")
async def get_budget_policy_audit(
    limit: int = Query(200, ge=1, le=1000),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    del operator
    try:
        return {"items": _json_value(_store().list_policy_audit(limit))}
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算策略审计暂不可用") from exc
