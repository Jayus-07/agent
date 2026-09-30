"""travel/commerce/extract.py — 商务意图识别与槽位抽取（STOP K6 接线层）

**纯规则零 LLM**（K0 §7 决策 5：LLM 不得进入事实链，意图识别用正则）。
prefilter（orchestration 侧路由判定）与域图 slot_filler（参数抽取）共用
本模块的同一套函数——两处判定必然一致，不会分叉。

意图边界（K0 §1 优先级口径）：
  - 「酒店推荐/住宿推荐/住哪」是**旅游规划信号**（travel_prefilter 已含），
    不属于商务意图——行程语境的住宿偏好由旅游域 lodging 槽承接；
  - 商务意图 = 纯「找/查/订酒店/机票/航班」类诉求，且**无行程信号词**；
  - 「机票订单」类售后诉求由 CS 规则先行命中（路由顺序 CS > travel >
    funnel > commerce），本模块的负向词表再兜一层。
"""
from __future__ import annotations

import re
from datetime import date

from backend.travel.slot_filler import (
    _RE_DATE_ISO,
    extract_party_size,
)

# ── 意图正则（高精度优先）────────────────────────────────────────
# 动词与宾语之间允许出现城市/日期（「找大阪10月3日到5日的酒店」），
# 但跨标点即断（「找朋友。酒店的事」不算）
_RE_HOTEL_INTENT = re.compile(
    r"(?:找|查|搜|看看|订|预订|想住|要住)[^。，,；;！!？?]{0,14}?(?:酒店|宾馆|民宿)")
_RE_HOTEL_PRICE = re.compile(
    r"(?:酒店|宾馆|民宿)(?:的)?(?:价格|报价|多少钱)")
_RE_HOTEL_WORD = re.compile(r"酒店|宾馆|民宿")
_RE_FLIGHT_INTENT = re.compile(
    r"(?:机票|航班|飞机票)(?:的)?(?:价格|多少钱)?"
    r"|(?:查|找|搜|订|预订|买)(?:一下|张|个)?(?:机票|航班|飞机票)")

# 行程信号词（与 travel_prefilter._TRAVEL_PATTERNS 交集判据）：命中任一
# 即视为行程语境，commerce 让路（「行程+订酒店」= 旅游域 lodging 槽）
_TRIP_SIGNAL = re.compile(
    r"行程|攻略|景点|日游|几天|住哪|住宿推荐|酒店推荐|路线|怎么玩")

# 售后负向词（CS 域诉求， commerce 不接）
_CS_SIGNAL = re.compile(r"订单|退款|退票|改签|报销|发票|行程单")

_RE_ROOMS = re.compile(r"(\d{1,2})\s*间(?:房|)")
_RE_CHILDREN = re.compile(r"(\d{1,2})\s*(?:个)?(?:儿童|小孩)")

# 日期区间（复用 slot_filler 同一正则——单一事实源，防两处口径分叉）
_RE_DATE_RANGE = re.compile(
    r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*(?:到|至|[-~—]|住到)\s*"
    r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日号]?")


def detect_commerce_intent(message: str) -> str | None:
    """商务意图判定（纯函数）：hotel | flight | None。

    None = 非商务（含行程语境让路、CS 售后让路、无信号）。
    hotel 判定三通道：动词+宾语（可隔城市/日期）/ 酒店询价 /
    酒店词 + 已知城市在场（「大阪的酒店」）。
    """
    message = (message or "").strip()
    if not message:
        return None
    if _CS_SIGNAL.search(message):
        return None
    hotel = bool(
        _RE_HOTEL_INTENT.search(message)
        or _RE_HOTEL_PRICE.search(message)
        or (_RE_HOTEL_WORD.search(message) and _city_present(message))
    )
    flight = bool(_RE_FLIGHT_INTENT.search(message))
    if not hotel and not flight:
        return None
    if _TRIP_SIGNAL.search(message):
        return None  # 行程语境：让路旅游域（lodging 槽承接住宿偏好）
    # 同时命中（「找酒店和机票」）按 hotel 优先返回复合由图内双查
    if hotel:
        return "hotel"
    return "flight"


def _city_present(message: str) -> bool:
    from backend.travel.commerce.cities import find_city

    return find_city(message) is not None


def extract_hotel_params(message: str, today: date | None = None) -> dict:
    """酒店槽位抽取（确定性；缺失槽位不出现在结果里——读方用 .get()）。"""
    from backend.travel.commerce.cities import find_city

    today = today or date.today()
    params: dict = {}
    city = find_city(message)
    if city:
        params["city"] = city

    dates = _extract_date_pair(message, today)
    if dates:
        params["check_in"], params["check_out"] = dates

    adults = extract_party_size(message)
    if adults:
        params["adults"] = adults
    m = _RE_ROOMS.search(message)
    if m:
        rooms = int(m.group(1))
        if rooms >= 1:
            params["rooms"] = rooms
    m = _RE_CHILDREN.search(message)
    if m:
        children = int(m.group(1))
        if children >= 0:
            params["children"] = children
    return params


# 航线方向词（A [从A] 到/飞/去 B）——与已知城市表配合解析，不做裸汉字
# 捕获（「10月3日东京」的「日」会被裸正则吞进城市名，实测踩过）
_ROUTE_DIRECTION = re.compile(r"(?:到|至|→|飞|去)")


def extract_flight_params(message: str, today: date | None = None) -> dict:
    """机票槽位抽取（one-way；返回日期本轮契约不支持——出现也不取）。

    基于 known city 表 + 方向词：按出现顺序取前两个城市，方向词把两者
    串起来才认定航线（「东京到大阪」）；裸城市无方向词 → 缺槽追问。
    """
    from backend.travel.commerce.cities import find_cities

    today = today or date.today()
    params: dict = {}
    cities = find_cities(message)
    if len(cities) >= 2:
        first, second = cities[0], cities[1]
        m = re.search(
            re.escape(first) + r"[^。，,；;！!？?]{0,4}?" + _ROUTE_DIRECTION.pattern
            + re.escape(second), message)
        if m:
            params["origin"] = first
            params["destination"] = second
    single = _extract_single_date(message, today)
    if single:
        params["departure_date"] = single
    return params


def _extract_date_pair(message: str, today: date) -> tuple[date, date] | None:
    """「10月3日到5日」→ (check_in, check_out)；年份按「不早于今天」补齐。

    跨年区间（12月30到1月2日）同年内 end<start → 放弃（与 slot_filler
    同一口径：不猜）。
    """
    m = _RE_DATE_RANGE.search(message)
    if not m:
        return None
    mo1, d1, mo2, d2 = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
    mo2 = int(mo2) if mo2 else mo1
    d2 = int(d2)
    for year in (today.year, today.year + 1):
        try:
            start = date(year, mo1, d1)
            end = date(year, mo2, d2)
        except ValueError:
            return None
        if start >= today:
            if end <= start:
                return None
            return start, end
    return None


def _extract_single_date(message: str, today: date) -> date | None:
    """单日期（机票出发日）：ISO 优先，其次「N月N日」；复用 slot_filler
    的 ISO 正则与「不早于今天」补年规则（与旅游域同口径）。"""
    m = _RE_DATE_ISO.search(message)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", message)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if candidate >= today:
                return candidate
    return None


def extract_single_date(message: str, today: date | None = None) -> date | None:
    """单日期抽取（公开入口，Phase 5 / D2 跨轮续填用）。

    **不是新实现**——薄包装模块内 ``_extract_single_date``（与机票出发日
    抽取同一份正则与补年规则），避免续填路径另写一套日期解析而与主抽取
    分叉（本项目「同一口径单一事实源」纪律）。

    场景：用户被问「入住日期」后答「10月3日」（只有单日期、没有区间词），
    ``_extract_date_pair`` 不匹配 → check_in 补不上 → 系统再问一遍。
    区间表达（「10月3日到5日」）仍由 ``_extract_date_pair`` 在
    ``extract_hotel_params`` 内处理；本函数只兜单日期那一档。
    """
    return _extract_single_date(message or "", today or date.today())


# ── 跨轮续填的槽位契约（Phase 5 / D2 两跳断修复）─────────────────
# 必填槽位与展示名在此单点定义：预订（booking）与比价（commerce）两个
# 子图的追问文案、缺失判定、挂起回写三处共用，避免「追问说缺 A、挂起记
# B」这类分叉。两侧的「续填」都走下面的 merge_slot_values。
HOTEL_REQUIRED_SLOTS: tuple[str, ...] = ("city", "check_in", "check_out")
FLIGHT_REQUIRED_SLOTS: tuple[str, ...] = ("origin", "destination",
                                          "departure_date")
REQUIRED_SLOTS_BY_KIND: dict[str, tuple[str, ...]] = {
    "hotel": HOTEL_REQUIRED_SLOTS,
    "flight": FLIGHT_REQUIRED_SLOTS,
}
SLOT_LABELS: dict[str, str] = {
    "city": "城市", "check_in": "入住日期", "check_out": "退房日期",
    "origin": "出发城市", "destination": "目的地城市",
    "departure_date": "出发日期",
}


def iso_value(value):
    """date → ISO 字符串；其余原样。

    挂起落 ConversationContext（Redis JSON value），date 对象不可序列化。
    """
    return value.isoformat() if hasattr(value, "isoformat") else value


def merge_slot_values(kind: str, message: str,
                      base: dict | None = None) -> tuple[dict, list[str]]:
    """本轮抽取并入 base，返回 ``(collected, missing)``——跨轮续填的唯一合并点。

    唯一抽取来源仍是本模块的 ``extract_hotel_params`` /
    ``extract_flight_params``（与 prefilter 同源）；``base`` 是上轮挂起里
    已收到的槽位。collected 的值统一 ISO 化（可直接落 JSON、可直接并入
    子图 request 的 ISO 字段）。

    额外兜一层单日期：酒店缺 check_in 时用 ``extract_single_date`` 补——
    「10月3日」这类只答一个日期的形态，区间正则覆盖不到。
    """
    required = REQUIRED_SLOTS_BY_KIND.get(kind)
    if not required:
        return {}, []
    collected = {k: iso_value(v) for k, v in dict(base or {}).items()}
    fresh = (extract_hotel_params(message) if kind == "hotel"
             else extract_flight_params(message))
    collected.update({k: iso_value(v) for k, v in fresh.items()})
    if kind == "hotel" and not collected.get("check_in"):
        single = extract_single_date(message)
        if single:
            collected["check_in"] = iso_value(single)
    missing = [k for k in required if not collected.get(k)]
    return collected, missing


def missing_slots_clarification(missing: list[str],
                                prefix: str = "预订还需要：") -> str:
    """缺槽追问文案（预订侧用；比价侧走 graph_node_render.build_clarification）。"""
    return prefix + "、".join(SLOT_LABELS.get(m, m) for m in missing) + "。"

