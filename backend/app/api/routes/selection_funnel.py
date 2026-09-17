"""selection_funnel REST API — 智能选品导入通道

漏斗海选数据源的主入口：把生意参谋 / 竞品分析工具导出的表格
（CSV / XLSX / Excel 复制出的 TSV 文本）批量导入候选池，
pool_builder 以导入池为主源、watchlist 为兜底（2026-09-17 拍板 A 方案）。

路由前缀: /selection-funnel（经 next.config.js rewrite 由 /api/selection-funnel 代理）
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.selection_funnel import import_pool as _ip
from backend.shared.logger import logger

router = APIRouter(prefix="/selection-funnel", tags=["智能选品"])


class TextImportRequest(BaseModel):
    """对话粘贴场景：Excel 直接 Ctrl+C 复制的 TSV 文本。"""
    content: str = Field(..., min_length=1, description="表格文本（TSV 或 CSV）")
    category: str = Field("", description="批次级类目（行内类目列优先）")
    platform: str = Field("", description="批次级平台（行内平台列优先）")


class ImportResult(BaseModel):
    batch_id: str = ""
    count: int = 0
    notes: list[str] = []


@router.post("/import/file", response_model=ImportResult, summary="上传表格文件导入候选池")
async def import_file(
    file: UploadFile = File(..., description="CSV / TSV / XLSX 表格，首行为表头"),
    category: str = Form("", description="批次级类目"),
    platform: str = Form("", description="批次级平台"),
) -> ImportResult:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    batch_id, count, notes = _ip.import_table(data, category=category, platform=platform,
                                     filename=file.filename or "")
    if not batch_id:
        raise HTTPException(status_code=400, detail=notes[0] if notes else "导入失败")
    logger.info("[SelectionFunnelAPI] 文件导入 %s：%d 条", batch_id, count)
    return ImportResult(batch_id=batch_id, count=count, notes=notes)


@router.post("/import/text", response_model=ImportResult, summary="粘贴表格文本导入候选池")
async def import_text(req: TextImportRequest) -> ImportResult:
    batch_id, count, notes = _ip.import_table(req.content, category=req.category,
                                     platform=req.platform, filename="")
    if not batch_id:
        raise HTTPException(status_code=400, detail=notes[0] if notes else "导入失败")
    logger.info("[SelectionFunnelAPI] 文本导入 %s：%d 条", batch_id, count)
    return ImportResult(batch_id=batch_id, count=count, notes=notes)


@router.get("/import/candidates", summary="查看导入候选（粗过滤）")
async def list_candidates(category: str = "", platform: str = "") -> dict:
    items = _ip.get_import_store().list_candidates(category=category, platform=platform)
    return {"count": len(items), "items": items}


@router.delete("/import/batch/{batch_id}", summary="清除指定导入批次")
async def clear_batch(batch_id: str) -> dict:
    removed = _ip.get_import_store().clear_batch(batch_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"batch_id": batch_id, "removed": removed}
