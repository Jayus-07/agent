"""评测集管理 API — 从 trace 创建用例 + 查询评测集 + 运行评测 + 查看结果。"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.evaluation.curator import append_case, list_cases
from backend.evaluation.models import ModuleKind
from backend.evaluation.storage import (
    list_runs,
    load_report,
    read_run_status,
    request_cancel,
    validate_run_id,
)
from backend.evaluation.trace_bridge import build_test_case_from_trace
from backend.evaluation.weekly import run_weekly_rag_eval
from backend.observability.tracer import trace_collector
from backend.shared.logger import logger

router = APIRouter(prefix="/evaluation", tags=["评测"])

# C7-2/REL-08（2026-10-04 企业评审拍板）：当前为**单租户 default 架构**，
# 评测端点挂租户校验钩子作为扩租户时的启用点——非 default 租户显式 403
# （诚实拒绝，不静默按 default 放行）；扩租户时在此钩子上替换为真实
# 归属校验（run 元数据带 tenant + 查看者租户比对）。
EVAL_TENANT_SCOPE_ENABLED = os.getenv(
    "EVAL_TENANT_SCOPE_ENFORCED", "true",
).strip().lower() not in ("0", "false", "no")
SUPPORTED_EVAL_TENANTS = {"default"}


async def _eval_tenant_scope(
    x_tenant_id: str | None = Header(None, alias="X-Tenant-Id"),
) -> str:
    """租户校验钩子：单租户架构下恒 default，其他值显式拒绝。"""
    tenant = (x_tenant_id or "default").strip() or "default"
    if EVAL_TENANT_SCOPE_ENABLED and tenant not in SUPPORTED_EVAL_TENANTS:
        raise HTTPException(
            status_code=403,
            detail=(
                f"评测功能当前仅支持单租户 default（收到 {tenant!r}）；"
                f"跨租户访问在扩租户架构落地前不可用"
            ),
        )
    return tenant


class FromTraceRequest(BaseModel):
    trace_id: str
    module: ModuleKind | None = None
    expected: dict[str, Any] | None = None
    note: str = ""


class CaseDTO(BaseModel):
    id: str
    question: str
    module: str
    expected: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendResultDTO(BaseModel):
    appended: bool
    reason: str
    case_id: str = ""
    path: str = ""


@router.post("/cases/from-trace", response_model=AppendResultDTO)
async def create_case_from_trace(
    req: FromTraceRequest,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """从线上 trace 创建评测用例（P0-01：写操作仅限管理员）。"""
    trace = _find_trace(req.trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace not found: {req.trace_id}")

    case = build_test_case_from_trace(
        trace,
        module=req.module,
        expected_overrides=req.expected,
        note=req.note,
    )

    result = append_case(case)
    return AppendResultDTO(
        appended=result["appended"],
        reason=result["reason"],
        case_id=case.id,
        path=result.get("path", ""),
    )


@router.get("/cases", response_model=list[CaseDTO])
async def list_eval_cases(
    module: ModuleKind = Query(..., description="评测模块"),
):
    """列出指定模块的评测用例。"""
    cases = list_cases(module)
    return [
        CaseDTO(
            id=c.id,
            question=c.question,
            module=c.module,
            expected=c.expected,
            metadata=c.metadata,
        )
        for c in cases
    ]


def _find_trace(trace_id: str) -> dict | None:
    """在 trace_collector 中查找 trace（dict 或 TraceRecord）。"""
    stored = trace_collector.list(limit=200)
    for t in stored:
        tid = t.get("id") if isinstance(t, dict) else getattr(t, "id", "")
        if tid == trace_id:
            return t
    return None


class RunEvalResponse(BaseModel):
    ok: bool
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    top1_accuracy: float = 0.0
    reject_accuracy: float = 0.0
    recall_at_5: float = 0.0
    timestamp: str = ""
    error: str = ""


@router.post("/run", response_model=RunEvalResponse)
async def run_evaluation(
    module: ModuleKind = Query("rag", description="评测模块"),
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """运行评测（当前仅支持 rag 模块离线评测；P0-01：执行仅限管理员）。"""
    if module != "rag":
        raise HTTPException(status_code=400, detail="当前仅支持 rag 模块评测")
    try:
        result = await asyncio.to_thread(run_weekly_rag_eval)
        return RunEvalResponse(**result)
    except Exception as e:
        logger.error(f"评测运行失败: {e}")
        return RunEvalResponse(ok=False, error=str(e))


class RunSummary(BaseModel):
    run_id: str
    module: str = ""
    pass_rate: float = 0.0
    top1_accuracy: float = 0.0
    faithfulness: float = 0.0
    answer_correctness: float = 0.0
    recall_at_5: float = 0.0
    reject_accuracy: float = 0.0
    mrr: float = 0.0
    ndcg_at_10: float = 0.0
    timestamp: str = ""
    # UI-01/02/04：运行状态（running/completed/failed + stale）、评测集溯源、
    # 触发来源与评估器模式（RAGAS 徽标数据源）
    status: str = ""
    stale: bool = False
    suite: str = ""
    dataset_version: str = ""
    trigger: str = ""
    triggered_by: str = ""
    evaluator_mode: str = ""


def _run_status_fields(run_id: str, meta: dict) -> dict:
    """从状态文件与 meta 提取列表页扩展字段（软失败，缺省留空）。"""
    provenance = meta.get("eval_provenance") or {}
    dataset_version = meta.get("dataset_version")
    if not isinstance(dataset_version, str):
        dataset_version = str(provenance.get("dataset_version", "") or "")
    status_info = read_run_status(run_id) or {}
    return {
        "status": str(status_info.get("status", "") or ""),
        "stale": bool(status_info.get("stale", False)),
        "suite": str(provenance.get("suite", "") or ""),
        "dataset_version": dataset_version,
        "trigger": str((meta.get("env") or {}).get("trigger", "") or ""),
        "triggered_by": str((meta.get("env") or {}).get("triggered_by", "") or ""),
        "evaluator_mode": str(meta.get("evaluator_mode", "") or ""),
    }


@router.get("/runs", response_model=list[RunSummary])
async def list_eval_runs(limit: int = Query(20, ge=1, le=100, description="返回数量")):
    """列出历史评测运行记录。"""
    run_ids = list_runs(limit=limit)
    summaries = []
    for run_id in run_ids:
        try:
            report, _meta = load_report(run_id)
            rag = next((s for s in report.summaries if s.module == "rag"), None)
            summaries.append(RunSummary(
                run_id=run_id,
                module=report.module,
                pass_rate=rag.pass_rate if rag else 0.0,
                top1_accuracy=rag.metrics.get("top1_accuracy", 0.0) if rag else 0.0,
                faithfulness=rag.metrics.get("sem_faithfulness", rag.metrics.get("gen_S7_faithfulness", 0.0)) if rag else 0.0,
                answer_correctness=rag.metrics.get("sem_answer_correctness", rag.metrics.get("gen_S6_answer_correctness", 0.0)) if rag else 0.0,
                recall_at_5=rag.metrics.get("recall@5", 0.0) if rag else 0.0,
                reject_accuracy=rag.metrics.get("reject_accuracy", 0.0) if rag else 0.0,
                mrr=rag.metrics.get("mrr", 0.0) if rag else 0.0,
                ndcg_at_10=rag.metrics.get("ndcg@10", 0.0) if rag else 0.0,
                timestamp=report.timestamp,
                **_run_status_fields(run_id, _meta),
            ))
        except Exception:
            summaries.append(RunSummary(run_id=run_id, **_run_status_fields(run_id, {})))
    return summaries


@router.get("/prompt-version-runs")
async def eval_runs_for_prompt_version(
    key: str = Query(..., description="prompt key"),
    version: int = Query(..., ge=1, description="prompt 版本号"),
    limit: int = Query(10, ge=1, le=50),
):
    """治理验收 #4a：某 prompt 版本关联的评测 run（JSONB 包含反查）。

    数据源 ai.eval_run_records.prompt_snapshot（run 时的 PG 权威版本快照），
    返回使用过该版本的 run（含其后发布了新版的对照）——回答「这个版本
    上线时跑过什么评测、结果如何」。
    """
    try:
        from backend.config.database import OBS_DB_PG_CONFIG
        from backend.infra.db import engine_for

        with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT run_id, module, pass_rate, case_count, pass_count,
                       trigger, triggered_by, git_sha, created_at
                FROM ai.eval_run_records
                WHERE prompt_snapshot @> %s::jsonb
                ORDER BY created_at DESC LIMIT %s
                """,
                # 台账写入侧口径 = str(version)（run_records.collect_prompt_snapshot），
                # JSONB @> 严格类型匹配：这里同样转 str 才能命中
                (json.dumps({key: str(version)}), limit))
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — 查询软失败
        # C7-3/UI-07：内部错误细节只进日志，响应不回 str(e)
        logger.error(f"评测台账查询失败: {e}", exc_info=True)
        raise HTTPException(503, "评测台账暂不可用，请稍后重试（详情见服务端日志）") from e
    return {"key": key, "version": version, "runs": [
        {"run_id": r[0], "module": r[1], "pass_rate": r[2],
         "case_count": r[3], "pass_count": r[4], "trigger": r[5],
         "triggered_by": r[6], "git_sha": r[7], "created_at": str(r[8])}
        for r in rows
    ]}


@router.get("/runs/{run_id}")
async def get_eval_run(
    run_id: str,
    _tenant: str = Depends(_eval_tenant_scope),
):
    """获取单次评测的完整报告（C7-1：展示/导出层 PII 脱敏，原始文件不动）。"""
    try:
        report, meta = load_report(run_id)
        from backend.evaluation.export_masking import mask_report_for_viewer

        # P0-02 三层防御·第三层：历史旧 report 文件中仍可能存在 NaN/Infinity
        # 字面量（pydantic 解析放行），出响应前统一清洗，禁止 500 打穿前端
        from backend.shared.jsonable import safe_jsonable

        return {
            "run_id": run_id,
            "report": safe_jsonable(mask_report_for_viewer(report.model_dump(mode="json"))),
            "meta": safe_jsonable(meta),
            "run_status": safe_jsonable(read_run_status(run_id)),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    except Exception as e:
        # C7-3/UI-07：内部异常细节只进日志，响应回统一错误码+可读原因，
        # 不把 str(e)（可能含堆栈/路径/SQL）暴露给客户端
        logger.error(f"加载评测报告失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="评测报告加载失败，请稍后重试或联系管理员（详情见服务端日志）",
        ) from e


class CancelRunResponse(BaseModel):
    run_id: str
    cancel_requested: bool
    run_status: str = ""
    already_cancelled: bool = False
    audit_recorded: bool = False


@router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_eval_run(
    run_id: str,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """C2-1/RUN-03：协作式取消评测运行（P0-01：仅管理员）。

    幂等（RUN-04）：重复取消返回相同状态、不产生重复副作用。取消请求
    落盘后由运行中的 case 循环在检查点轮询生效——已完成的样本保留，
    剩余样本记 skip(reason=cancelled)，终态 cancelled。
    """
    try:
        validate_run_id(run_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        request_cancel(run_id, requested_by=_operator.actor)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"取消请求写入失败: {e}") from e
    status = read_run_status(run_id) or {}
    # C2-6/REL-09：取消动作落审计；审计失败时显式暴露（验收口径「取消必有痕」）
    from backend.evaluation.audit import record_operation

    audit_ok = record_operation(
        "eval_run.cancel", run_id,
        actor=_operator.actor,
        detail=f"run_status_at_cancel={status.get('status', '')}",
    )
    return CancelRunResponse(
        run_id=run_id,
        cancel_requested=True,
        run_status=str(status.get("status", "")),
        already_cancelled=status.get("status") == "cancelled",
        audit_recorded=audit_ok,
    )


@router.get("/runs/{run_id}/operations")
async def list_run_operations(
    run_id: str,
    _operator: OperatorIdentity = Depends(require_admin_user),
):
    """C2-6/REL-09：查看某 run 的操作审计（取消/重跑/熔断）。"""
    from backend.evaluation.audit import list_operations

    return {"run_id": run_id, "operations": list_operations(run_id)}
