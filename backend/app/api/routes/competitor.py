"""competitor REST API — 竞品监控结构化端点

为前端 /competitors 页面提供 JSON 数据，与 LangChain Tool 共用 CompetitorStore / pipeline。

路由前缀: /competitor（经 next.config.js rewrite 由 /api/competitor 代理）
"""
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.competitor import cookie_manager
from backend.competitor.store import get_store
from backend.shared.logger import logger

router = APIRouter(prefix="/competitor", tags=["竞品监控"])


# ── 请求 / 响应模型 ─────────────────────────────

class AddWatchRequest(BaseModel):
    url: str = Field(..., min_length=1, description="竞品页面 URL")
    name: str = Field("", description="竞品名称")
    platform: str = Field("auto", description="平台标识，auto=自动检测")
    my_sku: str = Field("", description="对应自家商品 SKU")
    frequency: str = Field("daily", description="监控频率: daily / 4h / weekly")


class ToggleRequest(BaseModel):
    url: str = Field(..., description="竞品 URL")
    enabled: bool = Field(True, description="是否启用")


class AnalyzeRequest(BaseModel):
    url: str = Field(..., min_length=1, description="待分析的竞品 URL")
    use_llm: bool = Field(True, description="是否使用 LLM 抽取")


class CookiesRequest(BaseModel):
    cookies: str = Field(..., description="浏览器 Cookie 字符串")
    platform: str = Field("taobao", description="目标平台: taobao/tmall/jd/douyin/pdd/suning/amazon")


class TestCookiesRequest(BaseModel):
    url: str = Field("https://item.taobao.com/item.htm?id=815673415507", description="测试用 URL")


class QrLoginRequest(BaseModel):
    platform: str = Field(..., description="平台: taobao / tmall / jd")


class QrPollRequest(BaseModel):
    platform: str = Field(..., description="平台")
    token: str = Field(..., description="QR token")
    session_cookies: str = Field(..., description="会话 Cookie（启动时返回，内部使用）")


# ── GET /competitor/watchlist ───────────────────

@router.get("/watchlist")
def list_watchlist(enabled_only: bool = Query(True, description="是否仅返回启用项")):
    """列出监控项，附带最新快照价格信息"""
    store = get_store()
    items = store.list_watch(enabled_only=enabled_only)

    enriched = []
    for item in items:
        snap = store.latest_snapshot(item["url"])
        enriched.append({
            **item,
            "enabled": bool(item.get("enabled")),
            "in_stock": bool(item.get("in_stock")) if item.get("in_stock") is not None else None,
            "latest_price": snap.get("price") if snap else None,
            "latest_original_price": snap.get("original_price") if snap else None,
            "latest_currency": snap.get("currency") if snap else "CNY",
            "latest_promo": snap.get("promo_text") if snap else None,
            "latest_review_count": snap.get("review_count") if snap else None,
            "latest_stock": snap.get("in_stock") if snap else None,
            "latest_extract_method": snap.get("extract_method") if snap else None,
            "latest_crawled_at": snap.get("crawled_at") if snap else None,
            "snapshot_count": len(store.history(item["url"], limit=1000)),
        })

    return {"items": enriched, "total": len(enriched)}


# ── POST /competitor/watchlist ──────────────────

@router.post("/watchlist")
def add_watch(req: AddWatchRequest):
    """添加监控项（URL 已存在则更新名称等字段）"""
    from backend.competitor.adapters import detect_platform

    store = get_store()
    platform = req.platform if req.platform != "auto" else detect_platform(req.url)
    name = req.name or req.url[:60]

    item = store.add_watch(
        name=name,
        url=req.url,
        platform=platform,
        my_sku=req.my_sku,
        frequency=req.frequency,
    )

    # 立即建立基线快照（后台执行，不阻塞响应）
    baseline = None
    try:
        from backend.competitor.pipeline import analyze_url
        result = analyze_url(req.url, name=name, use_llm=False)
        snap = store.latest_snapshot(req.url)
        if snap:
            baseline = {
                "price": snap.get("price"),
                "currency": snap.get("currency") or "CNY",
                "crawled_at": snap.get("crawled_at"),
            }
    except Exception as e:
        logger.warning(f"[competitor:api] 基线快照失败: {e}")

    return {"item": item, "baseline": baseline}


# ── DELETE /competitor/watchlist ────────────────

@router.delete("/watchlist")
def remove_watch(url: str = Query(..., description="待移除的竞品 URL")):
    """移除监控项（不删除快照历史）"""
    store = get_store()
    removed = store.remove_watch(url)
    if not removed:
        raise HTTPException(status_code=404, detail=f"监控项不存在: {url}")
    return {"removed": True, "url": url}


# ── PATCH /competitor/watchlist ─────────────────

@router.patch("/watchlist")
def toggle_watch(req: ToggleRequest):
    """启用/停用监控项"""
    store = get_store()
    item = store.toggle_watch(req.url, enabled=req.enabled)
    if not item:
        raise HTTPException(status_code=404, detail=f"监控项不存在: {req.url}")
    return {"item": item}


# ── GET /competitor/history ─────────────────────

@router.get("/history")
def get_history(
    url: str = Query(..., description="竞品 URL"),
    days: int = Query(0, description="最近 N 天（0=全部）"),
    limit: int = Query(50, description="最大返回条数"),
):
    """获取价格历史快照（新→旧排列）"""
    store = get_store()
    snaps = store.history(url, limit=limit)

    if days > 0:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        snaps = [s for s in snaps if s.get("crawled_at", "") >= cutoff]

    # 计算价格变化
    prices = [s["price"] for s in snaps if s.get("price") is not None]
    change = None
    if len(prices) >= 2:
        latest, oldest = prices[0], prices[-1]
        diff = latest - oldest
        pct = diff / oldest * 100 if oldest else 0
        change = {"diff": diff, "pct": pct, "latest": latest, "oldest": oldest}

    watch = store.get_watch_by_url(url)
    return {
        "url": url,
        "name": (watch["name"] if watch else "") or (snaps[0].get("title") if snaps else ""),
        "platform": (watch["platform"] if watch else "") or (snaps[0].get("platform") if snaps else "generic"),
        "snapshots": snaps,
        "price_change": change,
    }


# ── POST /competitor/analyze ────────────────────

@router.post("/analyze")
def analyze_competitor(req: AnalyzeRequest):
    """立即分析一个竞品页面（抓取→抽取→快照入库）"""
    from backend.competitor.pipeline import analyze_url

    try:
        result = analyze_url(req.url, use_llm=req.use_llm)
        return {"result": result, "url": req.url}
    except Exception as e:
        logger.error(f"[competitor:api] 分析失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")


# ── POST /competitor/scan ───────────────────────

@router.post("/scan")
def scan_all():
    """巡检全部启用的监控项"""
    from backend.competitor.pipeline import scan_watchlist

    try:
        report = scan_watchlist()
        return {"report": report}
    except Exception as e:
        logger.error(f"[competitor:api] 巡检失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"巡检失败: {e}")


# ── GET /competitor/stats ───────────────────────

@router.get("/stats")
def get_stats():
    """概览统计"""
    store = get_store()
    all_items = store.list_watch(enabled_only=False)
    enabled_items = [i for i in all_items if i.get("enabled")]

    # 统计今日有抓取的项数 + 降价项数
    today = datetime.now().strftime("%Y-%m-%d")
    scanned_today = 0
    price_drops = 0

    for item in enabled_items:
        snap = store.latest_snapshot(item["url"])
        if snap and snap.get("crawled_at", "").startswith(today):
            scanned_today += 1
            # 检查是否降价：与上一次快照对比
            prev = store.latest_snapshot(item["url"], before_id=snap["id"])
            if (prev and snap.get("price") is not None
                    and prev.get("price") is not None
                    and snap["price"] < prev["price"]):
                price_drops += 1

    return {
        "stats": {
            "total": len(all_items),
            "enabled": len(enabled_items),
            "scanned_today": scanned_today,
            "price_drops": price_drops,
        }
    }


# ── GET /competitor/cookies ───────────────────

@router.get("/cookies")
def get_cookies():
    """查询各平台 Cookie 配置状态（列表，不返回完整值）"""
    items = cookie_manager.list_cookies()
    return {"configured": len(items) > 0, "items": items}


# ── POST /competitor/cookies ──────────────────

@router.post("/cookies")
def save_cookies(req: CookiesRequest):
    """保存某平台 Cookie 到数据库（立即生效，无需重启）"""
    cookies = req.cookies.strip()
    if not cookies:
        raise HTTPException(status_code=400, detail="Cookie 不能为空")
    cookie_manager.save_cookies(req.platform, cookies, "manual")
    return {
        "saved": True,
        "platform": cookie_manager.normalize_platform(req.platform),
        "length": len(cookies),
    }


# ── DELETE /competitor/cookies ────────────────

@router.delete("/cookies")
def clear_cookies(platform: str = Query("", description="指定平台，空=删除全部")):
    """清除 Cookie 配置（按平台或全部）"""
    if platform:
        deleted = cookie_manager.delete_cookies(platform)
        return {"cleared": deleted, "platform": cookie_manager.normalize_platform(platform)}
    removed = cookie_manager.delete_all()
    logger.info(f"[competitor:api] Cookie 全部清除 (removed={removed})")
    return {"cleared": removed > 0, "removed": removed}


# ── POST /competitor/test-cookies ─────────────

@router.post("/test-cookies")
def test_cookies(req: TestCookiesRequest):
    """测试 Cookie 是否生效：用当前 Cookie 抓取指定 URL"""
    from backend.tools.crawler_runtime import crawl
    from backend.competitor.pipeline import _is_login_page

    result = crawl(req.url, mode="markdown", timeout=60.0)
    if not result["ok"]:
        return {"ok": False, "login_intercepted": False, "error": result["error"]}

    content = result["content"]
    is_login = _is_login_page(content)
    has_price = "\u00a5" in content or "\uffe5" in content

    return {
        "ok": True,
        "login_intercepted": is_login,
        "has_price": has_price,
        "content_length": len(content),
        "preview": content[:200],
        "message": (
            "Cookie 生效！已成功获取商品页面内容。" if not is_login else
            "Cookie 未生效，页面仍被重定向到登录页。请检查 Cookie 是否完整且未过期。"
        ),
    }


# ── POST /competitor/qr-login/start ───────────

@router.post("/qr-login/start")
async def qr_login_start(req: QrLoginRequest):
    """启动扫码登录，返回 QR 码图片 URL"""
    from backend.competitor.qr_login import start_qr_login, get_supported_platforms

    if req.platform not in get_supported_platforms():
        raise HTTPException(
            status_code=400,
            detail=f"不支持的平台: {req.platform}，支持: {get_supported_platforms()}",
        )
    try:
        result = await start_qr_login(req.platform)
        logger.info(f"[competitor:api] QR 登录已启动: platform={req.platform}")
        return {"ok": True, **result}
    except Exception as e:
        logger.error(f"[competitor:api] QR 登录启动失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "platform": req.platform}


# ── POST /competitor/qr-login/poll ────────────

@router.post("/qr-login/poll")
async def qr_login_poll(req: QrPollRequest):
    """轮询扫码状态，确认后自动提取 Cookie 并入库"""
    from backend.competitor.qr_login import poll_qr_login

    try:
        result = await poll_qr_login(req.platform, req.token, req.session_cookies)
        return {"ok": True, **result}
    except Exception as e:
        logger.error(f"[competitor:api] QR 轮询失败: {e}", exc_info=True)
        return {"ok": False, "error": str(e), "status": "error"}


# ── GET /competitor/qr-login/platforms ────────

@router.get("/qr-login/platforms")
def qr_login_platforms():
    """返回支持扫码登录的平台列表"""
    from backend.competitor.qr_login import get_supported_platforms
    return {"platforms": get_supported_platforms()}


# ── POST /competitor/retry-blocked ─────────────

@router.post("/retry-blocked")
def retry_blocked_urls():
    """重新抓取所有 login_blocked 状态的监控项"""
    from backend.competitor.pipeline import analyze_url

    store = get_store()
    items = store.list_watch(enabled_only=True)
    results = []
    for item in items:
        snap = store.latest_snapshot(item["url"])
        if snap and snap.get("extract_method") == "login_blocked":
            try:
                report = analyze_url(item["url"], name=item["name"])
                new_snap = store.latest_snapshot(item["url"])
                still_blocked = (
                    new_snap and new_snap.get("extract_method") == "login_blocked"
                )
                results.append({
                    "url": item["url"],
                    "name": item["name"],
                    "ok": not still_blocked,
                    "method": new_snap.get("extract_method") if new_snap else None,
                })
            except Exception as e:
                results.append({
                    "url": item["url"],
                    "name": item["name"],
                    "ok": False,
                    "error": str(e),
                })
    succeeded = sum(1 for r in results if r["ok"])
    logger.info(f"[competitor:api] 重试 {len(results)} 个被拦截的 URL，成功 {succeeded} 个")
    return {
        "retried": len(results),
        "succeeded": succeeded,
        "results": results,
    }


# ── GET /competitor/anti-ban/stats ──────────────

@router.get("/anti-ban/stats")
def anti_ban_stats():
    """防封策略观测: 今日预算/平台配额/熔断状态/Cookie 龄期/风控事件"""
    from backend.competitor import anti_ban
    return anti_ban.stats()


# ── POST /competitor/anti-ban/resume ────────────

@router.post("/anti-ban/resume")
def anti_ban_resume():
    """人工确认后解除 L2 全局停采（单账号场景不做自动重试，恢复必须人工决策）"""
    from backend.competitor import anti_ban
    anti_ban.resume_after_halt()
    return {"ok": True, "message": "全局停采已解除，采集将以保守频率恢复"}


# ── GET /competitor/recommendations ─────────

@router.get("/recommendations")
def competitor_recommendations(
    limit: int = Query(10, ge=1, le=50),
    platform: Optional[str] = Query(None, description="平台过滤"),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
):
    """推荐列表别名端点（直接调选品引擎，与 /selection/recommendations 等价）"""
    from backend.selection.recommender import recommend
    return recommend(limit=limit, platform=platform, min_score=min_score)
