"""评测集管理 API — 从 trace 创建用例 + 查询评测集 + 运行评测 + 查看结果。"""
from __future__ import annotations

import asyncio
from typing import Any

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, require_admin_user
from backend.evaluation.curator import append_case, list_cases
from backend.evaluation.models import ModuleKind
from backend.evaluation.storage import list_runs, load_report, read_run_status
from backend.evaluation.trace_bridge import build_test_case_from_trace
from backend.evaluation.weekly import run_weekly_rag_eval
from backend.observability.tracer import trace_collector
from backend.shared.logger import logger

router = APIRouter(prefix="/evaluation", tags=["评测"])


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
        raise HTTPException(503, f"评测台账查询失败: {e}")
    return {"key": key, "version": version, "runs": [
        {"run_id": r[0], "module": r[1], "pass_rate": r[2],
         "case_count": r[3], "pass_count": r[4], "trigger": r[5],
         "triggered_by": r[6], "git_sha": r[7], "created_at": str(r[8])}
        for r in rows
    ]}


@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str):
    """获取单次评测的完整报告。"""
    try:
        report, meta = load_report(run_id)
        return {
            "run_id": run_id,
            "report": report.model_dump(mode="json"),
            "meta": meta,
            "run_status": read_run_status(run_id),
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    except Exception as e:
        logger.error(f"加载评测报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
