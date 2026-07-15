"""报告路由 — 自动化报告生成（SQL→模板→图表→润色）"""
import asyncio
from fastapi import APIRouter
from backend.app.api.schemas import ReportRequest, ErrorResponse
from backend.app.api.deps import get_report_generator

router = APIRouter(prefix="/report", tags=["报告"])


@router.post("", responses={500: {"model": ErrorResponse}})
async def generate_report(req: ReportRequest):
    """根据报告类型 + 筛选条件生成 Markdown 报告"""
    gen = get_report_generator()
    report = await asyncio.to_thread(
        gen.generate, req.report_type, req.filters, req.user_id, req.polish,
    )
    return {"report": report, "report_type": req.report_type}
