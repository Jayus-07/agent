"""tools/map/static_map.py — 静态地图图片工具

**为什么返回的是代理地址而不是腾讯的图片直链**：腾讯静态图接口要求 Key
出现在 URL 查询串里。若把该直链回给 LLM，Key 就会进入对话上下文、最终
回答、日志与浏览器历史 —— 等同于泄露。因此这里只返回本服务的代理地址
``/api/map/static-map?...``，真实 Key 由后端在转发时注入。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.shared.logger import logger
from backend.tools.map import _base

_VALID_MAPTYPES = ("roadmap", "satellite", "hybrid")
_PROXY_PATH = "/api/map/static-map"


@tool
def map_static_map_tool(center: str, zoom: int = 14, size: str = "600*400",
                        maptype: str = "roadmap", markers: str = "") -> str:
    """
    生成一张静态地图图片的地址（用于把行程路线/地点直观展示给用户）。
    center: 地图中心坐标 "纬度,经度"，如 "26.0824,119.2968"；若已提供 markers 可留空
    zoom: 缩放级别，取值 4~18，数值越大越细（默认 14）
    size: 图片尺寸 "宽*高"，宽 50~1680、高 50~1200，如 "600*400"
    maptype: 底图类型，roadmap(路网)/satellite(卫星)/hybrid(卫星叠加路网)
    markers: 标注点列表，用 ";" 分隔，每项为 "纬度,经度" 或 "纬度,经度,标注字符"，
             如 "26.0824,119.2968,A;26.049,119.389,B"
    适用场景：行程规划完成后，生成一张带标注点的概览图嵌入到回复中。
    注意：返回的 image_url 是本服务的代理地址（不含密钥），可直接用于 <img src>。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    center_coord = _base.normalize_coord(center) if center else None
    if center and center_coord is None:
        return _base.fail(f"center 坐标无法解析：{center!r}，应形如 26.0824,119.2968")

    marker_items = _parse_markers(markers)
    if marker_items is None:
        return _base.fail(
            f"markers 格式错误：{markers!r}，应为 '纬度,经度[,标注字符]' 用 ; 分隔"
        )
    # 腾讯要求：有 markers 时 center 可省；两者都缺则地图无法定位
    if center_coord is None and not marker_items:
        return _base.fail("center 与 markers 不能同时为空，否则地图无法确定显示范围")

    maptype = (maptype or "roadmap").strip().lower()
    if maptype not in _VALID_MAPTYPES:
        return _base.fail(f"maptype 仅支持 {list(_VALID_MAPTYPES)}")

    zoom = max(4, min(int(zoom or 14), 18))
    width, height = _parse_size(size)
    if width is None:
        return _base.fail(f"size 格式错误：{size!r}，应为 '宽*高'，如 600*400")

    query: list[str] = [f"zoom={zoom}", f"size={width}*{height}", f"maptype={maptype}"]
    if center_coord is not None:
        query.insert(0, f"center={center_coord[0]:.6f},{center_coord[1]:.6f}")
    if marker_items:
        encoded = ";".join(
            f"{lat:.6f},{lng:.6f}" + (f",{label}" if label else "")
            for lat, lng, label in marker_items
        )
        query.append(f"markers={encoded}")

    url = f"{_PROXY_PATH}?" + "&".join(query)
    logger.info("[MapTool] 静态图代理地址已生成：%d 个标注点", len(marker_items))
    return _base.ok({
        "image_url": url,
        "note": "该地址由后端代理转发到腾讯静态图服务，不含密钥，可直接用于图片标签",
        "marker_count": len(marker_items),
        "size": f"{width}*{height}",
        "zoom": zoom,
        "maptype": maptype,
    })


def _parse_markers(raw: str) -> list[tuple[float, float, str]] | None:
    """解析 ``"lat,lng,label;lat,lng"`` 形式的标注点列表。"""
    if not raw or not raw.strip():
        return []
    out: list[tuple[float, float, str]] = []
    for chunk in raw.replace("；", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) not in (2, 3):
            return None
        coord = _base.normalize_coord(f"{parts[0]},{parts[1]}")
        if coord is None:
            return None
        label = parts[2] if len(parts) == 3 else ""
        # 腾讯限制标注字符为 0-9 / A-Z / 单个中文字
        if label and not (label.isalnum() or len(label) == 1):
            label = label[0]
        out.append((coord[0], coord[1], label))
    if len(out) > 50:
        return None  # 腾讯上限 50 个标注
    return out


def _parse_size(size: str) -> tuple[int | None, int | None]:
    """解析 ``"宽*高"``，越界时收敛到腾讯允许范围。"""
    try:
        w, h = (size or "600*400").replace("x", "*").replace("X", "*").split("*")
        width = max(50, min(int(float(w)), 1680))
        height = max(50, min(int(float(h)), 1200))
        return width, height
    except (ValueError, AttributeError):
        return None, None


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(map_static_map_tool, __file__)
