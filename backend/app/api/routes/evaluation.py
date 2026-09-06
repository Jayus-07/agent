"""评测集管理 API — 从 trace 创建用例 + 查询评测集 + 运行评测 + 查看结果。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.evaluation.curator import append_case, list_cases
from backend.evaluation.models import ModuleKind
from backend.evaluation.storage import list_runs, load_report
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
async def create_case_from_trace(req: FromTraceRequest):
    """从线上 trace 创建评测用例。"""
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
async def run_evaluation(module: ModuleKind = Query("rag", description="评测模块")):
    """运行评测（当前仅支持 rag 模块离线评测）。"""
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
                faithfulness=rag.metrics.get("gen_S7_faithfulness", 0.0) if rag else 0.0,
                answer_correctness=rag.metrics.get("gen_S6_answer_correctness", 0.0) if rag else 0.0,
                recall_at_5=rag.metrics.get("recall@5", 0.0) if rag else 0.0,
                reject_accuracy=rag.metrics.get("reject_accuracy", 0.0) if rag else 0.0,
                mrr=rag.metrics.get("mrr", 0.0) if rag else 0.0,
                ndcg_at_10=rag.metrics.get("ndcg@10", 0.0) if rag else 0.0,
                timestamp=report.timestamp,
            ))
        except Exception:
            summaries.append(RunSummary(run_id=run_id))
    return summaries


@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str):
    """获取单次评测的完整报告。"""
    try:
        report, meta = load_report(run_id)
        return {
            "run_id": run_id,
            "report": report.model_dump(mode="json"),
            "meta": meta,
        }
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    except Exception as e:
        logger.error(f"加载评测报告失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
