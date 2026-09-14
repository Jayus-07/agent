"""infra.lbs.geo — 坐标解析与坐标系换算（纯函数，可单测）

**为什么需要本地换算而不是只调接口**：排程与折返检测每轮要比较几十对坐标，
逐个走 ``/ws/coord/v1/translate`` 会把配额烧在网络往返上；换算本身是确定性
数学，放在本地既免费又快。接口版换算保留在 ``api.coord_translate``，
用于大批量（≥20 点）或需要与腾讯结果逐位对齐的场景。

国内坐标三兄弟（务必分清，混用会产生 50~700 米偏移）：

===========  ==========================  ============================
坐标系         来源                          典型出处
===========  ==========================  ============================
WGS-84       GPS 原始观测值              手机 GPS 芯片、Google 海外、OSM
GCJ-02       国测局加密偏移（火星坐标）    **腾讯位置服务全线**、高德、腾讯地图
BD-09        百度在 GCJ-02 上二次偏移     百度地图
===========  ==========================  ============================

腾讯位置服务的所有输入输出都是 **GCJ-02**。拿到 WGS-84 必须先转，
拿到百度坐标必须先转；否则行程单上的点在真实地图上会落错位置。
"""
from __future__ import annotations

import math

# 克拉索夫斯基椭球参数（GCJ-02 偏移算法所用）
_A = 6378245.0
_EE = 0.00669342162296594323
_X_PI = math.pi * 3000.0 / 180.0

# 合法的 GCJ-02 结果范围（腾讯静态图接口文档规定）
LAT_RANGE = (3.5, 53.0)
LNG_RANGE = (73.5, 135.0)


def _out_of_china(lat: float, lng: float) -> bool:
    """粗略判断是否在境外。

    境外不做偏移 —— GCJ-02 是国境内加密算法，对境外坐标强行套用会产生
    荒谬结果（这也是各家 SDK 的通行做法）。
    """
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
           + 0.2 * math.sqrt(abs(x)))
    ret += (20.0 * math.sin(6.0 * x * math.pi)
            + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi)
            + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi)
            + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
           + 0.1 * math.sqrt(abs(x)))
    ret += (20.0 * math.sin(6.0 * x * math.pi)
            + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi)
            + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi)
            + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """GPS 原始坐标 → 火星坐标（喂给腾讯接口前必须做的一步）。"""
    if _out_of_china(lat, lng):
        return lat, lng
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + dlat, lng + dlng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """火星坐标 → GPS 原始坐标。

    没有解析解，用「正算一次求偏移量、再反向扣掉」的近似法：
    国内范围内误差约 1~2 米，远小于 GPS 本身的定位误差，够用。
    """
    if _out_of_china(lat, lng):
        return lat, lng
    g_lat, g_lng = wgs84_to_gcj02(lat, lng)
    return lat * 2 - g_lat, lng * 2 - g_lng


def gcj02_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    """火星坐标 → 百度坐标。"""
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * _X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * _X_PI)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


def bd09_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """百度坐标 → 火星坐标。"""
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.sin(theta), z * math.cos(theta)


# 面向外部调用方的统一入口：腾讯接口的 type 参数语义
#   1 = GPS(WGS-84) → GCJ-02      2 = 输入 GCJ-02 → 输出 WGS-84
#   3 = 百度 BD-09 → GCJ-02        4 = 输入 GCJ-02 → 输出 BD-09
COORD_TYPE_TO_GCJ02 = {
    1: ("wgs84", "gcj02"),
    2: ("gcj02", "wgs84"),
    3: ("bd09", "gcj02"),
    4: ("gcj02", "bd09"),
}


def to_gcj02(lat: float, lng: float, src: str = "wgs84") -> tuple[float, float]:
    """把任意来源坐标统一到腾讯使用的 GCJ-02。

    Args:
        src: ``wgs84`` / ``gcj02`` / ``bd09``（大小写与连字符不敏感）
    """
    key = (src or "wgs84").strip().lower().replace("-", "").replace("_", "")
    if key in ("gcj02", "gcj2", "mars", "tencent"):
        return lat, lng
    if key in ("wgs84", "wgs", "gps", "84"):
        return wgs84_to_gcj02(lat, lng)
    if key in ("bd09", "bd9", "baidu"):
        return bd09_to_gcj02(lat, lng)
    raise ValueError(f"未知坐标系: {src!r}（支持 wgs84 / gcj02 / bd09）")


def is_valid_lat_lng(lat: float, lng: float) -> bool:
    """坐标是否落在腾讯接口的可服务范围内（lat, lng）。"""
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]


def parse_lat_lng(value: str) -> tuple[float, float] | None:
    """解析 ``"26.08,119.29"`` 形式的坐标串，纬度在前。

    腾讯全线接口都是「纬度,经度」，但国内开发者习惯「经度,纬度」，
    这个顺序错误不会报错、只会把点画到几百公里外 —— 因此入口做一次
    范围自愈：若按 lat,lng 解析后越界而按 lng,lat 解析合法，则交换并告警。
    """
    if not value:
        return None
    parts = value.replace("，", ",").split(",")
    if len(parts) != 2:
        return None
    try:
        first, second = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None

    if is_valid_lat_lng(first, second):
        return first, second
    if is_valid_lat_lng(second, first):
        # 输入疑似是「经度,纬度」，交换后合法
        return second, first
    return None


def format_lat_lng(lat: float, lng: float) -> str:
    """格式化为腾讯接口要求的 ``lat,lng`` 串（6 位小数 ≈ 0.1 米精度）。"""
    return f"{lat:.6f},{lng:.6f}"
