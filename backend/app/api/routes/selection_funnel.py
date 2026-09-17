"""selection_funnel REST API — 智能选品导入通道（商品 / 关键词榜 / 差评）

漏斗海选数据源的主入口：把生意参谋 / 竞品分析工具导出的表格
（CSV / XLSX / Excel 复制出的 TSV 文本）批量导入，pool_builder 以导入池
为主源、watchlist 为兜底（2026-09-17 拍板 A 方案）。

kind 三类（2026-09-17 第三轮扩展）：
  products  — 商品候选（标题/价格/评分/评价数/销量/类目/链接…）
  keywords  — 关键词榜（关键词/搜索人气/点击率/转化率/竞争度）→ 赛道画像
  reviews   — 差评（商品标题/评论内容/星级）→ 痛点机会

路由前缀: /selection-funnel（经 next.config.js rewrite 由 /api/selection-funnel 代理）
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from backend.selection_funnel import import_pool as _ip
from backend.selection_funnel import market_data as _md
from backend.shared.logger import logger

router = APIRouter(prefix="/selection-funnel", tags=["智能选品"])

_KINDS = ("products", "keywords", "reviews")


class TextImportRequest(BaseModel):
    """对话粘贴场景：Excel 直接 Ctrl+C 复制的 TSV 文本。"""
    content: str = Field(..., min_length=1, description="表格文本（TSV 或 CSV）")
    kind: str = Field("products", description="products / keywords / reviews")
    category: str = Field("", description="批次级类目（行内类目列优先）")
    platform: str = Field("", description="批次级平台（仅 products 用）")


class ImportResult(BaseModel):
    batch_id: str = ""
    count: int = 0
    notes: list[str] = []


def _dispatch_import(data: bytes | str, kind: str, category: str,
                     platform: str = "", filename: str = "") -> tuple[str, int, list[str]]:
    if kind == "keywords":
        return _md.import_keywords(data, category=category)
    if kind == "reviews":
        return _md.import_reviews(data, category=category)
    if kind == "products":
        return _ip.import_table(data, category=category, platform=platform, filename=filename)
    raise HTTPException(status_code=400, detail=f"未知 kind「{kind}」，支持: {', '.join(_KINDS)}")


@router.post("/import/file", response_model=ImportResult, summary="上传表格导入（商品/关键词榜/差评）")
async def import_file(
    file: UploadFile = File(..., description="CSV / TSV / XLSX 表格，首行为表头"),
    kind: str = Form("products", description="products / keywords / reviews"),
    category: str = Form("", description="批次级类目"),
    platform: str = Form("", description="批次级平台（仅 products 用）"),
) -> ImportResult:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    batch_id, count, notes = _dispatch_import(data, kind, category, platform,
                                              filename=file.filename or "")
    if not batch_id:
        raise HTTPException(status_code=400, detail=notes[0] if notes else "导入失败")
    logger.info("[SelectionFunnelAPI] 文件导入(%s) %s：%d 条", kind, batch_id, count)
    return ImportResult(batch_id=batch_id, count=count, notes=notes)


@router.post("/import/text", response_model=ImportResult, summary="粘贴表格文本导入（商品/关键词榜/差评）")
async def import_text(req: TextImportRequest) -> ImportResult:
    batch_id, count, notes = _dispatch_import(req.content, req.kind, req.category,
                                              req.platform, filename="")
    if not batch_id:
        raise HTTPException(status_code=400, detail=notes[0] if notes else "导入失败")
    logger.info("[SelectionFunnelAPI] 文本导入(%s) %s：%d 条", req.kind, batch_id, count)
    return ImportResult(batch_id=batch_id, count=count, notes=notes)


@router.get("/import/candidates", summary="查看导入候选（粗过滤）")
async def list_candidates(category: str = "", platform: str = "") -> dict:
    items = _ip.get_import_store().list_candidates(category=category, platform=platform)
    return {"count": len(items), "items": items}


@router.get("/market/snapshot", summary="赛道画像（关键词榜统计）")
async def market_snapshot(category: str = "") -> dict:
    snap = _md.market_snapshot(category)
    if not snap:
        return {"count": 0, "top": [], "opportunities": [],
                "hint": "暂无关键词榜数据——POST /selection-funnel/import/text 或 /file 上传（kind=keywords）"}
    return {"count": snap["total"], "top": snap["top"], "opportunities": snap["opportunities"]}


@router.get("/reviews/pain-points", summary="痛点机会（差评聚类，按候选标题）")
async def review_pain_points(category: str = "", titles: str = "") -> dict:
    title_list = [t.strip() for t in titles.split("|") if t.strip()]
    rows = _md.pain_points_for(title_list, category)
    return {"count": len(rows), "items": rows}


@router.delete("/import/batch/{batch_id}", summary="清除指定导入批次")
async def clear_batch(batch_id: str) -> dict:
    removed = _ip.get_import_store().clear_batch(batch_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"batch_id": batch_id, "removed": removed}
