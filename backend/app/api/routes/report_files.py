"""report_files.py — 报告图表 artifact 只读服务

图表落盘改造（2026-10-25）的读取端：ChartGenerator 把 PNG 写入
`<数据卷>/report_charts/`，报告 Markdown 引用 `/api/report-files/<name>`。
本路由只服务该目录内的 PNG，basename 白名单校验防路径穿越。
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.shared.logger import logger

router = APIRouter(prefix="/report-files", tags=["报告图表"])

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+\.png$")


def _charts_dir() -> str:
    from backend.business_report.snapshot import _get_snapshot_dir
    return os.path.join(os.path.dirname(_get_snapshot_dir()), "report_charts")


@router.get("/{name}")
async def get_report_chart(name: str):
    """按文件名取图表 PNG（仅 report_charts 目录、仅 .png、拒绝路径分隔）。"""
    if not _SAFE_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail="图表不存在")
    fpath = os.path.join(_charts_dir(), name)
    # 二次防御：解析后必须仍落在目标目录内
    if os.path.realpath(fpath) != os.path.join(
            os.path.realpath(_charts_dir()), name):
        raise HTTPException(status_code=404, detail="图表不存在")
    if not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="图表不存在")
    logger.info(f"[ReportFiles] 读取图表: {name}")
    return FileResponse(fpath, media_type="image/png")
