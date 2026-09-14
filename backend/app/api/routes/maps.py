"""app/api/routes/map.py — 腾讯位置服务后端代理

**为什么所有地图能力都要经后端转发，而不是让前端直连腾讯**：

腾讯 WebService API 的 Key 是明文放在 URL 查询串里的。前端直连意味着 Key
出现在浏览器地址栏、开发者工具、页面 Referer 与前端构建产物中 —— 任何人
都能拿到并用尽你的配额。经后端代理后：

  - Key 只存在于服务端 ``.env``，绝不下发
  - 可在服务端统一加缓存与节流，避免前端重复点击打爆配额
  - 前端只需调 ``/api/map/*``（Next.js 会重写到 ``/map/*``）

前端渲染交互式腾讯地图（GL JS）仍需在浏览器侧带 Key，那是另一条链路：
请在腾讯控制台为该 Key 配置 **Referer 域名白名单**；若为商用，应改为
服务端密钥代理模式。静态图（``/static-map``）不需要前端 Key，本路由已代理。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from backend.config import map as MAP
from backend.infra.http.tencent_lbs import TencentLbsError
from backend.infra.lbs import api
from backend.shared.logger import logger

router = APIRouter(prefix="/map", tags=["地图-腾讯位置服务"])

# 静态图在浏览器端可缓存 10 分钟：底图内容按坐标确定，短期不会变
_STATIC_MAP_CACHE_SECONDS = 600

# 演示台 HTML 存放在仓库 docs/ 下（routes → api → app → backend → 仓库根）
_DEMO_HTML = Path(__file__).resolve().parents[4] / "docs" / "tencent-lbs-demo.html"


def _ensure_configured() -> None:
    if not MAP.is_configured():
        raise HTTPException(
            status_code=503,
            detail="腾讯位置服务未配置：请在服务端 .env 设置 TENCENT_LBS_KEY",
        )


def _guard(action: str):
    """把底层异常转成合适的 HTTP 状态码。

    区分两类失败很重要：配额/鉴权问题（502，运维要处理）与
    「查不到」（200 + 空结果，业务正常），前端不应把后者当故障弹错。
    """
    def _wrap(e: Exception) -> HTTPException:
        if isinstance(e, TencentLbsError):
            logger.warning("[MapAPI] %s 失败: %s", action, e)
            if e.is_quota:
                return HTTPException(status_code=429, detail=f"腾讯位置服务配额受限: {e}")
            if e.is_fatal:
                return HTTPException(status_code=502, detail=f"腾讯位置服务鉴权失败: {e}")
            return HTTPException(status_code=502, detail=f"{action} 失败: {e}")
        logger.warning("[MapAPI] %s 异常: %s", action, e)
        return HTTPException(status_code=502, detail=f"{action} 失败: {e}")

    return _wrap


@router.get("/health", summary="地图能力自检")
async def map_health():
    """返回当前 LBS 配置与可用能力清单（不发起真实请求，不泄露 Key）。"""
    return api.capability_report()


# =============================================
# 定位与地址解析
# =============================================
@router.get("/geocode", summary="地址转坐标")
async def geocode(address: str = Query(..., description="地址文本"),
                  city: str = Query("", description="限定城市，用于消歧")):
    _ensure_configured()
    try:
        result = api.geocode(address, region=city or None)
    except Exception as e:  # noqa: BLE001
        raise _guard("地理编码")(e) from e
    if result is None:
        return {"found": False, "address": address, "result": None}
    return {"found": True, "address": address, **result}


@router.get("/reverse-geocode", summary="坐标转地址")
async def reverse_geocode(location: str = Query(..., description="纬度,经度"),
                          with_poi: bool = Query(False, description="是否附带周边 POI")):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    coord = parse_lat_lng(location)
    if coord is None:
        raise HTTPException(status_code=400,
                            detail="location 格式错误，应形如 26.0824,119.2968（纬度在前）")
    try:
        result = api.reverse_geocode(*coord, get_poi=with_poi)
    except Exception as e:  # noqa: BLE001
        raise _guard("逆地理编码")(e) from e
    if result is None:
        return {"found": False, "result": None}
    return {"found": True, "lat": coord[0], "lng": coord[1], **result}


@router.get("/ip-location", summary="IP 定位")
async def ip_location(ip: str = Query("", description="留空则查询请求方出口 IP")):
    _ensure_configured()
    try:
        result = api.ip_location(ip or None)
    except Exception as e:  # noqa: BLE001
        raise _guard("IP 定位")(e) from e
    if result is None:
        return {"found": False, "result": None}
    return {"found": True, **result}


@router.get("/district", summary="行政区划查询")
async def district(keyword: str = Query("", description="行政区名称，如 福州"),
                   parent_id: str = Query("", description="行政区 id，填写则查下级")):
    _ensure_configured()
    try:
        if parent_id:
            children = api.district_children(parent_id)
            if children is None:
                raise HTTPException(status_code=502, detail="查询下级行政区失败")
            return {"count": len(children), "children": children}
        if not keyword:
            raise HTTPException(status_code=400, detail="keyword 与 parent_id 至少填写一个")
        hits = api.district_search(keyword)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _guard("行政区划查询")(e) from e
    if hits is None:
        raise HTTPException(status_code=502, detail="行政区划查询失败")
    return {"count": len(hits), "districts": hits}


@router.get("/coord/convert", summary="坐标系换算")
async def coord_convert(location: str = Query(..., description="纬度,经度"),
                        from_type: str = Query("wgs84",
                                               description="wgs84 / gcj02 / bd09")):
    """本地纯函数换算（不消耗配额）。需要与腾讯逐位对齐时用批量接口。"""
    from backend.infra.lbs import geo

    coord = geo.parse_lat_lng(location)
    if coord is None:
        raise HTTPException(status_code=400, detail="location 格式错误，应形如 26.0824,119.2968")
    try:
        lat, lng = geo.to_gcj02(coord[0], coord[1], from_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"lat": round(lat, 6), "lng": round(lng, 6), "crs": "gcj02",
            "source_crs": from_type.lower()}


@router.get("/coord/convert-batch", summary="坐标系换算（服务端算法）")
async def coord_convert_batch(locations: str = Query(..., description="以 ; 分隔的坐标串"),
                              from_type: int = Query(1, ge=1, le=4,
                                                     description="1=GPS→GCJ02 2=GCJ02→GPS 3=百度→GCJ02 4=GCJ02→百度")):
    _ensure_configured()
    from backend.infra.lbs import geo

    points = [geo.parse_lat_lng(p) for p in locations.split(";") if p.strip()]
    if not points or any(p is None for p in points):
        raise HTTPException(status_code=400, detail="locations 含无法解析的坐标")
    try:
        converted = api.coord_translate(points, from_type=from_type)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise _guard("坐标换算")(e) from e
    if converted is None:
        raise HTTPException(status_code=502, detail="坐标换算失败")
    return {"count": len(converted),
            "locations": [{"lat": a, "lng": b} for a, b in converted]}


# =============================================
# 地点检索
# =============================================
@router.get("/place/search", summary="地点检索")
async def place_search(keyword: str = Query(..., description="检索词"),
                       city: str = Query("", description="限定城市"),
                       near: str = Query("", description="周边检索中心点 纬度,经度"),
                       radius: int = Query(0, ge=0, le=50000, description="周边半径（米）"),
                       page_size: int = Query(10, ge=1, le=20)):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    near_coord = parse_lat_lng(near) if near else None
    if near and near_coord is None:
        raise HTTPException(status_code=400, detail="near 格式错误，应形如 26.0824,119.2968")
    try:
        results = api.place_search(keyword, region=city or None, near=near_coord,
                                  radius=radius or None, page_size=page_size)
    except Exception as e:  # noqa: BLE001
        raise _guard("地点检索")(e) from e
    if results is None:
        raise HTTPException(status_code=502, detail="地点检索失败")
    return {"count": len(results), "boundary": "nearby" if near_coord else "region",
            "pois": results}


@router.get("/place/suggest", summary="关键词联想")
async def place_suggest(keyword: str = Query(...), city: str = Query("")):
    _ensure_configured()
    try:
        results = api.place_suggestion(keyword, region=city or None)
    except Exception as e:  # noqa: BLE001
        raise _guard("地点联想")(e) from e
    if results is None:
        raise HTTPException(status_code=502, detail="地点联想失败")
    return {"count": len(results),
            "suggestions": [{"name": p["name"], "lat": p["lat"], "lng": p["lng"],
                             "city": p["city"], "district": p["district"]}
                            for p in results]}


# =============================================
# 路线
# =============================================
@router.get("/route", summary="路线规划")
async def route(from_location: str = Query(..., alias="from", description="纬度,经度"),
                to_location: str = Query(..., alias="to", description="纬度,经度"),
                mode: str = Query("driving", description="driving/walking/bicycling/transit"),
                policy: str = Query("", description="驾车策略，可选")):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    src, dst = parse_lat_lng(from_location), parse_lat_lng(to_location)
    if src is None or dst is None:
        raise HTTPException(status_code=400, detail="from/to 格式错误，应形如 26.0824,119.2968")
    try:
        result = api.direction(mode, *src, *dst, policy=policy.upper() or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise _guard("路线规划")(e) from e
    if result is None:
        return {"found": False, "result": None,
                "hint": "两地可能过近、跨海或该出行方式未覆盖"}
    return {"found": True, **result}


@router.get("/distance-matrix", summary="距离矩阵")
async def distance_matrix(from_locations: str = Query(..., alias="from", description="以 ; 分隔"),
                          to_locations: str = Query(..., alias="to", description="以 ; 分隔"),
                          mode: str = Query("driving", description="driving/walking")):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    src = [parse_lat_lng(p) for p in from_locations.split(";") if p.strip()]
    dst = [parse_lat_lng(p) for p in to_locations.split(";") if p.strip()]
    if not src or not dst or any(p is None for p in src + dst):
        raise HTTPException(status_code=400, detail="from/to 含无法解析的坐标")
    try:
        matrix = api.distance_matrix(mode, src, dst)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise _guard("距离矩阵")(e) from e
    if matrix is None:
        raise HTTPException(status_code=502, detail="距离矩阵计算失败")
    return {"mode": mode, "from_count": len(src), "to_count": len(dst), "matrix": matrix}


# =============================================
# 静态图（代理转发，前端无需 Key）
# =============================================
@router.get("/static-map", summary="静态地图图片（代理）", response_class=Response,
            responses={200: {"content": {"image/png": {}}}})
async def static_map(center: str = Query("", description="纬度,经度；有 markers 时可省"),
                     zoom: int = Query(14, ge=4, le=18),
                     size: str = Query("600*400", description="宽*高"),
                     maptype: str = Query("roadmap",
                                          description="roadmap / satellite / hybrid"),
                     markers: str = Query("", description="以 ; 分隔的 '纬度,经度[,标注字符]'")):
    """取静态图并以 PNG 回给前端。

    Key 在此处由服务端注入，前端拿到的只是图片字节流 —— 图片中不含 Key，
    浏览器也不会因此持有 Key。
    """
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    center_coord = parse_lat_lng(center) if center else None
    if center and center_coord is None:
        raise HTTPException(status_code=400, detail="center 格式错误，应形如 26.0824,119.2968")

    marker_list: list[dict] = []
    for chunk in (markers or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) not in (2, 3):
            raise HTTPException(status_code=400,
                                detail="markers 格式错误，应为 '纬度,经度[,标注字符]'")
        coord = parse_lat_lng(f"{parts[0]},{parts[1]}")
        if coord is None:
            raise HTTPException(status_code=400, detail=f"markers 坐标无法解析: {chunk}")
        marker_list.append({
            "lat": coord[0], "lng": coord[1],
            "label": parts[2] if len(parts) == 3 else "",
        })

    if center_coord is None and not marker_list:
        raise HTTPException(status_code=400, detail="center 与 markers 不能同时为空")

    png = api.static_map_bytes(center=center_coord, zoom=zoom, size=size,
                               maptype=maptype, markers=marker_list)
    if not png:
        raise HTTPException(status_code=502, detail="静态图生成失败（可能是配额或参数越界）")

    return Response(
        content=png, media_type="image/png",
        headers={"Cache-Control": f"public, max-age={_STATIC_MAP_CACHE_SECONDS}"},
    )


# =============================================
# 天气
# =============================================
@router.get("/weather", summary="天气查询")
async def weather(city: str = Query("", description="城市名，如 福州"),
                  location: str = Query("", description="坐标 纬度,经度，精度高于城市名"),
                  kind: str = Query("now", description="now(实时) / future(未来几天) / hours(逐小时)")):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    if kind not in api.WEATHER_KINDS:
        raise HTTPException(status_code=400,
                            detail=f"kind 仅支持 {list(api.WEATHER_KINDS)}")
    coord = parse_lat_lng(location) if location else None
    if location and coord is None:
        raise HTTPException(status_code=400, detail="location 格式错误，应形如 26.0824,119.2968")
    if coord is None and not city.strip():
        raise HTTPException(status_code=400, detail="city 与 location 至少填写一个")

    try:
        result = (api.weather(location=coord, kind=kind) if coord is not None
                  else api.weather_for_city(city.strip(), kind=kind))
    except Exception as e:  # noqa: BLE001
        raise _guard("天气查询")(e) from e
    if result is None:
        return {"found": False, "result": None}
    return {"found": True, **result}


@router.get("/services", summary="附加服务探活（真实发起请求）")
async def services():
    """一眼看出天气/街景/行政区划/路线规划哪些已经能用。"""
    _ensure_configured()
    return api.service_availability()


# =============================================
# 街景（代理转发，前端无需 Key）
# =============================================
@router.get("/street-view/pano", summary="查街景全景点")
async def street_view_pano(location: str = Query(..., description="纬度,经度"),
                           radius: int = Query(50, ge=1, le=1000, description="搜索半径（米）")):
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    coord = parse_lat_lng(location)
    if coord is None:
        raise HTTPException(status_code=400, detail="location 格式错误，应形如 26.0824,119.2968")
    try:
        return api.street_view_pano(coord[0], coord[1], radius=radius)
    except Exception as e:  # noqa: BLE001 — 113 的申请指引在异常消息里，要原样透出
        raise _guard("街景查询")(e) from e


@router.get("/street-view", summary="街景全景图片（代理）", response_class=Response,
            responses={200: {"content": {"image/jpeg": {}}}})
async def street_view(pano: str = Query(..., description="全景点 id"),
                      heading: int = Query(0, ge=0, le=360, description="水平朝向，0=正北"),
                      pitch: int = Query(0, ge=-90, le=90, description="垂直俯仰，负数向下"),
                      width: int = Query(640, ge=50, le=2048),
                      height: int = Query(480, ge=50, le=2048)):
    _ensure_configured()
    img = api.street_view_image_bytes(pano, heading=heading, pitch=pitch,
                                      width=width, height=height)
    if not img:
        raise HTTPException(
            status_code=502,
            detail="街景图片获取失败（服务未开通或该全景点已失效）",
        )
    return Response(content=img, media_type="image/jpeg",
                    headers={"Cache-Control": f"public, max-age={_STATIC_MAP_CACHE_SECONDS}"})


# =============================================
# 导航调起（URI API）
# =============================================
@router.get("/navigate", summary="生成地图调起导航链接")
async def navigate(to_location: str = Query(..., alias="to", description="目的地 纬度,经度"),
                   to_name: str = Query("", description="目的地名称"),
                   from_location: str = Query("", alias="from", description="起点 纬度,经度；留空用当前位置"),
                   from_name: str = Query("", description="起点名称"),
                   mode: str = Query("drive", description="drive/walk/bus/bike"),
                   policy: int = Query(0, ge=0, le=3, description="驾车策略 0默认 1避堵 2避收费 3不走高速")):
    """**Web 端没有腾讯导航 SDK**，真导航只能调起腾讯地图 App，本接口生成该调起链接。

    返回值里的 ``key_kind`` 决定这个 URL 能不能给浏览器：

    - ``frontend`` —— 用的是前端专用 Key（配了 Referer 白名单），可以安全外发
    - ``backend``  —— 回退用了后端 Key，URL 里含密钥，**不要外发**；
      请在 ``.env`` 配置 ``TENCENT_LBS_FRONTEND_KEY``

    后端做了这层区分，是因为实测腾讯的 302 跳转会把传入的 Key 原样带到
    跳转目标 URL 上，后端代理也藏不住 —— 只能靠「拆两个 Key」来隔离风险。
    """
    _ensure_configured()
    from backend.infra.lbs.geo import parse_lat_lng

    dst = parse_lat_lng(to_location)
    src = parse_lat_lng(from_location) if from_location else None
    if dst is None:
        raise HTTPException(status_code=400, detail="to 格式错误，应形如 26.0824,119.2968")
    if from_location and src is None:
        raise HTTPException(status_code=400, detail="from 格式错误，应形如 26.0824,119.2968")

    try:
        return api.navigation_uri(
            to_lat=dst[0], to_lng=dst[1], to_name=to_name,
            from_lat=src[0] if src else None, from_lng=src[1] if src else None,
            from_name=from_name, mode=mode, policy=policy,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise _guard("导航调起")(e) from e


# =============================================
# 演示台（同源托管，避免 file:// 打开时的 CORS 拦截）
# =============================================
@router.get("/demo", summary="能力演示台（无需前端密钥）", response_class=HTMLResponse)
async def demo():
    """把 docs/tencent-lbs-demo.html 以同源页面提供给浏览器。

    为什么不直接双击那个 HTML 文件：``file://`` 页面的 Origin 为 null，
    会被后端的 CORS 策略拦下，所有请求都会失败。经本路由打开即同源，
    演示台里的相对路径请求可以直接工作。
    """
    if not _DEMO_HTML.exists():
        raise HTTPException(status_code=404, detail=f"演示页不存在: {_DEMO_HTML}")
    return HTMLResponse(_DEMO_HTML.read_text(encoding="utf-8"))
