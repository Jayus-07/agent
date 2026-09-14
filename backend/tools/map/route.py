"""tools/map/route.py — 路线规划类工具

这是本接入对旅行域价值最高的部分：把「直尺量出来的直线距离 × 拍脑袋的绕行
系数」换成腾讯真实的道路路径与路况时长。

单位口径已在 ``infra/lbs/api.direction`` 统一归一：原接口 ``/direction`` 用
分钟、``/distance/matrix`` 用秒，本层对外一律同时给 ``duration_s`` 与
``duration_min``，避免 LLM 在两者之间做换算时出错。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.map import _base

_MODE_ALIASES = {
    "drive": "driving", "driving": "driving", "驾车": "driving", "开车": "driving", "打车": "driving",
    "walk": "walking", "walking": "walking", "步行": "walking", "走路": "walking",
    "bike": "bicycling", "bicycling": "bicycling", "骑行": "bicycling", "自行车": "bicycling",
    "transit": "transit", "bus": "transit", "公交": "transit", "地铁": "transit", "公共交通": "transit",
}


def _normalize_mode(mode: str) -> str | None:
    return _MODE_ALIASES.get((mode or "").strip().lower())


@tool
def map_route_tool(from_location: str, to_location: str, mode: str = "driving",
                   policy: str = "") -> str:
    """
    规划两点之间的真实路线（驾车/步行/骑行/公交），返回距离、时长与分段导航指令。
    from_location: 起点坐标 "纬度,经度"，如 "26.0824,119.2968"
    to_location: 终点坐标 "纬度,经度"
    mode: 出行方式，driving/walking/bicycling/transit（也接受中文：驾车/步行/骑行/公交）
    policy: 仅驾车可选，LEAST_TIME(最短时间)/LEAST_DISTANCE(最短距离)/AVOID_HIGHWAY(不走高速)/REAL_TRAFFIC(实时路况)
    适用场景：估算通勤时长、判断一天行程是否排得下、给出行程单里的交通方式建议。
    注意：返回 duration_min 为分钟、duration_s 为秒，二者已对齐；公交返回 price_cny 为票价（元）。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    src = _base.normalize_coord(from_location)
    dst = _base.normalize_coord(to_location)
    if src is None:
        return _base.fail(f"起点坐标无法解析：{from_location!r}，应形如 26.0824,119.2968")
    if dst is None:
        return _base.fail(f"终点坐标无法解析：{to_location!r}，应形如 26.0824,119.2968")

    resolved = _normalize_mode(mode)
    if resolved is None:
        return _base.fail(f"不支持的出行方式：{mode!r}",
                          supported=list(api.ROUTE_MODES))

    try:
        result = api.direction(resolved, *src, *dst,
                               policy=(policy or "").strip().upper() or None)
    except ValueError as e:
        return _base.fail(str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] direction 失败: %s", e)
        return _base.fail(f"路线规划调用失败: {e}")

    if result is None:
        # 腾讯对「起终点过近/无法通达」会返回无结果，属常见而非故障
        return _base.fail(
            f"未规划出 {resolved} 路线（两地可能过近、跨海或公交未覆盖）",
            from_location=from_location, to_location=to_location, mode=resolved,
        )
    return _base.ok({
        "from": {"lat": src[0], "lng": src[1]},
        "to": {"lat": dst[0], "lng": dst[1]},
        **result,
    })


@tool
def map_distance_matrix_tool(from_locations: str, to_locations: str,
                             mode: str = "driving") -> str:
    """
    批量计算多点到多点的距离与时长矩阵（一次请求算 N×M 段，比逐段调用省配额）。
    from_locations: 起点坐标列表，多个用 ";" 分隔，如 "26.08,119.29;26.05,119.38"，上限 25 个
    to_locations: 终点坐标列表，格式同上，上限 25 个
    mode: 出行方式，仅支持 driving / walking
    适用场景：行程排程前预计算一批候选点的两两通勤时长，用于就近串联与折返检测。
    注意：只返回距离与时长，不含路线详情；需要分段导航指令请用 map_route_tool。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    src_list = _parse_list(from_locations)
    dst_list = _parse_list(to_locations)
    if src_list is None:
        return _base.fail(f"起点列表格式错误：{from_locations!r}，多个坐标用 ; 分隔")
    if dst_list is None:
        return _base.fail(f"终点列表格式错误：{to_locations!r}，多个坐标用 ; 分隔")
    if not src_list or not dst_list:
        return _base.fail("起点与终点列表均不能为空")
    if len(src_list) > 25 or len(dst_list) > 25:
        return _base.fail("单次上起点/终点各 25 个，请分批调用")

    resolved = _normalize_mode(mode)
    if resolved not in api.MATRIX_MODES:
        return _base.fail(f"距离矩阵仅支持 {list(api.MATRIX_MODES)}，收到 {mode!r}")

    try:
        matrix = api.distance_matrix(resolved, src_list, dst_list)
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] distance_matrix 失败: %s", e)
        return _base.fail(f"距离矩阵调用失败: {e}")
    if matrix is None:
        return _base.fail("距离矩阵调用失败（可能是配额或网络问题）")

    return _base.ok({
        "mode": resolved,
        "from_count": len(src_list), "to_count": len(dst_list),
        # matrix[i][j] = 第 i 个起点到第 j 个终点的 {distance_m, duration_s, duration_min}
        "matrix": matrix,
        "total_distance_km": round(
            sum(c["distance_m"] for row in matrix for c in row) / 1000.0, 2),
    })


def _parse_list(raw: str) -> list[tuple[float, float]] | None:
    """解析 ``"lat,lng;lat,lng"`` 形式的坐标列表。"""
    if not raw or not raw.strip():
        return []
    out: list[tuple[float, float]] = []
    for chunk in raw.replace("；", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        coord = _base.normalize_coord(chunk)
        if coord is None:
            return None
        out.append(coord)
    return out


_URI_MODE_ALIASES = {
    "drive": "drive", "driving": "drive", "car": "drive", "驾车": "drive", "开车": "drive",
    "walk": "walk", "walking": "walk", "步行": "walk",
    "bus": "bus", "transit": "bus", "公交": "bus", "公共交通": "bus",
    "bike": "bike", "bicycling": "bike", "骑行": "bike",
}


@tool
def map_navigation_tool(to_location: str, to_name: str = "", from_location: str = "",
                        from_name: str = "", mode: str = "drive", policy: int = 0) -> str:
    """
    生成「在腾讯地图 App 中打开导航」的调起链接（真正的转向导航交给 App 完成）。
    to_location: 目的地坐标 "纬度,经度"
    to_name: 目的地名称，会显示在调起页上，建议填写
    from_location: 起点坐标 "纬度,经度"；留空则以用户当前位置为起点
    from_name: 起点名称
    mode: drive(驾车)/walk(步行)/bus(公交)/bike(骑行)
    policy: 仅驾车有效：0=默认 1=避免拥堵 2=躲避收费 3=不走高速
    适用场景：用户想真的开始导航（需要语音播报与实时偏航重算）——网页无法提供这些，
             必须调起原生地图 App。仅想「看路线/估时间」请用 map_route_tool。
    注意：返回的 url 是否可直接给浏览器，取决于 key_kind 字段：
         为 frontend 表示用的是前端专用 Key，安全；
         为 backend 表示回退用了后端 Key，此时 url 含密钥，**不要外发**。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    dst = _base.normalize_coord(to_location)
    if dst is None:
        return _base.fail(f"目的地坐标无法解析：{to_location!r}，应形如 26.0824,119.2968")

    src = _base.normalize_coord(from_location) if from_location else None
    if from_location and src is None:
        return _base.fail(f"起点坐标无法解析：{from_location!r}")

    resolved = _URI_MODE_ALIASES.get((mode or "drive").strip().lower())
    if resolved is None:
        return _base.fail(f"不支持的出行方式：{mode!r}", supported=list(api.URI_MODES))

    try:
        result = api.navigation_uri(
            to_lat=dst[0], to_lng=dst[1], to_name=to_name,
            from_lat=src[0] if src else None, from_lng=src[1] if src else None,
            from_name=from_name, mode=resolved, policy=int(policy or 0),
        )
    except ValueError as e:
        return _base.fail(str(e))
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] navigation_uri 失败: %s", e)
        return _base.fail(f"导航调起链接生成失败: {e}")

    return _base.ok({
        **result,
        "note": "网页端无腾讯导航 SDK，真导航需调起腾讯地图 App；"
                "此链接在手机浏览器中打开会自动唤起 App",
    })


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

for _t in (map_route_tool, map_distance_matrix_tool, map_navigation_tool):
    tool_registry.register(_t, __file__)
