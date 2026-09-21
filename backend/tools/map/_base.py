"""tools/map/_base.py — 地图工具层公共约定

工具返回值统一为 JSON 字符串（与 backend/tools 下其他工具一致），
并遵守两条契约：

1. **统一封套**（shared/tool_envelope.py）：成功 ``{"status": "success",
   "data": {...}}``，失败 ``{"status": "failed", "error": ...}``。
2. **失败必须显式出现在返回值里**，不能安静地返回空。

原因：LLM 拿到 ``{"pois": []}`` 会理解成「这里没有地点」并据此改写行程，
而 ``{"status": "failed", "error": "配额用尽"}`` 才会让它换策略（改用地理
编码或直接告知用户）。把「查不到」与「查不了」混为一谈，是行程幻觉的常见起点。
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from backend.shared.tool_envelope import tool_success_result, tool_error_result

# 「lat=/lng:/longitude=」等 key 前缀（大小写不敏感；交替顺序无关，
# 正则会自动回溯匹配最长的 latitude/longitude）
_KEY_PREFIX_RE = re.compile(
    r"\b(lat|latitude|lng|lon|long|longitude)\s*[:=]\s*", re.IGNORECASE,
)
# 半球后缀「26.08N」：字母紧跟数字之后，且后面不再接字母数字（避开 1e5 的 e）
_HEMI_SUFFIX_RE = re.compile(r"(?<=[0-9])\s*[nsew](?![a-z0-9])", re.IGNORECASE)
# 半球前缀「N26.08」
_HEMI_PREFIX_RE = re.compile(r"(?<![a-z0-9])[nsew](?=[0-9])", re.IGNORECASE)
# 分隔符：中英文逗号 / 分号 / 冒号 / 空白
_SEPARATORS_RE = re.compile(r"[,，;；:\s]+")


def ok(payload: Mapping[str, Any]) -> str:
    """成功返回：业务数据嵌套在 ``data`` 下（统一封套）。"""
    return tool_success_result(payload)


def fail(message: str, **extra: Any) -> str:
    """失败返回：``error`` 字段是给 LLM 看的可执行提示，不是堆栈。"""
    return tool_error_result(message, **extra)


def not_configured() -> str:
    """未配置 Key 的统一提示（key 名唯一出处是 config/map.py）。

    全部地图工具共用且当前只有腾讯一家 provider，故收在本模块、
    保持零参数；出现第二家 provider 时再下沉到各 provider 模块。
    """
    return fail(
        "腾讯位置服务未配置，地图能力不可用",
        hint="请在项目根目录 .env 中设置 TENCENT_LBS_KEY 后重启服务",
    )


def normalize_coord(value: str) -> tuple[float, float] | None:
    """把 LLM 可能给出的各种坐标写法归一为 (lat, lng)。

    兼容写法（大小写不敏感）::

        "26.08,119.29"        "26.08 119.29"      "26.08;119.29"
        "lat=26.08,lng=119.29"  "Lat=26.08, Lng=119.29"
        "latitude=26.08,longitude=119.29"  "lat:26.08 lng:119.29"
        "26.08N 119.29E"      "N26.08 E119.29"    "26.08° 119.29°"

    经纬度顺序照 :func:`infra.lbs.geo.parse_lat_lng` 的规则自动纠正。
    """
    from backend.infra.lbs.geo import parse_lat_lng

    if not value:
        return None
    text = _KEY_PREFIX_RE.sub("", value)
    text = text.replace("°", "")
    text = _HEMI_SUFFIX_RE.sub("", text)
    text = _HEMI_PREFIX_RE.sub("", text)
    parts = [p for p in _SEPARATORS_RE.split(text) if p]
    if len(parts) != 2:
        return None
    return parse_lat_lng(f"{parts[0]},{parts[1]}")
