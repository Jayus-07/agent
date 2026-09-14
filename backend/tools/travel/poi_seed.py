"""tools/travel/poi_seed.py — POI 本地种子数据集（P0）

**数据可信度声明（重要）**
本文件中的坐标、营业时段、闭馆星期、票价、建议停留时长均为**示例值**，
用于跑通「槽位 → 骨架 → 校验 → 行程单」全链路与单元测试，
**不可作为真实出行依据**。

因此：
  - 每条 Poi 的 source 固定为 "seed:local"，reporter 会在行程单里如实标注
  - 接入真实数据源（地图 / 票务 MCP）后，应替换本模块的数据供给，
    并把 source 改为具体 provider 标识；Poi 契约本身不变
  - 种子数据故意覆盖了几类边界：周一闭馆、收费、超长停留、全天开放，
    以便校验器的时间轴有真实可触发的用例

城市键为中文城市名，TravelBrief.destination 需能归一到这里（见 poi.py 的
resolve_city）。
"""
from __future__ import annotations

from backend.travel.models.poi import (
    CATEGORY_MEAL,
    CATEGORY_NIGHT,
    CATEGORY_PARK,
    CATEGORY_SHOPPING,
    CATEGORY_VISIT,
    Poi,
)

SOURCE_SEED = "seed:local"

# 紧凑行格式（见 _build 的注释）—— 36 条用 dict 写会产生大量重复键名噪声
# (poi_id, name, category, lat, lng, open, close, closed_weekdays, minutes, ticket, tags, rating)
_RAW: dict[str, list[tuple]] = {
    "福州": [
        ("fz_sanfang", "三坊七巷", CATEGORY_VISIT, 26.0865, 119.2962, "08:30", "22:00", [], 150, 0, ["人文", "摄影", "购物"], 4.8),
        ("fz_gushan", "鼓山", CATEGORY_VISIT, 26.0489, 119.3843, "07:30", "17:30", [], 210, 0, ["自然", "摄影"], 4.6),
        ("fz_xihu", "福州西湖公园", CATEGORY_PARK, 26.0921, 119.2794, "05:30", "22:30", [], 90, 0, ["自然", "亲子"], 4.5),
        ("fz_museum", "福建博物院", CATEGORY_VISIT, 26.0887, 119.2782, "09:00", "17:00", [0], 120, 0, ["人文", "亲子"], 4.7),
        ("fz_yantai", "烟台山", CATEGORY_VISIT, 26.0449, 119.3132, "09:00", "21:30", [], 120, 0, ["人文", "摄影", "美食"], 4.6),
        ("fz_shangxiahang", "上下杭历史文化街区", CATEGORY_VISIT, 26.0551, 119.2968, "09:00", "22:00", [], 120, 0, ["人文", "美食", "夜生活"], 4.5),
        ("fz_guling", "鼓岭", CATEGORY_VISIT, 26.0760, 119.4060, "08:00", "18:00", [], 180, 0, ["自然", "摄影"], 4.4),
        ("fz_forest", "福州国家森林公园", CATEGORY_PARK, 26.1090, 119.2800, "08:00", "17:30", [], 150, 0, ["自然", "亲子"], 4.4),
        ("fz_yushan", "于山风景区", CATEGORY_VISIT, 26.0847, 119.3011, "06:00", "21:00", [], 90, 0, ["自然", "人文"], 4.3),
        ("fz_wushan", "乌山历史风貌区", CATEGORY_VISIT, 26.0850, 119.2910, "06:00", "21:00", [], 75, 0, ["人文", "自然"], 4.3),
        ("fz_daming", "达明美食街", CATEGORY_MEAL, 26.0873, 119.2955, "11:00", "23:00", [], 90, 60, ["美食", "夜生活"], 4.5),
    ],
    "厦门": [
        ("xm_gulangyu", "鼓浪屿", CATEGORY_VISIT, 24.4471, 118.0672, "08:00", "18:00", [], 300, 100, ["人文", "摄影", "自然"], 4.8),
        ("xm_nanputuo", "南普陀寺", CATEGORY_VISIT, 24.4405, 118.0924, "05:30", "18:00", [], 90, 0, ["人文", "自然"], 4.7),
        ("xm_xmu", "厦门大学", CATEGORY_VISIT, 24.4358, 118.0968, "08:00", "17:30", [0], 120, 0, ["人文", "摄影"], 4.6),
        ("xm_hulishan", "胡里山炮台", CATEGORY_VISIT, 24.4318, 118.1172, "07:30", "17:30", [], 90, 25, ["人文", "亲子"], 4.4),
        ("xm_huandao", "环岛路", CATEGORY_PARK, 24.4302, 118.1402, "00:00", "23:59", [], 120, 0, ["自然", "摄影", "亲子"], 4.6),
        ("xm_zengcuoan", "曾厝垵", CATEGORY_MEAL, 24.4238, 118.1283, "10:00", "23:00", [], 120, 0, ["美食", "夜生活", "摄影"], 4.3),
        ("xm_zhongshanlu", "中山路步行街", CATEGORY_SHOPPING, 24.4574, 118.0821, "09:00", "22:30", [], 120, 0, ["购物", "美食", "夜生活"], 4.4),
        ("xm_jimei", "集美学村", CATEGORY_VISIT, 24.5738, 118.1018, "08:30", "17:30", [], 150, 0, ["人文", "摄影"], 4.5),
        ("xm_botanical", "厦门园林植物园", CATEGORY_PARK, 24.4530, 118.1050, "07:00", "18:00", [], 150, 30, ["自然", "亲子", "摄影"], 4.6),
        ("xm_shapowei", "沙坡尾艺术西区", CATEGORY_VISIT, 24.4390, 118.0870, "10:00", "22:00", [], 90, 0, ["人文", "摄影", "美食"], 4.3),
    ],
    "杭州": [
        ("hz_xihu", "西湖", CATEGORY_PARK, 30.2450, 120.1490, "00:00", "23:59", [], 240, 0, ["自然", "摄影", "人文"], 4.9),
        ("hz_lingyin", "灵隐寺", CATEGORY_VISIT, 30.2412, 120.1015, "07:00", "18:00", [], 150, 75, ["人文", "自然"], 4.7),
        ("hz_leifeng", "雷峰塔", CATEGORY_NIGHT, 30.2318, 120.1498, "08:00", "20:00", [], 90, 40, ["人文", "摄影", "夜生活"], 4.5),
        ("hz_xixi", "西溪国家湿地公园", CATEGORY_PARK, 30.2681, 120.0703, "08:00", "17:30", [], 210, 80, ["自然", "亲子", "摄影"], 4.6),
        ("hz_hefang", "河坊街", CATEGORY_MEAL, 30.2402, 120.1707, "09:00", "22:00", [], 120, 0, ["美食", "购物", "人文"], 4.4),
        ("hz_zhebo", "浙江省博物馆", CATEGORY_VISIT, 30.2528, 120.1621, "09:00", "17:00", [0], 120, 0, ["人文", "亲子"], 4.6),
        ("hz_longjing", "龙井村", CATEGORY_VISIT, 30.2183, 120.1052, "08:30", "17:30", [], 120, 0, ["自然", "人文"], 4.4),
        ("hz_songcheng", "宋城", CATEGORY_VISIT, 30.1908, 120.0901, "10:00", "21:00", [], 240, 320, ["人文", "亲子", "夜生活"], 4.5),
        ("hz_qiantang", "钱江新城城市阳台", CATEGORY_PARK, 30.2470, 120.2150, "00:00", "23:59", [], 90, 0, ["自然", "摄影", "夜生活"], 4.3),
        ("hz_liuhe", "六和塔", CATEGORY_VISIT, 30.2050, 120.1300, "08:00", "17:30", [], 75, 20, ["人文", "自然"], 4.4),
    ],
}

CITY_ALIASES: dict[str, str] = {
    "福州市": "福州", "榕城": "福州", "fuzhou": "福州",
    "厦门市": "厦门", "鹭岛": "厦门", "xiamen": "厦门",
    "杭州市": "杭州", "hangzhou": "杭州", "余杭": "杭州",
}


def _build(city: str, rows: list[tuple]) -> list[Poi]:
    """紧凑行 → Poi。行格式见 _RAW 上方的注释。"""
    out: list[Poi] = []
    for (poi_id, name, category, lat, lng, open_t, close_t,
         closed, minutes, ticket, tags, rating) in rows:
        out.append(Poi(
            poi_id=poi_id, name=name, city=city, category=category,
            lat=lat, lng=lng, open_time=open_t, close_time=close_t,
            closed_weekdays=list(closed), suggested_minutes=minutes,
            ticket_cny=float(ticket), tags=list(tags), rating=float(rating),
            source=SOURCE_SEED,
        ))
    return out


_CATALOG: dict[str, list[Poi]] = {c: _build(c, rows) for c, rows in _RAW.items()}


def all_cities() -> list[str]:
    """已知城市键（稳定顺序，供 slot_filler 提示用户可选范围）。"""
    return list(_CATALOG.keys())


def load_city(city_key: str) -> list[Poi]:
    """按城市键取 POI 列表的副本。

    返回副本而非内部对象：Poi 会被调用方打 required 标记，共享实例会串味。
    """
    return [p.model_copy() for p in _CATALOG.get(city_key, [])]


def all_poi_names() -> list[str]:
    """全部已知 POI 名（跨城市）。

    slot_filler 用它把用户说的地名识别成「必去地点」——用真实名录做匹配，
    比用「想去XX」之类的正则盲抽准得多，也不会把「福州」这类城市名
    误当成景点。
    """
    names: list[str] = []
    for pois in _CATALOG.values():
        for p in pois:
            if p.name not in names:
                names.append(p.name)
    return sorted(names, key=len, reverse=True)
