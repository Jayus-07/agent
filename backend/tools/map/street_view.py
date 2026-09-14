"""tools/map/street_view.py — 街景工具

**当前状态（诚实标注）**：本 Key 尚未开通街景服务，接口返回
``113 此功能未被授权``。因此：

  - 端点路径已实测确认（返回 113 而非 404，说明路径是对的）
  - 但**响应字段的解析未经实测**，基于官方文档实现，采用宽松提取
  - 工具会把「去哪申请」的指引原样返回给调用方，而不是含糊地说「查不到」

街景服务是**申请制**（腾讯仅对企业开发者开放，需按官方配额申请模板发邮件
至 mapapi@vip.qq.com 并抄送 mapbd@tencent.com，约 3 个工作日审批），
不是控制台里勾一下就能开的开关。

与静态图工具一致：**返回后端代理地址而不是腾讯图片直链**，避免 Key 泄露。
"""
from __future__ import annotations

from langchain_core.tools import tool

from backend.config import map as MAP
from backend.infra.http.tencent_lbs import TencentLbsError
from backend.infra.lbs import api
from backend.shared.logger import logger
from backend.tools.map import _base

_PROXY_PATH = "/api/map/street-view"


@tool
def map_street_view_tool(location: str, radius_m: int = 50,
                         heading: int = 0, pitch: int = 0) -> str:
    """
    获取指定坐标附近的街景全景图，用于让用户「实地看一眼」某个地点。
    location: 坐标 "纬度,经度"，如 "26.0824,119.2968"
    radius_m: 搜索半径（米），默认 50，越大越容易命中但可能偏离目标点
    heading: 水平朝向角度 0~360，0 为正北，90 为正东
    pitch: 垂直俯仰 -90~90，负数向下看（默认 0 平视）
    适用场景：用户想确认某个路口/店铺/景点的实际样貌；或行程中需要「到达指引」。
    注意：街景覆盖有限，乡村与新建道路常常没有数据；返回 image_url 为本服务代理地址（不含密钥）。
    """
    if not MAP.is_configured():
        return _base.not_configured()

    coord = _base.normalize_coord(location)
    if coord is None:
        return _base.fail(f"坐标无法解析：{location!r}，应形如 26.0824,119.2968")

    try:
        pano = api.street_view_pano(coord[0], coord[1], radius=radius_m)
    except TencentLbsError as e:
        # 113 的异常消息里带官方申请路径，必须原样透出 —— 这是用户要采取的行动
        logger.info("[MapTool] 街景不可用: %s", e)
        return _base.fail(
            str(e),
            service="street_view",
            actionable="街景为申请制服务，未开通前调用一律返回 113",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[MapTool] street_view 失败: %s", e)
        return _base.fail(f"街景查询调用失败: {e}")

    if not pano.get("pano"):
        return _base.fail(
            f"坐标 {location} 附近 {radius_m} 米内没有街景数据",
            hint="街景覆盖有限，可尝试放大半径或换一个坐标",
        )

    query = (f"pano={pano['pano']}&heading={max(0, min(int(heading), 360))}"
             f"&pitch={max(-90, min(int(pitch), 90))}")
    return _base.ok({
        "pano": pano["pano"],
        "lat": pano["lat"], "lng": pano["lng"],
        "description": pano.get("description", ""),
        "image_url": f"{_PROXY_PATH}?{query}",
        "note": "该地址由后端代理转发到腾讯街景服务，不含密钥，可直接用于图片标签",
    })


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry  # noqa: E402

tool_registry.register(map_street_view_tool, __file__)
