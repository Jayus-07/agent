"""travel/agents/requirement_agent.py — Requirement Agent（v3 §3.1 ② / Phase 2）

职责边界（冻结）：自然语言需求理解 / 槽位抽取 / 缺失检测 / TripBrief 生成 /
追问文案。只回答「用户这轮说了什么」，不做合并（merge_brief 归
services/requirement_service.py）、不碰 checkpoint、不碰 memory、不判会话
意图（重开/取消信号在 core/intent_signals.py）。

零 LLM（Phase 2 冻结，确定性优先）：全部为可单测穷举的正则与词典。未来
LLM 结构化补全的扩展位只在本文档说明，**不设代码接口、不接配置**——
接入时的硬约束：仅当规则层 required 槽缺失/低置信才允许触发；单 run ≤1 次；
禁止重试；失败必须回落规则结果+追问；LLM 不得写业务状态。

抽取边界（明确不猜，历史事故修复原样保留）：
  - 没有货币单位的数字不当预算（"3天"不是 3000 元）
  - 城市名与景点名分开处理：城市进 destination，只有真实 POI 名录里的
    名字才进 must_go，其余通过「必去/想去」触发词捕获，未匹配到数据时
    会在行程单里如实提示（不伪造条目）
  - 日期表达先遮蔽再抽天数（「9月21日」的「21日」防误读成 21 天）
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from backend.tools.travel import poi_seed
from backend.travel.data import cities as city_directory
from backend.travel.models.brief import (
    DIET_KEYWORDS,
    PACE_KEYWORDS,
    PREFERENCE_KEYWORDS,
    TIER_KEYWORDS,
    SLOT_QUESTIONS,
    TravelBrief,
)
from backend.travel.services.requirement_service import (
    KNOWN_MAJOR_CITIES,
    filter_city_names,
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
_RE_DAYS = re.compile(rf"(?<![第\d一二两三四五六七八九十])({_CN_COMPOUND})\s*[天日](?!气)")
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
# 成人/儿童显式表达（Phase 2 adults/children 拆分，修 D4「2个大人」排成 1 人）：
# 数字 + 大人/成人 / 大人/成人 + 数字，儿童同构。不带数字的「带孩子」是同伴
# guess 表达（_RE_COMPANION 管辖），这里不认——避免与同伴推断重复计数。
_RE_ADULTS = re.compile(rf"({_CN_COMPOUND})\s*[个位名]?\s*(?:大人|成人)")
_RE_ADULTS_REV = re.compile(rf"(?:大人|成人)\s*({_CN_COMPOUND})\s*[位名个]?")
_RE_CHILDREN = re.compile(rf"({_CN_COMPOUND})\s*[个位名]?\s*(?:小孩|儿童|孩子)")
_RE_CHILDREN_REV = re.compile(rf"(?:小孩|儿童|孩子)\s*({_CN_COMPOUND})\s*[位名个]?")
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
# 相对日期（验收 #63）：「明天/后天/大后天」「下周五」「这周末」。
# 「(周|星期|礼拜)X」允许无前缀（「周五」=最近的未来周五）；「周末」指周六+周日
# 两天，歧义取周六并回显（slot_filler 消费）。匹配顺序：大后天必须先于后天。
_RE_REL_DAYS = re.compile(r"(大后天|后天|明天)")
_RE_REL_WEEKDAY = re.compile(r"(这|本|下)?(?:周|星期|礼拜)([一二三四五六日天])")
_RE_REL_WEEKEND = re.compile(r"(这|本|下)?周末")
# 模糊时间词（验收 #64）：「月底去上海」无法唯一定日 —— 一律不猜具体日期，
# 命中后由 slot_filler 明示（先按「第 1 天」排，确定后补具体日期）。
_RE_VAGUE_TIME = re.compile(
    r"(月初|月中|月底|月末|年初|年底|上半年|下半年|上旬|中旬|下旬|"
    r"节假日|寒假|暑假|周末前后|月底前后|月初前后)")
_WEEKDAY_CN_TO_MON1 = {"一": 1, "二": 2, "三": 3, "四": 4,
                       "五": 5, "六": 6, "日": 7, "天": 7}
_REL_DAYS_OFFSET = {"明天": 1, "后天": 2, "大后天": 3}

# 首末日时间（验收 #82）：「16点到」「晚上8点到」「上午走」「10点出发」。
# 显式时刻优先；「晚上到」「上午走」这类无数字的模糊时段按约定钟点承接
# （slot_filler 回显，用户可纠正）。模糊时段 → 默认钟点（24h）：
_PERIOD_DEFAULT = {"早上": "09:00", "上午": "09:00", "中午": "12:00",
                   "下午": "15:00", "傍晚": "18:00", "晚上": "20:00",
                   "夜里": "22:00", "半夜": "23:00"}
_PERIOD_PREFIX = r"(?:早上|上午|中午|下午|傍晚|晚上|夜里|半夜)?"
# 「点」后允许「半」或两位分钟；「左右/前后」是口语尾缀。
# 分组契约（消费方 extract_arrival_time/extract_departure_time 依赖顺序）：
# 1=时段前缀 2=钟点 3=「半」 4=分钟数字 —— 前缀必须是捕获组。
_TIME_POINT = (r"(早上|上午|中午|下午|傍晚|晚上|夜里|半夜)?\s*"
               rf"({_CN_COMPOUND})\s*[点时:：]\s*"
               r"(?:(半)|(\d{1,2})\s*分?)?\s*(?:左右|前后|上下)?")
_RE_ARRIVAL_EXPLICIT = re.compile(
    _TIME_POINT + r"\s*(?:才|就)?(?:到|抵达|到达)(?!\s*\d{1,2}\s*[点时:：])")
_RE_DEPART_EXPLICIT = re.compile(_TIME_POINT + r"\s*(?:就)?(?:走|出发|离开|返程|回程)")
_RE_ARRIVAL_VAGUE = re.compile(r"(早上|上午|中午|下午|傍晚|晚上|夜里|半夜)\s*(?:才|就)?(?:到|抵达|到达)")
_RE_DEPART_VAGUE = re.compile(r"(早上|上午|中午|下午|傍晚|晚上|夜里|半夜)\s*(?:就)?(?:走|出发|离开|返程|回程)")
# 下午/晚上类前缀把 1-11 点修正为 13-23 点（「下午4点」=16:00）；12 点不进位
_PM_PREFIXES = ("下午", "傍晚", "晚上")
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
# 软必去触发词（验收 #67）：「有空再去/顺便去」——意愿真实但让位优先级低，
# 与 must_go 的「永不被静默删除」分级；捕获清洗与 must_go 同一套。
_RE_OPTIONAL_GO = re.compile(
    r"(?:有空(?:的话)?(?:再|就)去|有时间(?:的话)?(?:再|就)去|顺便(?:去|逛)|"
    r"如果来得及(?:就|再)?去|可以的话(?:再|就)?去|想去的话(?:再|就)去)" +
    _RE_TRIGGER_SKIP +
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


def _first_int(message: str, *patterns: re.Pattern) -> int | None:
    """依次尝试多个模式，返回首个可解析的数字（adults/children 抽取共用）。"""
    for pattern in patterns:
        match = pattern.search(message)
        if match:
            value = _to_int(match.group(1))
            if value and value > 0:
                return value
    return None


# 目的地否定语境（STOP I2，T7）：「不去厦门了，重新规划杭州两天」同时
# 提到两个城市 —— 被否定/被放弃的那个不是目的地。匹配城市名左边界，
# 判定其紧邻前缀是否为否定/放弃表达。
_RE_CITY_NEGATION = re.compile(r"(?:不去|不想去|别去|不要去|避开|离开)$")

# ── 城市扫描（v3 P0-A 名录扩容后的单一入口）─────────────────────────
# 语义变更（2026-10-02 拍板）：匹配集从「种子 3 城」扩为「识别名录 ∪
# 种子池」——名录是识别词表不是支持范围闸门（TRAVEL_POI_SOURCE=live 后
# 支持 = Provider 能力边界，见 data/cities.py 模块头）。destination /
# route_pair / origin 三处共用本入口，禁止各自再扫 poi_seed。
#
# 后缀守卫：「南京路 / 北海公园 / 香格里拉酒店 / 中山广场」这类含城市名
# 的普通词不是目的地——命中城市名后紧跟这些后缀时作废该次命中。
_POSTFIX_GUARD_RE = re.compile(
    r"(?:路|街|巷|门|桥|站|道|公园|酒店|饭店|大厦|广场|机场|大学|中学)"
)
_CITY_SCAN_CACHE: tuple[list[str], dict[str, str]] | None = None


def _city_scan_set() -> tuple[list[str], dict[str, str]]:
    """识别名录∪种子城市与别名合集（进程级缓存；两份静态数据运行期不变）。"""
    global _CITY_SCAN_CACHE
    if _CITY_SCAN_CACHE is None:
        names = sorted(
            set(city_directory.all_directory_cities())
            | set(poi_seed.all_cities()),
            key=len, reverse=True,
        )
        aliases = {**poi_seed.CITY_ALIASES, **city_directory.directory_aliases()}
        _CITY_SCAN_CACHE = (names, aliases)
    return _CITY_SCAN_CACHE


def _iter_city_hits(message: str) -> list[tuple[int, str]]:
    """消息中出现的城市命中（纯函数）：返回 (位置, 标准名) 列表。

    同名多次出现保留多个位置（多城消歧按消息顺序取最左需要）；别名与
    正名同位置的重复命中在返回前去重。
    """
    text = message or ""
    if not text:
        return []
    lowered = text.lower()
    names, aliases = _city_scan_set()
    hits: list[tuple[int, str]] = []

    def _guarded(pos: int, length: int, frag: str) -> bool:
        end = pos + length
        # 拼音别名只匹配完整英文词，避免 dali 命中 dalian。
        if frag.isascii() and (
            (pos > 0 and text[pos - 1].isascii() and text[pos - 1].isalpha())
            or (end < len(text) and text[end].isascii() and text[end].isalpha())
        ):
            return True
        if _POSTFIX_GUARD_RE.match(text, end):
            return True
        # 词内误报（「三明治」含三明）：负向词的出现区间与本次命中重叠才作废
        for phrase in city_directory._NEGATIVE_PHRASES:
            if frag not in phrase:
                continue
            for m in re.finditer(re.escape(phrase), text):
                if m.start() < end and m.end() > pos:
                    return True
        return False

    for name in names:
        start = 0
        while True:
            pos = text.find(name, start)
            if pos < 0:
                break
            start = pos + len(name)
            if not _guarded(pos, len(name), name):
                hits.append((pos, name))
    for alias, city in aliases.items():
        start = 0
        while True:
            pos = lowered.find(alias, start)
            if pos < 0:
                break
            start = pos + len(alias)
            if not _guarded(pos, len(alias), alias):
                hits.append((pos, city))
    # 同位置同城市去重（正名/别名同时命中）；保留不同位置
    return sorted(set(hits), key=lambda h: h[0])


def extract_destination(message: str, previous_destination: str = "") -> str:
    """从消息中识别目的地城市（识别名录匹配，不做盲抽）。

    匹配集 = data/cities.py 识别名录 ∪ 种子池（v3 P0-A 扩容：名录是
    识别词表不是支持范围闸门）。多城市同现时按确定性消歧（STOP I2）：
      1. 剔除紧邻否定/放弃表达的（「不去厦门了」的厦门）；
      2. 仍有多个且上一轮目的地在场 → 剔除上一轮目的地（变化目标优先，
         「换」语义）；单城市时直接取（含与上一轮相同的重申）；
      3. 其余按**消息出现顺序**取最左（首个提及通常是主目的地，
         优于目录序——目录序会让「不去厦门…杭州」错取厦门）。
    无法识别返回空串（由调用方走追问）。
    """
    route = _extract_route_city_pair(message)
    if route:
        return route[1]

    # 不能因为消息里同时出现了出发城市，就把未覆盖的目的地静默替换成
    # 出发城市。例如「泉州，从福州出发」此前会被误抽成「福州」，页面
    # 看起来像成功，实际行程却完全去了错误的城市。显式点名未支持目的地
    # 时返回空槽位，让 build_clarification 如实提示支持范围。
    if _extract_explicit_unsupported_destination(message):
        return ""

    hits = _iter_city_hits(message)
    if not hits:
        return ""

    candidates = [c for _, c in hits]
    if len(candidates) > 1:
        # 1) 否定/放弃语境的城市出局
        kept: list[str] = []
        for pos, city in hits:
            prefix = message[max(0, pos - 4):pos]
            if _RE_CITY_NEGATION.search(prefix):
                continue
            kept.append(city)
        candidates = kept or candidates
        # 2) 「换目的地」语义：多个候选含上一轮目的地时，变化目标优先
        if (len(candidates) > 1 and previous_destination
                and previous_destination in candidates):
            candidates = [c for c in candidates if c != previous_destination]
        # 3) 消息出现顺序取最左
        order: dict[str, int] = {}
        for pos, city in hits:
            order.setdefault(city, pos)
        candidates.sort(key=lambda c: order[c])
    return candidates[0]


def _extract_route_city_pair(message: str) -> tuple[str, str] | None:
    """识别「从 A 出发去 B / A 到 B」中的城市对（A、B 均须在消息中出现）。"""
    text = message or ""
    hits = _iter_city_hits(text)
    if len(hits) < 2:
        return None
    # 同城市取最早出现位置；长名优先保持原语义（「乌鲁木齐到吐鲁番」
    # 不能被「鲁到」之类的短串干扰——名录里本就没有短串，这里只防别名）
    first_pos: dict[str, int] = {}
    for pos, city in hits:
        first_pos.setdefault(city, pos)
    ordered = sorted(first_pos, key=len, reverse=True)
    for origin in ordered:
        for destination in ordered:
            if origin == destination:
                continue
            origin_pattern = _city_name_pattern(origin)
            destination_pattern = _city_name_pattern(destination)
            patterns = (
                rf"(?:从|由)\s*{origin_pattern}\s*(?:出发\s*)?(?:去|到|前往)\s*{destination_pattern}",
                rf"{origin_pattern}\s*(?:到|去|前往)\s*{destination_pattern}",
            )
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
                return origin, destination
    return None


def _city_name_pattern(city: str) -> str:
    """标准城市与别名的原文匹配式；路线识别后才归一化输出。"""
    variants = {city} | {alias for alias, target in _city_scan_set()[1].items()
                        if target == city}
    return "(?:" + "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True)) + ")"


def _extract_explicit_unsupported_destination(message: str) -> str:
    """识别带目的地语义的未覆盖城市（境外/名录外），避免被已覆盖城市抢槽位。"""
    supported = set(_city_scan_set()[0])
    for city in sorted(KNOWN_MAJOR_CITIES, key=len, reverse=True):
        if city in supported:
            continue
        start = 0
        while True:
            position = message.find(city, start)
            if position < 0:
                break
            prefix = message[max(0, position - 8):position]
            # 「不去泉州，改去厦门」中的泉州是排除项，不应阻断后面的
            # 已支持目的地；「泉州，从福州出发」则是开头直接点名。
            if _RE_CITY_NEGATION.search(prefix):
                start = position + len(city)
                continue
            if position == 0 or re.search(
                    r"(?:去|到|前往|规划|安排|游玩|玩|目的地(?:是|为)?)\s*$",
                    prefix):
                return city
            start = position + len(city)
    return ""


def extract_origin(message: str) -> str:
    """抽取跨城交通语句中的出发城市；没有明确路线时保持为空。"""
    route = _extract_route_city_pair(message)
    if route:
        return route[0]
    text = message or ""
    names = _city_scan_set()[0]  # 已按长度降序
    for city in names:
        if re.search(rf"(?:从|由)\s*{_city_name_pattern(city)}\s*(?:出发|启程)",
                     text, re.IGNORECASE):
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
    day_range = extract_days_range(message)
    if day_range is not None:
        return day_range[1]
    range_days = extract_date_range_days(message)
    if range_days is not None:
        return range_days[0]
    return None


def extract_days_range(message: str) -> tuple[int, int, str] | None:
    """识别区间天数说法（「两三天」「3-5天」），返回 (下限, 上限, 原文)。

    extract_days 对区间表达取上限；本函数同时保留区间原文，
    供 slot_filler 写透明化提示 note ——
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
    """人数的抽取来源：explicit（数字/一家X口/大人儿童显式表达）| guess
    （同伴推断）| none。

    guess 是有信息量的猜测（带爸妈≈3 人）但仍是猜测，slot_filler 据此
    写提示 note，让用户有机会纠正，而不是默默按猜的数字算钱。
    大人/儿童显式表达（「2个大人」）是明确事实，计 explicit。
    """
    if (_RE_PARTY.search(message) or _RE_FAMILY_KOU.search(message)
            or _RE_ADULTS.search(message) or _RE_ADULTS_REV.search(message)
            or _RE_CHILDREN.search(message) or _RE_CHILDREN_REV.search(message)):
        return "explicit"
    if _RE_COMPANION.search(message):
        return "guess"
    return "none"


def extract_adults_children(message: str) -> tuple[int | None, int | None]:
    """成人/儿童显式拆分（Phase 2，D4 修复）。

    只认带数字的显式表达（「2个大人」「大人两位」「1个小孩」「孩子两个」）；
    同伴 guess 表达（「带爸妈」）不进这里。返回 (adults, children)，未表达
    为 None——party_size 的派生规则见 extract_fresh_brief（显式总数优先）。
    """
    adults = _first_int(message, _RE_ADULTS, _RE_ADULTS_REV)
    children = _first_int(message, _RE_CHILDREN, _RE_CHILDREN_REV)
    return adults, children


def extract_unsupported_city(message: str) -> str:
    """识别「用户点名了但不支持」的知名城市，追问时明示原因。

    「不支持」的语义随 v3 P0-A 收紧：名录（识别词表）∪种子池之外、
    且在 KNOWN_MAJOR_CITIES（境外等明确不支持名单）里的才算。名录内
    城市（北京/西安/丽江…）live 模式下受支持，不再进入本判定。
    """
    supported = set(_city_scan_set()[0])
    for city in KNOWN_MAJOR_CITIES:
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
    return (set(poi_seed.all_cities()) | set(poi_seed.CITY_ALIASES)
            | set(KNOWN_MAJOR_CITIES))


def _resolve_relative_date(message: str, today: date) -> date | None:
    """相对日期词 → 具体日期（验收 #63）。命中不了返回 None。

    「周末」歧义（周六+周日两天）取周六并回显；今天周日时「这周末」的
    周六已过，取今天（周末的剩余部分）。无前缀或「这/本」前缀的周 X 若
    已过去则顺延到下周（用户不会指过去的周五）；「下」前缀恒为下周。
    """
    match = _RE_REL_DAYS.search(message)
    if match:
        return today + timedelta(days=_REL_DAYS_OFFSET[match.group(1)])

    match = _RE_REL_WEEKEND.search(message)
    if match:
        this_monday = today - timedelta(days=today.weekday())
        saturday = this_monday + timedelta(days=5)
        if match.group(1) == "下":
            return saturday + timedelta(days=7)
        return max(saturday, today)

    match = _RE_REL_WEEKDAY.search(message)
    if match:
        target = _WEEKDAY_CN_TO_MON1.get(match.group(2))
        if target is None:
            return None
        this_monday = today - timedelta(days=today.weekday())
        candidate = this_monday + timedelta(days=target - 1)
        if match.group(1) == "下":
            candidate += timedelta(days=7)
        elif candidate < today:
            candidate += timedelta(days=7)
        return candidate
    return None


def extract_start_date(message: str, today: date | None = None) -> date | None:
    """出发日期。

    优先级：明确日期（ISO / 中文月日）> 相对日期词。只给月日时按
    「不早于今天」补年份；ISO 给全年月日时若早于今天则**不采用**（验收
    #71：过去日期直接当没说，由 extract_past_date 负责出拦截提示）。
    """
    today = today or date.today()

    match = _RE_DATE_ISO.search(message)
    if match:
        try:
            candidate = date(int(match.group(1)), int(match.group(2)),
                             int(match.group(3)))
        except ValueError:
            return None
        return candidate if candidate >= today else None

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

    return _resolve_relative_date(message, today)


def extract_relative_date_expr(message: str) -> str:
    """命中的相对日期词原文（供 slot_filler 回显）。

    明确日期（ISO/中文月日）优先级高于相对词——「9月21日周五」按 9月21日
    解析；此时回显「周五」会误导用户以为按相对词解析，故在源头返回空串。
    """
    if _RE_DATE_ISO.search(message) or _RE_DATE_CN.search(message):
        return ""
    for pattern in (_RE_REL_DAYS, _RE_REL_WEEKEND, _RE_REL_WEEKDAY):
        match = pattern.search(message)
        if match:
            return match.group(0)
    return ""


def extract_vague_time_expr(message: str) -> str:
    """命中的模糊时间词原文（验收 #64），未命中返回空串。

    「月底/十一前后」这类表达无法唯一定日，抽取层不猜；调用方据此明示
    「按第 1 天排、确定后补具体日期」。与相对词互斥：能解析相对词的消息
    不再报模糊（「下周五」有确定日）。
    """
    if extract_relative_date_expr(message):
        return ""
    if _RE_DATE_ISO.search(message) or _RE_DATE_CN.search(message):
        return ""
    match = _RE_VAGUE_TIME.search(message)
    return match.group(0) if match else ""


def extract_past_date(message: str, today: date | None = None) -> date | None:
    """消息里的 ISO 完整日期若早于今天则原样返回（验收 #71 的提示依据）。

    extract_start_date 对过去日期返回 None（与「没说」不可区分）；本函数
    让 slot_filler 能区分「没给日期」与「给了但已过去」，后者必须明示
    拦截原因。中文月日自动进位明年，不产生过去日期，不进本判定。
    """
    today = today or date.today()
    match = _RE_DATE_ISO.search(message)
    if not match:
        return None
    try:
        candidate = date(int(match.group(1)), int(match.group(2)),
                         int(match.group(3)))
    except ValueError:
        return None
    return candidate if candidate < today else None


def _normalize_time_point(period: str, hour_token: str, half: str,
                          minute: str) -> str | None:
    """时段前缀 + 钟点 + 分 → 归一 "HH:MM"；非法钟点返回 None（不猜）。"""
    hour = _to_int(hour_token)
    if hour is None or not 0 <= hour <= 23:
        return None
    if period in _PM_PREFIXES and hour < 12:
        hour += 12
    minute_value = 30 if half else int(minute or 0)
    if minute_value > 59:
        return None
    return f"{hour:02d}:{minute_value:02d}"


def extract_arrival_time(message: str) -> str | None:
    """到达时刻（验收 #82）：「16点到」「晚上8点到」「晚上到」→ "HH:MM"。

    显式钟点优先（含时段进位）；无数字的模糊时段按 _PERIOD_DEFAULT 约定
    钟点承接。都不命中返回 None（用户没说 ≠ 任何默认值）。
    """
    match = _RE_ARRIVAL_EXPLICIT.search(message)
    if match:
        return _normalize_time_point(match.group(1) or "", match.group(2),
                                     match.group(3), match.group(4))
    match = _RE_ARRIVAL_VAGUE.search(message)
    if match:
        return _PERIOD_DEFAULT[match.group(1)]
    return None


def extract_departure_time(message: str) -> str | None:
    """离开时刻（验收 #82）：「10点走」「上午走」→ "HH:MM"。语义同到达。"""
    match = _RE_DEPART_EXPLICIT.search(message)
    if match:
        return _normalize_time_point(match.group(1) or "", match.group(2),
                                     match.group(3), match.group(4))
    match = _RE_DEPART_VAGUE.search(message)
    if match:
        return _PERIOD_DEFAULT[match.group(1)]
    return None


def detect_multi_city(message: str) -> list[str]:
    """多目的地检测（验收 #73）：返回主目的地之外的城市（消息出现序）。

    出局规则与 extract_destination 的消歧一致：否定/放弃语境的城市、
    「从A出发去B / A到B」路线对的出发地、「从A出发」无目的地的出发地。
    剩余 ≥2 个不同城市 = 多目的地诉求，第一个是主目的地，其余进返回值
    供 slot_filler 明示「当前支持单城市规划」。
    """
    text = message or ""
    if not text:
        return []
    route = _extract_route_city_pair(text)
    route_origin = route[0] if route else ""
    kept: list[str] = []
    for pos, city in _iter_city_hits(text):
        if city in kept:
            continue
        if city == route_origin:
            continue
        prefix = text[max(0, pos - 4):pos]
        if _RE_CITY_NEGATION.search(prefix):
            continue
        if re.search(rf"(?:从|由)\s*{_city_name_pattern(city)}\s*(?:出发|启程)",
                     text, re.IGNORECASE):
            continue
        kept.append(city)
    return kept[1:]


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


def extract_tier(message: str) -> str | None:
    """方案档位抽取（M3-e）：命中即返回，未提返回 None（保持既有值/默认）。"""
    for tier, keywords in TIER_KEYWORDS.items():
        if any(k in message for k in keywords):
            return tier
    return None


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
    return filter_city_names(names)


def extract_avoid(message: str) -> list[str]:
    """避雷清单 = 触发词捕获 ∪ 真实 POI 名录（"不要去鼓山"两种情况都能覆盖）。"""
    names = _extract_names(_RE_AVOID, message)
    for matched in _extract_names(_RE_AVOID, message):
        for poi_name in poi_seed.all_poi_names():
            if matched in poi_name or poi_name in matched:
                if poi_name not in names:
                    names.append(poi_name)
    return filter_city_names(names)


def extract_optional_go(
    message: str, destination: str = "", must_go: list[str] | None = None,
) -> list[str]:
    """软必去清单（验收 #67）：「有空再去/顺便去」触发词捕获 ∪ 名录命中。

    与 must_go 同一套捕获清洗与名录匹配；名录命中排除 must_go 已吸收的
    （「必去三坊七巷，有空再去鼓山」的三坊七巷不进软清单）；avoid 命中
    优先（「有空再去但别去鼓山」以 avoid 为准）。软必去能排就排，容量
    不足/修复时先于普通候选移除。
    """
    must_names = set(must_go or [])
    names = [p for p in poi_seed.all_poi_names()
             if p in message and p not in must_names]
    for name in _extract_names(_RE_OPTIONAL_GO, message, destination):
        if name and name not in names and name not in must_names:
            names.append(name)
    avoid = extract_avoid(message)
    if avoid:
        names = [n for n in names
                 if not any(n in a or a in n for a in avoid if a)]
    return filter_city_names(names)


def _explicit_total_party(message: str) -> int | None:
    """显式总人数（排除已被成人/儿童表达吸收的数字）。

    「大人两位」的「两位」会被通用人数正则误读成总人数 2（存量潜伏 bug，
    被 adults/children 拆分暴露）：凡与 adults/children 匹配段重叠的
    _RE_PARTY 命中一律跳过，把计数权交还给派生规则。
    """
    contaminated: list[tuple[int, int]] = []
    for pattern in (_RE_ADULTS, _RE_ADULTS_REV, _RE_CHILDREN, _RE_CHILDREN_REV):
        contaminated.extend(m.span() for m in pattern.finditer(message))

    def _absorbed(match: re.Match) -> bool:
        """命中段与成人/儿童表达段重叠 = 该数字已被拆分计数吸收。"""
        return any(not (match.end() <= s or match.start() >= e)
                   for s, e in contaminated)

    for match in _RE_PARTY.finditer(message):
        if _absorbed(match):
            continue
        value = _to_int(match.group(1))
        if value and value > 0:
            return value
    match = _RE_FAMILY_KOU.search(message)
    if match and not _absorbed(match):
        value = _to_int(match.group(1))
        if value and value > 0:
            return value
    # 同伴兜底同理：「大人两位带孩子一个」的「带孩子」已被 children=1 吸收，
    # 不再按同伴 guess +1——否则显式拆分被 guess 覆盖（实测踩过：返回 2≠3）。
    match = _RE_COMPANION.search(message)
    if match and not _absorbed(match):
        return 1 + _COMPANION_PLUS.get(match.group(1), 1)
    return None


def extract_fresh_brief(
    message: str, previous_destination: str = "",
) -> TravelBrief:
    """抽取本轮 fresh brief（纯函数，不做多轮合并——merge 归 RequirementService）。

    party_size 派生规则（Phase 2 adults/children）：显式总数（「3个人」
    「一家三口」，剔除被成人/儿童表达吸收的数字段）优先；无显式总数但
    有大人数时 party_size = adults + (children or 0)——「2个大人」= 2 人
    （D4 修复），「大人两位带孩子一个」= 3 人（总数漏计修复）；仅儿童
    显式时不动 party_size（回落默认，避免与同伴 guess 口径冲突）。
    adults/children 始终如实记录（即使显式总数优先）。
    """
    # 软必去触发词捕获先行（验收 #67）：「必去A，有空再去B」的 B 不得被
    # must_go 的名录命中吸收成硬必去——名录匹配是「名字在句子里就算」，
    # 不剔除就会把软承诺升级成硬必去。
    soft_trigger_names = _extract_names(_RE_OPTIONAL_GO, message)
    fresh = TravelBrief(
        destination=extract_destination(message, previous_destination),
        origin=extract_origin(message),
        days=extract_days(message),
        party_size=1,
        budget_cny=extract_budget(message),
        start_date=extract_start_date(message),
        arrival_time=extract_arrival_time(message) or "",
        departure_time=extract_departure_time(message) or "",
        preferences=extract_preferences(message),
        pace=extract_pace(message) or "moderate",
        tier=extract_tier(message) or "economy",
        diet=extract_diet(message),
        lodging=extract_lodging(message),
    )
    fresh.avoid = extract_avoid(message)
    fresh.must_go = [
        n for n in extract_must_go(message, fresh.destination, fresh.avoid)
        if not any(n in s or s in n for s in soft_trigger_names)
    ]
    fresh.optional_go = extract_optional_go(
        message, fresh.destination, must_go=fresh.must_go)
    adults, children = extract_adults_children(message)
    fresh.adults = adults
    fresh.children = children
    explicit_total = _explicit_total_party(message)
    if explicit_total is not None:
        fresh.party_size = explicit_total
    elif adults is not None:
        fresh.party_size = adults + (children or 0)
    return fresh


def build_clarification(brief: TravelBrief, user_message: str = "") -> str:
    """必填槽位缺失时的追问文案。

    user_message 用于识别「用户点名了不支持的城市」——此时明确告知原因，
    而不是让用户对着城市列表猜自己哪里答错了。
    P1-3：destination 缺失时附偏好推荐，让用户有「可以直接选」的起点。
    支持范围口径随数据源分叉（v3 P0-A）：live 模式 = 全国主要城市
    （识别名录 + 实时地图检索），seed 模式仍如实只报种子 3 城。
    """
    missing = brief.missing_slots()
    if not missing:
        return ""
    questions = [SLOT_QUESTIONS.get(s, s) for s in missing]
    lines = ["为了把行程排准，还需要确认："]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    from backend.config.travel import TRAVEL_POI_SOURCE

    unsupported = extract_unsupported_city(user_message) if user_message else ""
    if TRAVEL_POI_SOURCE == "seed":
        cities_line = "、".join(poi_seed.all_cities())
        if unsupported and "destination" in missing:
            lines.append(
                f"\n你提到的「{unsupported}」暂时无法规划（还没有当地的地点数据），"
                f"当前可规划的城市：{cities_line}"
            )
        else:
            lines.append(f"\n（当前可规划的城市：{cities_line}）")
    else:
        if unsupported and "destination" in missing:
            lines.append(
                f"\n你提到的「{unsupported}」暂时无法规划（暂不支持境外及"
                "该目的地），境内主要城市都可以试。"
            )
        else:
            lines.append(
                "\n（全国主要城市均可规划，地点信息来自实时地图检索）"
            )
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


class RequirementAgent:
    """Requirement Agent（v3 §3.1 ②）。

    自然语言需求理解 / 槽位抽取 / 缺失检测 / TripBrief 生成。无状态
    （state 读写归图节点编排），实例可进程级复用。模块级同名函数是其
    公开面（兼容 re-export 与单测直调），类方法统一委托，保证两条路径
    行为天然一致。
    """

    def extract_fresh_brief(
        self, message: str, previous_destination: str = "",
    ) -> TravelBrief:
        return extract_fresh_brief(message, previous_destination)

    def build_clarification(self, brief: TravelBrief, user_message: str = "") -> str:
        return build_clarification(brief, user_message)

    def detect_missing(self, brief: TravelBrief) -> tuple[str, ...]:
        """缺失必填槽检测（追问的唯一判定来源）。"""
        return brief.missing_slots()


__all__ = [
    "RequirementAgent",
    "build_clarification",
    "detect_multi_city",
    "extract_adults_children",
    "extract_arrival_time",
    "extract_avoid",
    "extract_budget",
    "extract_date_range_days",
    "extract_days",
    "extract_days_range",
    "extract_departure_time",
    "extract_destination",
    "extract_diet",
    "extract_fresh_brief",
    "extract_lodging",
    "extract_must_go",
    "extract_optional_go",
    "extract_party_size",
    "extract_past_date",
    "extract_pace",
    "extract_tier",
    "extract_preferences",
    "extract_start_date",
    "extract_unsupported_city",
    "extract_relative_date_expr",
    "extract_vague_time_expr",
    "party_size_source",
]
