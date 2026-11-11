"""用户预算摘要与管理员预算治理 API。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
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
    """策略金额单位 = 记账本位币 CNY（2026-10-01 起）。

    domain（任务 9，2026-11-11）：''=总额度策略；main/travel/customer_service
    = 域额度策略。枚举与迁移 147 的 CHECK 同源。
    """

    daily_limit_cny: str = Field(..., min_length=1)
    monthly_limit_cny: str = Field(..., min_length=1)
    enforcement: Literal["hard", "soft", "audit"]
    audit_exempt: bool = False
    reason: str = Field(..., min_length=1, max_length=1000)
    expected_updated_at: datetime | None = None
    domain: str = ""


_RESOLUTION_ACTIONS = ("settled_actual", "released", "written_off", "archived")


class BudgetResolveRequest(BaseModel):
    """单笔待对账核销（第二批闭环）。actual_cny 仅 settled_actual 必填。"""

    reservation_id: str = Field(..., min_length=1)
    action: Literal["settled_actual", "released", "written_off", "archived"]
    reason: str = Field(..., min_length=1, max_length=1000)
    actual_cny: str | None = None


class BudgetResolveBatchRequest(BaseModel):
    """批量待对账核销：逐笔幂等，单笔失败不影响其余。"""

    reservation_ids: list[str] = Field(..., min_length=1, max_length=500)
    action: Literal["settled_actual", "released", "written_off", "archived"]
    reason: str = Field(..., min_length=1, max_length=1000)
    actual_cny: str | None = None


def _resolve_amount(value: str | None, action: str) -> Decimal | None:
    """核销金额解析：settled_actual 必填且 ≥ 0（0 = 实际零账单）。"""
    if value is None or str(value).strip() == "":
        if action == "settled_actual":
            raise HTTPException(422, "settled_actual 必须提供实际账单金额")
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(422, "actual_cny 必须是六位以内的小数字符串") from exc
    if number < 0 or number.as_tuple().exponent < -6:
        raise HTTPException(422, "actual_cny 必须大于等于 0 且最多 6 位小数")
    return number


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
    if operator.role not in {"viewer", "editor", "admin", "super_admin"}:
        raise HTTPException(403, "无权查看预算汇总")
    try:
        return _json_value(_store().summary())
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "预算汇总暂不可用") from exc


@router.get("/admin/budgets/subjects")
async def get_budget_subjects(
    scope: str = Query("", pattern="^(|user|tenant)$"),
    include_test: bool = Query(False, description="包含测试/评测主体（默认排除）"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    del operator
    try:
        items = _store().list_subjects(include_test=include_test)
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
    # 域枚举与迁移 147 CHECK 同源（任务 9）：'' = 总额度策略
    _domain_gates = {"", "main", "travel", "customer_service"}
    if body.domain not in _domain_gates:
        raise HTTPException(
            422,
            f"预算域非法: {body.domain!r}（允许: 总额度空串 / main / "
            "travel / customer_service）",
        )
    daily = _decimal(body.daily_limit_cny, "daily_limit_cny")
    monthly = _decimal(body.monthly_limit_cny, "monthly_limit_cny")
    def persist() -> dict[str, Any]:
        policy = _store().upsert_policy(
                scope_type=scope_type,
                scope_id=scope_id,
                daily_limit_cny=daily,
                monthly_limit_cny=monthly,
                enforcement=body.enforcement,
                audit_exempt=body.audit_exempt,
                updated_by=operator.actor,
                reason=body.reason,
                expected_updated_at=body.expected_updated_at,
                domain=body.domain,
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


@router.get("/admin/budgets/reconciliation")
async def get_budget_reconciliation(
    limit: int = Query(100, ge=1, le=500),
    include_closed: bool = Query(False, description="包含已核销/归档预占"),
    include_test: bool = Query(False, description="包含测试/评测数据"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """待对账队列：用量未知（可能已计费）的预占 + 总览。

    惰性触发滞留回收（sweep 幂等，只有 status='reserved' 且超龄的行会被
    处理），保证页面打开时队列是新鲜的；回收本身只转状态/释放旧周期占额，
    不伪造结算金额。
    2026-10-02 企业口径：附对账日报段（usage 侧 cost_status 分布 + 派生
    比率）——队列是下钻材料，人只看聚合比率。
    第二批：默认只显示开态（resolution=''）且非测试/评测数据；总览占额
    同步只统计开态（与明细口径一致）。
    """
    del operator
    store = _store()
    try:
        sweep = store.sweep_stale_reservations()
        summary = _json_value(store.reconciliation_summary())
        from backend.infra.llm.reconciliation_report import (
            build_reconciliation_report,
        )

        report = build_reconciliation_report(quota_summary=summary)
        return {
            "summary": summary,
            "items": _json_value(
                store.list_pending_review(
                    limit, include_closed=include_closed, include_test=include_test,
                )
            ),
            "sweep": sweep,
            "report": {
                "usage": report.get("usage") or {},
                "derived": report.get("derived") or {},
            },
        }
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "待对账队列暂不可用") from exc


@router.post("/admin/budgets/reconciliation/resolve")
async def resolve_budget_reconciliation(
    body: BudgetResolveRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """单笔核销/归档待对账预占（第二批闭环）。

    settled_actual = 按供应商实际账单金额入账；released = 确认未计费释放；
    written_off = 无法确认、接受不记账；archived = 超期归档。
    除 settled_actual 外均不写入成本，不伪造费用。核销同步释放账本占额，
    审计写入 budget_reservation_resolutions（append-only）。幂等：重复调用
    返回 already_resolved，不二次释放。
    """
    if body.action not in _RESOLUTION_ACTIONS:
        raise HTTPException(422, f"核销动作非法: {body.action}")
    amount = _resolve_amount(body.actual_cny, body.action)

    def persist() -> dict[str, Any]:
        return _json_value(
            _store().resolve_needs_review(
                body.reservation_id,
                action=body.action,
                reason=body.reason,
                resolved_by=operator.actor,
                actual_cny=amount,
            )
        )

    try:
        return run_governance_mutation(
            request,
            operator,
            "budget.reconciliation.resolve",
            body.model_dump(mode="json"),
            persist,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "待对账核销暂不可用") from exc


@router.post("/admin/budgets/reconciliation/resolve-batch")
async def resolve_budget_reconciliation_batch(
    body: BudgetResolveBatchRequest,
    request: Request,
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """批量核销/归档：逐笔幂等执行，单笔失败不影响其余。

    用于存量迁移的批量动作（archived / released / written_off）；返回
    逐笔结果与计数，供前端展示成功/跳过明细。
    """
    if body.action not in _RESOLUTION_ACTIONS:
        raise HTTPException(422, f"核销动作非法: {body.action}")
    amount = _resolve_amount(body.actual_cny, body.action)
    unique_ids = list(dict.fromkeys(body.reservation_ids))
    store = _store()

    def persist() -> dict[str, Any]:
        results = [
            _json_value(
                store.resolve_needs_review(
                    rid,
                    action=body.action,
                    reason=body.reason,
                    resolved_by=operator.actor,
                    actual_cny=amount,
                )
            )
            for rid in unique_ids
        ]
        return {
            "results": results,
            "requested": len(unique_ids),
            "resolved": sum(
                1 for r in results if r.get("found") and not r.get("already_resolved")
            ),
            "already_resolved": sum(1 for r in results if r.get("already_resolved")),
        }

    try:
        return run_governance_mutation(
            request,
            operator,
            "budget.reconciliation.resolve_batch",
            {"action": body.action, "reason": body.reason,
             "count": len(unique_ids), "ids_sample": unique_ids[:10]},
            persist,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "待对账批量核销暂不可用") from exc


@router.get("/admin/budgets/reconciliation/dry-run")
async def dry_run_budget_backlog_migration(
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """存量待对账分类 dry-run（只读）：test_eval / local_unpriced /
    small_unknown / large_unknown 四类，给出单数、占额、建议动作与样例。
    批量执行须走 resolve-batch，写库前需人工逐类确认。"""
    del operator
    try:
        return _json_value(_store().dry_run_backlog_migration())
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "存量迁移 dry-run 暂不可用") from exc


@router.get("/admin/budgets/reconciliation/monthly")
async def get_budget_reconciliation_monthly(
    months: int = Query(6, ge=1, le=24, description="回溯自然月数（含当月）"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> dict[str, Any]:
    """月度对账聚合（路线图第 4 块 PR-A）：usage 成本 / 预算月行 /
    预占结算形态按月分布，只读。CSV 明细导出走 .csv 子路径。"""
    del operator
    try:
        return _json_value(_store().monthly_reconciliation(months=months))
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "月度对账聚合暂不可用") from exc


@router.get("/admin/budgets/reconciliation/monthly.csv")
async def get_budget_reconciliation_monthly_csv(
    months: int = Query(6, ge=1, le=24, description="回溯自然月数（含当月）"),
    operator: OperatorIdentity = Depends(require_admin_user),
) -> Response:
    """月度对账 CSV 导出：月 × 维度（供应商/模型/租户/合计）usage 成本行，
    与供应商月账单核对的基线材料（PR-B）。"""
    del operator
    try:
        content = _store().monthly_reconciliation_csv(months=months)
    except QuotaConfigurationError as exc:
        raise HTTPException(503, "月度对账 CSV 暂不可用") from exc
    month_tag = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="monthly-reconciliation-{month_tag}.csv"'
            )
        },
    )
