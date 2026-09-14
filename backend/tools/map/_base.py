"""tools/map/_base.py — 地图工具层公共约定

工具返回值统一为 JSON 字符串（与 backend/tools 下其他工具一致），
并遵守一条契约：**失败必须显式出现在返回值里**，不能安静地返回空。

原因：LLM 拿到 ``{"pois": []}`` 会理解成「这里没有地点」并据此改写行程，
而 ``{"error": "配额用尽"}`` 才会让它换策略（改用地理编码或直接告知用户）。
把「查不到」与「查不了」混为一谈，是行程幻觉的常见起点。
"""
from __future__ import annotations

import json
from typing import Any


def dumps(payload: Any) -> str:
    """序列化为紧凑 JSON（中文不转义）。"""
    return json.dumps(payload, ensure_ascii=False)


def ok(payload: dict) -> str:
    """成功返回。"""
    return dumps(payload)


def fail(message: str, **extra: Any) -> str:
    """失败返回：``error`` 字段是给 LLM 看的可执行提示，不是堆栈。"""
    return dumps({"error": message, **extra})


def not_configured() -> str:
    """未配置 Key 的统一提示。"""
    return fail(
        "腾讯位置服务未配置，地图能力不可用",
        hint="请在项目根目录 .env 中设置 TENCENT_LBS_KEY 后重启服务",
    )


def normalize_coord(value: str) -> tuple[float, float] | None:
    """把 LLM 可能给出的各种坐标写法归一为 (lat, lng)。

    模型常见写法：``"26.08,119.29"`` / ``"26.08 119.29"`` / ``"lat=26.08,lng=119.29"``。
    经纬度顺序照 :func:`infra.lbs.geo.parse_lat_lng` 的规则自动纠正。
    """
    from backend.infra.lbs.geo import parse_lat_lng

    if not value:
        return None
    cleaned = (value.replace("lat=", "").replace("lng=", "")
               .replace("latitude=", "").replace("longitude=", "")
               .replace("，", ",").replace(" ", ","))
    parts = [p for p in cleaned.split(",") if p.strip()]
    if len(parts) != 2:
        return None
    return parse_lat_lng(f"{parts[0]},{parts[1]}")
