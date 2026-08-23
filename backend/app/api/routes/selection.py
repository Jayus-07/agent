"""selection REST API — 智能选品结构化端点（spec §7）

路由前缀: /selection（经 next.config.js rewrite 由 /api/selection 代理）。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.selection.recommender import (
    batch_scores,
    compare as do_compare,
    generate_report,
    recommend,
    score_url,
)
from backend.selection.store import DEFAULT_WEIGHTS, get_selection_store
from backend.shared.logger import logger

router = APIRouter(prefix="/selection", tags=["智能选品"])


class ScoreRequest(BaseModel):
    url: str = Field(..., min_length=1, description="商品 URL")
    force_refresh: bool = Field(False, description="强制重算（忽略缓存）")


class WeightsRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="权重字典，key 必须属于五维度")


@router.get("/recommendations")
def recommendations(
    category: str = Query("", description="品类过滤（Phase 2 生效）"),
    platform: Optional[str] = Query(None, description="平台过滤"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    min_score: float = Query(0.0, ge=0.0, le=100.0, description="潜力分下限"),
):
    """推荐列表（潜力分降序 + LLM 理由）"""
    return recommend(limit=limit, platform=platform, min_score=min_score)


@router.get("/trends")
def trends(days: int = Query(30, ge=0, le=365),
           platform: Optional[str] = Query(None)):
    """趋势聚合数据（结构趋势 + 语义检索计数）"""
    from backend.competitor.store import get_store
    from backend.selection.trends import compute_trends
    result = compute_trends(get_store(), days=days, platform=platform)
    # 语义趋势检索作为可选增强，失败不阻塞结构化数据返回
    try:
        from backend.selection.market_index import get_market_index
        hits = get_market_index().search_trends(
            "市场趋势 热卖卖点 促销", k=10,
            metadata_filter={"platform": platform} if platform else None)
        result["sources"]["rag_hits"] = len(hits)
    except Exception as e:
        logger.warning(f"[selection:api] 语义趋势检索失败（忽略）: {e}")
    return result


@router.post("/score")
def score(req: ScoreRequest):
    """单品潜力评估"""
    item = score_url(req.url, force_refresh=req.force_refresh)
    if item is None:
        raise HTTPException(status_code=404, detail=f"无快照数据: {req.url}")
    return item


@router.get("/scores/batch")
def scores_batch(urls: list[str] = Query(..., description="商品 URL 列表")):
    """批量读评分缓存（供监控表潜力分列）"""
    return batch_scores(urls)


@router.get("/compare")
def compare(urls: list[str] = Query(..., min_length=2, description="至少两个 URL")):
    """多品对比数据"""
    return do_compare(urls)


@router.get("/weights")
def get_weights():
    """读取评分权重"""
    return {"weights": get_selection_store().get_weights(), "default": DEFAULT_WEIGHTS}


@router.put("/weights")
def put_weights(req: WeightsRequest):
    """更新评分权重（仅接受五维度 key）"""
    unknown = set(req.weights) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知权重 key: {sorted(unknown)}")
    get_selection_store().set_weights(req.weights)
    return {"weights": get_selection_store().get_weights()}


@router.post("/report")
def report(category: str = "", days: int = Query(30, ge=1, le=365)):
    """选品报告（同步返回 markdown）"""
    return {"report": generate_report(category=category, days=days)}
