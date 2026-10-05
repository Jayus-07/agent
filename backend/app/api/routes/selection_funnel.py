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

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from backend.app.api.deps import resolve_operator_role
from backend.selection_funnel import import_pool as _ip
from backend.selection_funnel import market_data as _md
from backend.shared.logger import logger

# 域运行时收口（2026-09-24 STOP A）：导入通道写共享选品池，与决策任务同档，
# 必须过运营角色门禁（JWT 用户取平台角色，服务凭据走内部令牌），此前无任何身份门禁。
router = APIRouter(
    prefix="/selection-funnel",
    tags=["智能选品"],
    dependencies=[Depends(resolve_operator_role)],
)

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


class FunnelRunRequest(BaseModel):
    """选品专属页直达漏斗请求（多域隔离收官 M4）。

    message 可空：页面表单的 category/platform 已足够 brief_node 建槽；
    传了则作为 user_message 参与槽位解析（与主图进域的口径一致）。
    conversation_id 传同一值即跨轮续跑（need_info 后补参重跑候选不丢）。
    """

    message: str = Field("", max_length=2000, description="补充诉求（可空）")
    category: str = Field("", max_length=100, description="类目")
    platform: str = Field("", max_length=50, description="平台（仅 products 池用）")
    conversation_id: str = Field("", max_length=100, description="会话锚点（空=新建）")


@router.post("/run", summary="执行选品漏斗（专属页直达，不经主图）")
def run_funnel(req: FunnelRunRequest, request: Request) -> dict:
    """复用主图域适配器 selection_funnel_graph_node（同一条执行链）：
    淘空/缺槽如实收尾、E1 候选同步 ConversationContext、执行标签埋点全同。
    同步 def → FastAPI 线程池执行，不阻塞事件循环（漏斗为秒级线性管线，
    一期不做 SSE 流，报告整包返回）。"""
    from backend.orchestration.graph.selection_funnel_graph_node import (
        selection_funnel_graph_node,
    )

    from backend.app.api.identity import resolve_identity

    identity = resolve_identity(request)
    user_id = str(getattr(identity, "user_id", "") or "anonymous")
    tenant_id = str(getattr(identity, "tenant_id", "") or "default")
    conversation_id = req.conversation_id.strip() or f"sf-{uuid.uuid4().hex[:12]}"

    message = req.message.strip()
    if not message:
        slots = "、".join(x for x in (req.category, req.platform) if x)
        message = f"帮我做一次智能选品" + (f"（{slots}）" if slots else "")

    result = selection_funnel_graph_node({
        "question": message,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_id": conversation_id,
        "funnel_context": {
            "conversation_id": conversation_id,
            "category": req.category.strip(),
            "platform": req.platform.strip(),
            "source": "selection_page",
        },
    })
    return {
        "conversation_id": conversation_id,
        "final_answer": result.get("final_answer", ""),
        "funnel_context": result.get("funnel_context") or {},
    }


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
    if batch_id.startswith("imp-"):
        removed = _ip.get_import_store().clear_batch(batch_id)
    else:  # kw- / rv- → 市场库（关键词榜/差评），2026-09-17 全流程实测补
        removed = _md.get_market_store().clear_batch(batch_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"批次 {batch_id} 不存在")
    return {"batch_id": batch_id, "removed": removed}
