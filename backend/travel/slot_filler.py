"""travel/slot_filler.py — 槽位抽取与追问

职责：把一句自然语言需求变成 TravelBrief，并在**必填槽位缺失时转为追问**。

P0 纯规则实现（零 LLM），两个理由：
  1. 「日期是哪天、几个人、几天」属于**事实抽取**而非理解。抽取错了整份
     行程作废，把这件事的正确性押在模型上，代价与收益不成比例。
  2. 追问话术与缺失槽位必须严格一一对应，规则实现可以单测穷举；
     LLM 实现只能靠抽查。

抽取边界（明确不猜）：
  - 没有货币单位的数字不当预算（"3天"不是 3000 元）
  - 城市名与景点名分开处理：城市进 destination，只有真实 POI 名录里的
    名字才进 must_go，其余通过「必去/想去」触发词捕获，未匹配到数据时
    会在行程单里如实提示（不伪造条目）
"""
from __future__ import annotations

import re
from datetime import date

from backend.config import travel as T
from backend.shared.logger import logger
from backend.tools.travel import poi_seed
from backend.travel.graph_state import load_brief
from backend.travel.models.itinerary import CHANGE_BRIEF
from backend.travel.models.brief import (
    DIET_KEYWORDS,
    PACE_KEYWORDS,
    PREFERENCE_KEYWORDS,
    SLOT_QUESTIONS,
    TravelBrief,
)

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 中文数字（含复合：「十」「十二」「二十」「二十五」）或 1-2 位阿拉伯数字。
# 注意交替顺序：复合形式必须在单字符之前，否则 "十二天" 会先命中 "二" 抽出 2。
_CN_COMPOUND = (r"(?:\d{1,2}"
                r"|[一二两三四五六七八九]?十[一二三四五六七八九]?"
                r"|[一二两三四五六七八九十])")

# 日期保护：「9月21日」的「21日」会被 _RE_DAYS 误读成 21 天（评测数据集
# T-E02 实测踩过：「9月21日福州一日游」抽出 days=21）。抽天数/区间前先把
# 日期表达替换为占位符——与 _RE_DATE_CN 同源，另含「9月21到25日」省写区间变体。
_RE_DATE_RANGE_CN = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]?\s*(?:到|至|[-~—])\s*\d{1,2}\s*[日号]?")
_DATE_MASK = "▚"


def _mask_dates(message: str) -> str:
    """把日期表达（含省写区间）替换为占位符，防止天数抽取误捕。

    顺序关键：先替换省写区间（长模式），否则「9月21到25日」会被短模式
    吃成「▚到25日」，残留的「25日」照样被误读成 25 天。
    """
    return _RE_DATE_CN.sub(_DATE_MASK, _RE_DATE_RANGE_CN.sub(_DATE_MASK, message))


def extract_date_range_days(
    message: str, today: date | None = None,
) -> tuple[int, str] | None:
    """日期区间 → 天数（「9月21到25日」= 5 天），返回 (天数, 原文)。

    此前日期区间只做遮蔽（防误读成 21 天），区间本身的天数没有兑现 ——
    用户给了明确日期却还要被追问「玩几天」。年份按「不早于今天」补齐
    （与 extract_start_date 同一规则）；跨年区间（12月30到1月2日）-end
    早于-start 时放弃计算，回落追问（不猜）。
    """
    today = today or date.today()
    match = _RE_DATE_RANGE_CN.search(message)
    if not match:
        return None
    # 省写区间两边不一定都带月份（「9月21到25日」右边只有日），逐侧解析：
    # 带「N月D日」用之；只有「D日」继承左侧月份。两侧都无月份 → 放弃。
    left, right = re.split(r"到|至|[-~—]", match.group(0), maxsplit=1)

    def _month_day(text: str) -> tuple[int | None, int | None]:
        full = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", text)
        if full:
            return int(full.group(1)), int(full.group(2))
        day_only = re.search(r"(\d{1,2})", text)
        return (None, int(day_only.group(1))) if day_only else (None, None)

    mo1, d1 = _month_day(left)
    mo2, d2 = _month_day(right)
    if d1 is None or d2 is None or mo1 is None:
        return None
    if mo2 is None:
        mo2 = mo1
    for year in (today.year, today.year + 1):
        try:
            start = date(year, mo1, d1)
            end = date(year, mo2, d2)
        except ValueError:
            return None
        if start >= today:
            if end < start:
                return None
            return (end - start).days + 1, match.group(0)
    return None


# 天数：阿拉伯数字或中文数字（含复合） + 天/日（原文已先经 _mask_dates 保护）
_RE_DAYS = re.compile(rf"({_CN_COMPOUND})\s*[天日]")
# 区间天数：「两三天」「三四天」「2-3天」「两到三天」。
# 相邻中文数字形式必须排除「十」（否则「十二天」会被当成区间 1-2）；
# 阿拉伯数字形式要求显式分隔符（否则「12天」会被劈成 1-2）。
_RE_DAYS_RANGE_CN = re.compile(r"([一二两三四五六七八九])([一二三四五六七八九])\s*[天日]")
_RE_DAYS_RANGE_SEP = re.compile(
    rf"({_CN_COMPOUND})\s*(?:到|至|[-~—])\s*({_CN_COMPOUND})\s*[天日]")
# 人数：XX人 / XX个人 / XX位
_RE_PARTY = re.compile(rf"({_CN_COMPOUND})\s*(?:个)?[人位]")
# 人数：「一家三口」「三口之家」—— 口语里最常见的家庭人数表达
_RE_FAMILY_KOU = re.compile(rf"(?:一家)?({_CN_COMPOUND})\s*口")
# 人数：同伴表达（无数字时的兜底）——「带爸妈」+2、「和女朋友」+1
_RE_COMPANION = re.compile(
    r"(?:带|和|跟|与)\s*(爸妈|父母|家人|孩子|小孩|朋友|女朋友|男朋友|"
    r"老婆|老公|对象|闺蜜|同事)")
_COMPANION_PLUS = {"爸妈": 2, "父母": 2}  # 其余同伴默认 +1
# 预算：必须带货币单位（或「万」），否则 "3天" 会被当成钱。
# 例外（STOP I2，STOP H Deferred #2）：「预算」关键词锚定的裸数字——
# "预算改成5000"（无「元」）是 PATCH 高频句式；锚定词在数字之前，
# 无预算语义的句子（"3天"）不含「预算」二字，不会误捕。
_RE_BUDGET_YUAN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|rmb|人民币)", re.I)
# 「万」口径支持中文数字（「预算两万」）：数字与「万」之间无其他成分；
# 阿拉伯数字不设位数上限（「预算5000万」），中文数字走复合规则
_RE_BUDGET_WAN = re.compile(
    r"(?:预算|大概|差不多|总共)?\s*"
    rf"(\d+(?:\.\d+)?|{_CN_COMPOUND})\s*[万wW]")
_RE_BUDGET_BARE = re.compile(r"预算[^。，,；;！!？?\d]{0,4}(\d+(?:\.\d+)?)")
# 住宿区域（STOP F2）：「住难波」「住在梅田」「酒店订在难波」。排除问句
# （住哪/住宿）与自指尾缀（「新宿的酒店」→ 新宿）。「住哪」是用户在问，
# 不是在回答，绝不能进槽位。
_RE_LODGING_ZHU = re.compile(r"(?:住在|住到|住)(?!宿|哪)[\s的]??([^。，,；;！!？?\s]{2,12})")
_RE_LODGING_HOTEL = re.compile(
    r"(?:酒店|民宿|宾馆)(?:在|定在|订在)\s*([^。，,；;！!？?\s]{2,12})")
_LODGING_TAIL_STRIP = ("的酒店", "的民宿", "的宾馆", "附近", "一带", "那边")
# 日期：ISO 或「X月X日」
_RE_DATE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_RE_DATE_CN = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
# 必去 / 避雷 触发词后面的地名（不含标点与空白）。
# 触发词与地名之间常带「的/是/：」等连接词（"必去的：烟台山"），跳过它们，
# 否则连接词会被吞进地名（实测产出 "的：烟台山" 这种脏条目直出行程单）。
_RE_TRIGGER_SKIP = r"[\s的：:是]{0,3}"
_RE_MUST_GO = re.compile(
    r"(?:必去|一定要去|必须去|想去|要去|打卡)" + _RE_TRIGGER_SKIP +
    r"([^。，,；;！!？?\s]{2,12}"
    r"(?:[、和及][^。，,；;！!？?\s]{2,12})*)"
)
_RE_AVOID = re.compile(
    r"(?:不要去|不想去|避开|别去|不去)" + _RE_TRIGGER_SKIP +
    r"([^。，,；;！!？?\s]{2,12}"
    r"(?:[、和及][^。，,；;！!？?\s]{2,12})*)"
)
_SPLIT_NAMES = re.compile(r"[、和及]")


def _to_int(token: str) -> int | None:
    """阿拉伯数字或中文数字（含「十/十二/二十/二十五」）→ int。"""
    if token.isdigit():
        return int(token)
    if "十" in token:
        head, _, tail = token.partition("十")
        tens = _CN_NUM.get(head, 1) if head else 1
        ones = _CN_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_NUM.get(token)


def extract_destination(message: str) -> str:
    """从消息中识别目的地城市（用数据集真实城市名录匹配，不做盲抽）。"""
    for city in poi_seed.all_cities():
        if city in message:
            return city
    # 别名（"榕城"、"鹭岛"、英文名等）
    for alias, city in poi_seed.CITY_ALIASES.items():
        if alias in message.lower():
            return city
    return ""


def extract_days(message: str) -> int | None:
    # 显式天数表达优先（「9月21到25日去福州玩2天」= 2 天，用户说了算）；
    # 没有显式天数时，日期区间才兜底（「9月21到25日福州玩」= 5 天）
    match = _RE_DAYS.search(_mask_dates(message))
    if match:
        value = _to_int(match.group(1))
        if value and value > 0:
            return value
    range_days = extract_date_range_days(message)
    if range_days is not None:
        return range_days[0]
    return None


def extract_days_range(message: str) -> tuple[int, int, str] | None:
    """识别区间天数说法（「两三天」「3-5天」），返回 (下限, 上限, 原文)。

    上限会被 extract_days 优先命中（「两三天」的正则首个命中就是「三天」），
    这里负责把「这是区间」这件事暴露出来，供 slot_filler 写提示 note ——
    取上限本身可辩护，但不该让用户毫无感知地被决定了天数。
    """
    masked = _mask_dates(message)
    for pattern in (_RE_DAYS_RANGE_SEP, _RE_DAYS_RANGE_CN):
        match = pattern.search(masked)
        if not match:
            continue
        lo, hi = _to_int(match.group(1)), _to_int(match.group(2))
        if lo is None or hi is None or lo <= 0 or lo > hi:
            continue
        return lo, hi, match.group(0)
    return None


def extract_party_size(message: str) -> int | None:
    """人数：数字表达 > 「一家三口」 > 同伴表达（带爸妈/和女朋友）。

    分层兜底是因为口语里大量需求不带「N人」字样；每层都要求明确的
    事实信号，不做开放式猜测（「组团去」之类一律不认）。
    """
    match = _RE_PARTY.search(message)
    if match:
        value = _to_int(match.group(1))
        if value and value > 0:
            return value
    match = _RE_FAMILY_KOU.search(message)
    if match:
        value = _to_int(match.group(1))
        if value and value > 0:
            return value
    match = _RE_COMPANION.search(message)
    if match:
        return 1 + _COMPANION_PLUS.get(match.group(1), 1)
    return None


def party_size_source(message: str) -> str:
    """人数的抽取来源：explicit（数字/一家X口）| guess（同伴推断）| none。

    guess 是有信息量的猜测（带爸妈≈3 人）但仍是猜测，slot_filler 据此
    写提示 note，让用户有机会纠正，而不是默默按猜的数字算钱。
    """
    if _RE_PARTY.search(message) or _RE_FAMILY_KOU.search(message):
        return "explicit"
    if _RE_COMPANION.search(message):
        return "guess"
    return "none"


# 知名城市名录：仅用于「用户点了名但暂不支持」的明示提醒，不做目的地
# 抽取（抽取只认数据集真实城市，不猜）。已支持的城市天然被跳过。
_KNOWN_MAJOR_CITIES = (
    "北京", "上海", "广州", "深圳", "成都", "重庆", "武汉", "西安",
    "南京", "天津", "苏州", "长沙", "郑州", "青岛", "大连", "昆明",
    "贵阳", "哈尔滨", "沈阳", "济南", "合肥", "南昌", "宁波", "无锡",
    "泉州", "宁德", "南平", "莆田", "漳州", "龙岩", "三明",
    # 境外热门目的地（2026-09-22 场景 B1）：点名时明确告知不支持，
    # 而不是当成没说过 —— 用户需要知道为什么城市没被接住
    "纽约", "伦敦", "巴黎", "东京", "大阪", "首尔", "曼谷",
    "新加坡", "吉隆坡", "香港", "澳门",
)


def extract_unsupported_city(message: str) -> str:
    """识别「用户点名了但数据集不支持」的知名城市，追问时明示原因。"""
    supported = set(poi_seed.all_cities())
    for city in _KNOWN_MAJOR_CITIES:
        if city in message and city not in supported:
            return city
    return ""


def extract_budget(message: str) -> float | None:
    """预算：优先认带「万」的说法，其次带货币单位，最后「预算」锚定的裸数字。"""
    match = _RE_BUDGET_WAN.search(message)
    if match:
        token = match.group(1)
        if token.replace(".", "", 1).isdigit():
            return round(float(token) * 10000, 2)
        whole = _to_int(token)
        if whole:
            return round(float(whole) * 10000, 2)
    match = _RE_BUDGET_YUAN.search(message)
    if match:
        return round(float(match.group(1)), 2)
    match = _RE_BUDGET_BARE.search(message)
    if match:
        return round(float(match.group(1)), 2)
    return None


def _clean_lodging(name: str) -> str:
    """剥掉捕获串里的尾缀与边界脏字（「新宿的酒店」→ 新宿）。"""
    for tail in _LODGING_TAIL_STRIP:
        if name.endswith(tail):
            name = name[: -len(tail)]
    while name and name[-1] in "的了的了":
        name = name[:-1]
    # 城市尾缀（STOP I2，STOP H 实测「白城沙滩厦门」）：捕获串末端黏着
    # 城市名时剥掉 —— 住宿区描述的真义是「白城沙滩」，城市属于 destination。
    for city in _lodging_city_names():
        if name != city and name.endswith(city):
            name = name[: -len(city)]
            break
    return name.strip()


def extract_lodging(message: str) -> str:
    """住宿区域抽取（STOP F2）：「住难波」「住在梅田」「酒店订在难波」。

    lodging 是记录性槽位（P0 无酒店供给数据，不参与排程与指纹——
    与 diet 同口径）；抽取它的目的是 pending 补槽判定与行程单如实回显。
    STOP I2 边界（STOP H Deferred #2）：剥尾缀后若命中城市名（或已知
    目的地名录），说明用户说的是城市不是住宿区（「住在厦门」），拒绝——
    城市信息走 destination 槽位，混进 lodging 会以假槽位触发误补槽。
    """
    for pattern in (_RE_LODGING_ZHU, _RE_LODGING_HOTEL):
        match = pattern.search(message)
        if match:
            cleaned = _clean_lodging(match.group(1))
            if len(cleaned) >= 2 and cleaned not in _lodging_city_names():
                return cleaned
    return ""


def _lodging_city_names() -> set[str]:
    """不可作为 lodging 的地名集合：种子城市 ∪ 别名 ∪ 知名城市名录。

    函数化而非模块级常量：poi_seed/名录随数据演进，读取时求值避免陈旧快照。
    """
    return set(poi_seed.all_cities()) | set(poi_seed.CITY_ALIASES) | set(_KNOWN_MAJOR_CITIES)


# NEW_RUN 显式信号（STOP F2，单一事实源在本模块）：用户明确推翻当前规划
# 重开。普通补槽/改约束（CONTINUE/PATCH/REPLAN）不含这些词。路由层
# resolver 与 slot_filler/adapter 都以这里为准（context → travel 单向
# 依赖，禁止反向 import 造成循环）。
# 「不想去」必须收紧为「不想去了」（STOP I2）：带地点的「不想去鼓浪屿了」
# 是 PATCH avoid，不是重开规划——裸「不想去」会把点名排除句误判成 NEW_RUN。
_NEW_RUN_RE = re.compile(
    r"重新规划|重新安排|重新来|换个方案|换套方案|换一个方案|不去了|不想去了"
)


def is_new_run_query(message: str) -> bool:
    """消息是否为「重开规划」显式信号（纯函数）。"""
    return bool(_NEW_RUN_RE.search(message or ""))


# CANCEL_RUN 显式信号（STOP G3）：取消**整个**规划任务。保守词表——
# 只认「规划/行程/安排」整体对象的取消；「不去海游馆了」类点名局部
# 景点的排除句是 PATCH avoid，由 _CANCEL_EXCLUDE_RE 先行排除（宁可
# 漏判走正常图处理，不可误取消整趟规划）。
_CANCEL_RUN_RE = re.compile(
    r"取消(整个|这次|本次|当前的?)?(规划|行程(规划)?|安排|旅行|旅游|计划)"
    r"|(规划|行程(规划)?|安排|计划)(不用|别|不)?(取消|取消了?)"
    r"|不(想|要|用)?(做)?(攻略|规划)了|别(规划|安排)了|先不(规划|去)了"
    r"|这次(旅行|旅游|行程)不(想|去)?了?"
)
# 局部排除句：「不去X了 / 不想去X了 / 不打算去X了」（X=具体地点，1~12 字）
# → PATCH avoid 场景，不得识别为取消整个规划（任务书 §16/T16）。
_CANCEL_EXCLUDE_RE = re.compile(r"不(想去|打算去|去).{1,12}?(了|啦)")


def is_cancel_run_query(message: str) -> bool:
    """消息是否为「取消整个规划」显式信号（纯函数，保守）。

    规则（任务书 §16）：
    - 真取消必须表达「取消整个规划 / 不规划了」这类整体意图；
    - 「不去海游馆了」类点名局部景点的排除句返回 False（PATCH avoid）。
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 40:
        return False
    if _CANCEL_EXCLUDE_RE.search(msg):
        return False
    return bool(_CANCEL_RUN_RE.search(msg))


# avoid-PATCH 显式信号（STOP I2，STOP H Deferred #1）：completed 态
#（无 pending）的「不去鼓浪屿了」。此时 TravelPendingResolver 的补槽通道
# 不工作（补槽仅 pending 期），travel_prefilter 也不命中（该句 0 强信号词
# 0 城市名）——没有它，用户的避雷诉求就静默丢失。判定复用 extract_avoid
#（唯一抽取点）：捕获到具体地点才算（「不想去了」无地点不拦）。
def is_avoid_patch_query(message: str) -> bool:
    """消息是否为「避开某地点」的局部修改信号（纯函数，保守）。

    只回答「这句话在表达 avoid」，不回答路由该不该拦——后者由
    TravelPendingResolver 结合「活跃 travel 任务是否存在」决定。
    与 cancel/new_run 的优先级：整体取消与重开规划优先，避免
    「不去厦门了，重新规划杭州」被误判成 avoid。
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 40:
        return False
    if is_cancel_run_query(msg) or is_new_run_query(msg):
        return False
    return bool(extract_avoid(msg))


def extract_start_date(message: str, today: date | None = None) -> date | None:
    """出发日期。只给月日时按「不早于今天」补年份，避免抽到过去的日期。"""
    today = today or date.today()

    match = _RE_DATE_ISO.search(message)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = _RE_DATE_CN.search(message)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                return None
            if candidate >= today:
                return candidate
    return None


def extract_preferences(message: str) -> list[str]:
    tags: list[str] = []
    for tag, keywords in PREFERENCE_KEYWORDS.items():
        if any(k in message for k in keywords):
            tags.append(tag)
    return tags


def extract_diet(message: str) -> str:
    """饮食忌口抽取（P1-1）：命中多个时取最长表述（信息量最大）。"""
    hits = [k for k in DIET_KEYWORDS if k in message]
    if not hits:
        return ""
    return max(hits, key=len)


def extract_pace(message: str) -> str | None:
    for pace, keywords in PACE_KEYWORDS.items():
        if any(k in message for k in keywords):
            return pace
    return None


# 触发词捕获串的清洗规则 —— 口语里触发词后面经常跟的不是地名，而是
# 「福州玩」「地方很多」「人多拥挤的地方」这类半截话。不清洗就会作为
# 脏条目进 must_go/avoid 并直出行程单（实测 bug）。
_GENERIC_NAMES = {"地方", "景点", "城市", "哪里", "哪儿", "人多拥挤"}
_GENERIC_TOKENS = ("很多", "不少", "好多", "好玩", "好看")
# 捕获串里带「数量+时间词」（两天/3号/几点）基本是日期天数被吞进地名
_RE_CAPT_NUM_TIME = re.compile(r"[一二两三四五六七八九\d]{1,3}\s*[天日晚号点月]")


def _clean_captured_name(name: str, destination: str = "") -> str | None:
    """清洗触发词捕获串：剥掉目的地前缀/动词缀/通用词，剩下不像地名的丢弃。

    返回 None 表示该捕获串不是地名，直接丢弃。只处理**触发词捕获**的
    串；POI 名录命中走的是另一条路（名字本身就是真的），不受影响。
    """
    name = name.strip()
    if destination and name.startswith(destination):
        name = name[len(destination):]
    while name and name[-1] in "玩游逛":
        name = name[:-1]
    while name and name[0] in "玩去到":
        name = name[1:]
    if name.endswith("的地方"):
        name = name[:-3]
    if len(name) < 2:
        return None
    if name in _GENERIC_NAMES or any(t in name for t in _GENERIC_TOKENS):
        return None
    if _RE_CAPT_NUM_TIME.search(name):
        return None
    return name


def _extract_names(
    pattern: re.Pattern, message: str, destination: str = "",
) -> list[str]:
    names: list[str] = []
    for match in pattern.finditer(message):
        for part in _SPLIT_NAMES.split(match.group(1)):
            name = _clean_captured_name(part, destination)
            if name and 2 <= len(name) <= 12 and name not in names:
                names.append(name)
    return names


def _filter_city_names(names: list[str]) -> list[str]:
    """城市名不是 POI：从必去/避雷清单剔除。

    「我想去北京玩」会让触发词捕获到「北京」；留着它，跨轮后必然产出
    「必去地点未能排入：北京」的假警告（城市进不了候选池是数据覆盖问题，
    不是行程排布问题）。目的地信息走 destination 槽位，不在这里表达。
    """
    city_names = set(poi_seed.all_cities()) | set(_KNOWN_MAJOR_CITIES)
    return [n for n in names if n not in city_names]


def extract_must_go(
    message: str, destination: str, avoid: list[str] | None = None,
) -> list[str]:
    """必去清单 = 真实 POI 名录命中 ∪ 触发词捕获（排除城市名与避雷项）。

    avoid 必须传入并参与过滤：POI 名录匹配是「名字出现在句子里就算」，
    因此「别去河坊街」会命中河坊街 —— 不过滤就会同时进必去清单，
    最终必然产生一条「必去地点未能排入」的假警告。
    """
    names = [p for p in poi_seed.all_poi_names() if p in message]
    for name in _extract_names(_RE_MUST_GO, message, destination):
        if name and name != destination and name not in names:
            names.append(name)

    if avoid:
        names = [
            n for n in names
            if not any(n in a or a in n for a in avoid if a)
        ]
    return _filter_city_names(names)


def extract_avoid(message: str) -> list[str]:
    """避雷清单 = 触发词捕获 ∪ 真实 POI 名录（"不要去鼓山"两种情况都能覆盖）。"""
    names = _extract_names(_RE_AVOID, message)
    for matched in _extract_names(_RE_AVOID, message):
        for poi_name in poi_seed.all_poi_names():
            if matched in poi_name or poi_name in matched:
                if poi_name not in names:
                    names.append(poi_name)
    return _filter_city_names(names)


def merge_brief(previous: TravelBrief, fresh: TravelBrief) -> TravelBrief:
    """多轮合并：新抽取到的字段覆盖旧值，未抽到的保留旧值。

    只在「用户这一轮补充了信息」时生效（如追问后回答"3天"），
    不会因为这一轮没提预算就把之前说的预算清空。
    """
    merged = previous.model_copy()
    for field in ("destination", "origin", "lodging"):
        value = getattr(fresh, field)
        if value:
            setattr(merged, field, value)
    for field in ("start_date", "days", "budget_cny"):
        value = getattr(fresh, field)
        if value is not None:
            setattr(merged, field, value)
    if fresh.party_size != 1 or previous.party_size == 1:
        if fresh.party_size >= 1:
            merged.party_size = fresh.party_size
    if fresh.preferences:
        merged.preferences = list(dict.fromkeys(previous.preferences + fresh.preferences))
    for field in ("must_go", "avoid"):
        combined = list(dict.fromkeys(getattr(previous, field) + getattr(fresh, field)))
        setattr(merged, field, combined)
    # avoid 的语义优先级高于 must_go：上一轮的必去被这一轮拉黑后必须移出
    # 必去清单，否则「不想去三坊七巷了」之后行程仍会把它当必去排入。
    if merged.avoid:
        merged.must_go = [
            n for n in merged.must_go
            if not any(n in a or a in n for a in merged.avoid if a)
        ]
    # 城市名不进必去清单：对「历史脏状态」（旧版本代码写入的 brief）同样
    # 成立，不能只信本轮 fresh 抽取干净。
    merged.must_go = _filter_city_names(merged.must_go)
    if fresh.pace != "moderate":
        merged.pace = fresh.pace
    # 饮食忌口：本轮有表述才覆盖（与 pace 同一合并语义）
    if fresh.diet:
        merged.diet = fresh.diet
    return merged


def extract_brief(message: str, previous: TravelBrief | None = None) -> TravelBrief:
    """规则抽取完整 brief（纯函数，可单测）。

    先抽 avoid 再抽 must_go —— 前者参与后者的过滤，顺序不能反。
    """
    fresh = TravelBrief(
        destination=extract_destination(message),
        days=extract_days(message),
        party_size=extract_party_size(message) or 1,
        budget_cny=extract_budget(message),
        start_date=extract_start_date(message),
        preferences=extract_preferences(message),
        pace=extract_pace(message) or "moderate",
        diet=extract_diet(message),
        lodging=extract_lodging(message),
    )
    fresh.avoid = extract_avoid(message)
    fresh.must_go = extract_must_go(message, fresh.destination, fresh.avoid)
    return merge_brief(previous, fresh) if previous else fresh


def build_clarification(brief: TravelBrief, user_message: str = "") -> str:
    """必填槽位缺失时的追问文案。

    user_message 用于识别「用户点名了不支持的城市」——此时明确告知原因
    （缺当地地点数据），而不是让用户对着城市列表猜自己哪里答错了。
    P1-3：destination 缺失时附偏好推荐，让用户有「可以直接选」的起点。
    """
    missing = brief.missing_slots()
    if not missing:
        return ""
    questions = [SLOT_QUESTIONS.get(s, s) for s in missing]
    lines = ["为了把行程排准，还需要确认："]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    cities_line = "、".join(poi_seed.all_cities())
    unsupported = extract_unsupported_city(user_message) if user_message else ""
    if unsupported and "destination" in missing:
        lines.append(
            f"\n你提到的「{unsupported}」暂时无法规划（还没有当地的地点数据），"
            f"当前可规划的城市：{cities_line}"
        )
    else:
        lines.append(f"\n（当前可规划的城市：{cities_line}）")
    if "destination" in missing:
        try:
            from backend.travel.recommend import (
                recommend_cities,
                render_recommendation_line,
            )

            rec_line = render_recommendation_line(
                recommend_cities(brief.preferences))
            if rec_line:
                lines.append(f"\n{rec_line}，回复城市名即可开始规划。")
        except Exception:  # noqa: BLE001 — 推荐是增强项，失败不影响追问
            pass
    return "\n".join(lines)


def slot_filler_node(state: dict) -> dict:
    """槽位节点：抽取 → 合并 → 判定需求是否变化 → 计算缺失槽位与追问文案。

    跨轮失效判定放在这里、而不是 supervisor：slot_filler 是每轮**唯一**会
    改写 brief 的地方，判断依据（新旧指纹）只有它同时拿得到。
    """
    from backend.travel.graph_state import brief_fingerprint, planning_reset

    message = state.get("user_message", "")
    # brief 基底：checkpoint 产物优先；无 checkpoint（STOP F3 reconstruct
    # 轮）时用适配器从 ConversationContext 重建的事实基底，二者皆无才从零抽
    raw_brief = state.get("brief") or state.get("reconstruct_brief") or {}
    previous = load_brief({"brief": raw_brief}) if raw_brief else None

    # 持久化状态（任务书 §10，Phase 4）：图入口每轮把当前状态写进 state
    # —— 这是该事实的唯一产生点，下游（supervisor_decision / reporter）
    # 只消费不重算。延迟 import：graph_builder 装配图时顶层 import 本模块，
    # 顶部 import 会成环（与 stamp_version 的延迟 import 同理）。
    from backend.travel.graph_builder import get_persistence_status

    persistence_status = get_persistence_status()
    # 强持久化策略（任务书 §10）：TRAVEL_REQUIRE_PERSISTENCE 开启且已降级时，
    # **拒绝复用跨轮产物** —— 多 worker 部署下 MemorySaver 各存一份，第二轮
    # 请求可能被路由到另一个 worker，跨轮改单会静默失效（用户拿到与上一轮
    # 无关的新行程还以为改成功了）。宁可每轮按全新规划处理，也要如实告知。
    require_fresh = (persistence_status == "degraded"
                     and T.TRAVEL_REQUIRE_PERSISTENCE)

    brief = extract_brief(message, previous)

    # P1-1 偏好持久化（软失败，读写失败都不影响规划主链）：
    #   预填 —— 跨轮首轮（无上一轮 brief）且开启了偏好功能时，把历史偏好
    #   填进本轮没表达的槽位；发生在指纹计算之前，同轮内一次性完成。
    #   回写 —— 本轮用户明确表达的偏好（标签/节奏/忌口）upsert 落库。
    if previous is None and T.TRAVEL_PREFS_ENABLED and state.get("user_id"):
        try:
            from backend.tools.travel import preferences as prefs_store

            saved = prefs_store.get_preferences(state.get("user_id", ""))
            if saved:
                if not brief.preferences and saved.get("preferences"):
                    brief.preferences = list(saved["preferences"])
                if not brief.origin and saved.get("origin"):
                    brief.origin = saved["origin"]
                if not brief.diet and saved.get("diet"):
                    brief.diet = saved["diet"]
                if (brief.pace == "moderate" and saved.get("pace")
                        in ("relaxed", "intense")):
                    brief.pace = saved["pace"]
                logger.info("[TravelSlotFiller] 已预填历史偏好: tags=%s pace=%s",
                            brief.preferences, brief.pace)
        except Exception:  # noqa: BLE001 — 预填失败按未填处理
            logger.debug("[TravelSlotFiller] 偏好预填失败", exc_info=True)
    if T.TRAVEL_PREFS_ENABLED and state.get("user_id"):
        try:
            from backend.tools.travel import preferences as prefs_store

            prefs_store.upsert_preferences(
                state.get("user_id", ""),
                origin=brief.origin or "",
                preferences=brief.preferences,
                pace=(brief.pace if brief.pace != "moderate" else ""),
                diet=brief.diet or "",
            )
        except Exception:  # noqa: BLE001 — 回写失败不影响本轮
            logger.debug("[TravelSlotFiller] 偏好回写失败", exc_info=True)

    missing = brief.missing_slots()
    clarification = build_clarification(brief, message)

    fingerprint = brief_fingerprint(brief)
    last_fingerprint = state.get("brief_fingerprint") or ""
    # 仅在「有上一轮指纹且不同」时失效：首次进入（无指纹）不算变化
    brief_changed = bool(last_fingerprint) and last_fingerprint != fingerprint

    # 版本链（任务书 §4）：指纹变化 = 需求实质变化 → version +1，并记录
    # 变化原因与差异字段（transit expert 盖版本章时消费）。必须先递增再
    # 构造 update —— update["brief"] 要带着新版本号落库。
    brief_change_reason = ""
    brief_changed_fields: list[str] = []
    if brief_changed and previous is not None:
        brief.version = previous.version + 1
        brief_change_reason = CHANGE_BRIEF
        old_dump = previous.model_dump()
        new_dump = brief.model_dump()
        brief_changed_fields = sorted(
            k for k in new_dump
            if k != "version" and old_dump.get(k) != new_dump[k]
        )

    logger.info(
        "[TravelSlotFiller] destination=%r days=%s missing=%s changed=%s",
        brief.destination, brief.days, missing, brief_changed,
    )

    # 透明化提示：猜测与区间说法不拦流程，但必须让用户看见、可纠正。
    # notes 每轮重写（旧轮提示对新规划已过时）；risk expert 在本轮末尾
    # 读取 state.notes 累加风险提示，不冲突。
    notes: list[str] = []
    # 日期区间透明化：「9月21到25日」按区间天数规划，让用户看得见换算结果
    date_range = extract_date_range_days(message)
    if date_range and brief.days == date_range[0]:
        notes.append(f"你说的「{date_range[1]}」共 {date_range[0]} 天，已按此规划；"
                     "想调整直接说「改成 N 天」")
    day_range = extract_days_range(message)
    if day_range and brief.days == day_range[1]:
        notes.append(
            f"你说的「{day_range[2]}」是区间说法，先按上限 {day_range[1]} 天规划；"
            "想调整直接说「改成 N 天」"
        )
    if party_size_source(message) == "guess" and brief.party_size > 1:
        notes.append(
            f"人数按 {brief.party_size} 人估算（根据你提到的同伴）；"
            "如不对，直接说「X个人」"
        )

    update: dict = {
        "brief": brief.model_dump(),
        "brief_missing": missing,
        "clarifications": [clarification] if clarification else [],
        "brief_fingerprint": fingerprint,
        "persistence_status": persistence_status,
        "stage": "slot",
    }

    if require_fresh:
        # 强持久化策略下的降级处置（任务书 §10）：无条件清跨轮产物，
        # 即使指纹没变 —— 降级后端里留着的上一轮产物不可信（多 worker
        # 不共享、重启即失）。planning_reset 会清 notes，note 必须在其后写。
        update.update(planning_reset())
        notes.insert(
            0,
            "持久化已降级（当前为临时存储），本轮按全新规划处理；"
            "在恢复持久化之前，跨轮修改行程暂不可用",
        )
        logger.warning(
            "[TravelSlotFiller] REQUIRE_PERSISTENCE 开启且持久化降级，"
            "拒绝复用跨轮产物，按全新规划处理"
        )

    if brief_changed:
        # 需求变了：旧行程作废，连同执行态一起清掉重新规划。
        # 注意 planning_reset() 会把 notes 置空，所以 notes 必须在它之后写。
        update.update(planning_reset())
        # 变化原因与差异字段不进 planning_reset 清单：变化当轮产生、当轮被
        # transit expert 消费（盖版本章），跨轮保留也无害（下次变化会覆盖）。
        update["brief_change_reason"] = brief_change_reason
        update["brief_changed_fields"] = brief_changed_fields
        notes.insert(0, "需求已变化，已按新需求重新规划（上一版行程作废）")
        logger.info("[TravelSlotFiller] 需求指纹变化 %s→%s（brief v%d，变化字段 %s），清空规划产物重排",
                    last_fingerprint, fingerprint, brief.version,
                    brief_changed_fields or "未知")

    if (bool((state.get("travel_route") or {}).get("new_run"))
            or is_new_run_query(message)):
        # NEW_RUN（STOP F2）：用户显式「重新规划」。两个来源：路由层
        # resolver 在 pending 场景打的标记（travel_route.new_run），以及
        # 无 pending 时（任务已齐备出单）对消息本身的直接判定。与
        # brief_changed 分级：带新信息时上面指纹分支已重排（此处重复
        # reset 幂等）；不带新信息（「重新规划一下」指纹不变）时只有
        # 这里能保证出全新方案，而不是把上一版行程原样再输出一遍。
        update.update(planning_reset())
        notes.insert(0, "已按你的要求重新规划（新方案独立生成）")
        logger.info("[TravelSlotFiller] NEW_RUN 信号，规划产物已清空重排")

    update["notes"] = notes
    return update
