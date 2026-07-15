"""报告路由 — 自动化报告生成（SQL→模板→图表→润色）

统一入口: 经 orchestration.tools.run_report() → business_report
"""
import asyncio
from fastapi import APIRouter
from backend.app.api.schemas import ReportRequest, ErrorResponse
from backend.orchestration.tools import run_report

router = APIRouter(prefix="/report", tags=["报告"])


@router.post("", responses={500: {"model": ErrorResponse}})
async def generate_report(req: ReportRequest):
    """根据报告类型 + 筛选条件生成 Markdown 报告"""
    report = await asyncio.to_thread(
        run_report, req.report_type, req.filters,
        user_id=req.user_id, polish=req.polish,
    )
    return {"report": report, "report_type": req.report_type}
