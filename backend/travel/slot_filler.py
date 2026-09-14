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

from backend.shared.logger import logger
from backend.tools.travel import poi_seed
from backend.travel.graph_state import load_brief
from backend.travel.models.brief import (
    PACE_KEYWORDS,
    PREFERENCE_KEYWORDS,
    SLOT_QUESTIONS,
    TravelBrief,
)

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_CLASS = "一二两三四五六七八九十"

# 天数：阿拉伯数字或中文数字 + 天/日
_RE_DAYS = re.compile(rf"(\d{{1,2}}|[{_CN_CLASS}])\s*[天日]")
# 人数：XX人 / XX个人 / XX位
_RE_PARTY = re.compile(rf"(\d{{1,2}}|[{_CN_CLASS}])\s*(?:个)?[人位]")
# 预算：必须带货币单位（或「万」），否则 "3天" 会被当成钱
_RE_BUDGET_YUAN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块钱|块|rmb|人民币)", re.I)
_RE_BUDGET_WAN = re.compile(r"(?:预算|大概|差不多|总共)?\s*(\d+(?:\.\d+)?)\s*[万wW]")
# 日期：ISO 或「X月X日」
_RE_DATE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_RE_DATE_CN = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
# 必去 / 避雷 触发词后面的地名（不含标点与空白）
_RE_MUST_GO = re.compile(
    r"(?:必去|一定要去|必须去|想去|要去|打卡)\s*([^。，,；;！!？?\s]{2,12}"
    r"(?:[、和及][^。，,；;！!？?\s]{2,12})*)"
)
_RE_AVOID = re.compile(
    r"(?:不要去|不想去|避开|别去|不去)\s*([^。，,；;！!？?\s]{2,12}"
    r"(?:[、和及][^。，,；;！!？?\s]{2,12})*)"
)
_SPLIT_NAMES = re.compile(r"[、和及]")


def _to_int(token: str) -> int | None:
    """阿拉伯数字或中文数字 → int。"""
    if token.isdigit():
        return int(token)
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
    match = _RE_DAYS.search(message)
    if not match:
        return None
    value = _to_int(match.group(1))
    return value if value and value > 0 else None


def extract_party_size(message: str) -> int | None:
    match = _RE_PARTY.search(message)
    if not match:
        return None
    value = _to_int(match.group(1))
    return value if value and value > 0 else None


def extract_budget(message: str) -> float | None:
    """预算：优先认带「万」的说法，其次要求带货币单位。"""
    match = _RE_BUDGET_WAN.search(message)
    if match:
        return round(float(match.group(1)) * 10000, 2)
    match = _RE_BUDGET_YUAN.search(message)
    if match:
        return round(float(match.group(1)), 2)
    return None


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


def extract_pace(message: str) -> str | None:
    for pace, keywords in PACE_KEYWORDS.items():
        if any(k in message for k in keywords):
            return pace
    return None


def _extract_names(pattern: re.Pattern, message: str) -> list[str]:
    names: list[str] = []
    for match in pattern.finditer(message):
        for part in _SPLIT_NAMES.split(match.group(1)):
            name = part.strip()
            if 2 <= len(name) <= 12 and name not in names:
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
    for name in _extract_names(_RE_MUST_GO, message):
        if name and name != destination and name not in names:
            names.append(name)

    if avoid:
        names = [
            n for n in names
            if not any(n in a or a in n for a in avoid if a)
        ]
    return names


def extract_avoid(message: str) -> list[str]:
    """避雷清单 = 触发词捕获 ∪ 真实 POI 名录（"不要去鼓山"两种情况都能覆盖）。"""
    names = _extract_names(_RE_AVOID, message)
    for matched in _extract_names(_RE_AVOID, message):
        for poi_name in poi_seed.all_poi_names():
            if matched in poi_name or poi_name in matched:
                if poi_name not in names:
                    names.append(poi_name)
    return names


def merge_brief(previous: TravelBrief, fresh: TravelBrief) -> TravelBrief:
    """多轮合并：新抽取到的字段覆盖旧值，未抽到的保留旧值。

    只在「用户这一轮补充了信息」时生效（如追问后回答"3天"），
    不会因为这一轮没提预算就把之前说的预算清空。
    """
    merged = previous.model_copy()
    for field in ("destination", "origin"):
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
    if fresh.pace != "moderate":
        merged.pace = fresh.pace
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
    )
    fresh.avoid = extract_avoid(message)
    fresh.must_go = extract_must_go(message, fresh.destination, fresh.avoid)
    return merge_brief(previous, fresh) if previous else fresh


def build_clarification(brief: TravelBrief) -> str:
    """必填槽位缺失时的追问文案。"""
    missing = brief.missing_slots()
    if not missing:
        return ""
    questions = [SLOT_QUESTIONS.get(s, s) for s in missing]
    lines = ["为了把行程排准，还需要确认："]
    lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append(f"\n（当前可规划的城市：{'、'.join(poi_seed.all_cities())}）")
    return "\n".join(lines)


def slot_filler_node(state: dict) -> dict:
    """槽位节点：抽取 → 合并 → 判定需求是否变化 → 计算缺失槽位与追问文案。

    跨轮失效判定放在这里、而不是 supervisor：slot_filler 是每轮**唯一**会
    改写 brief 的地方，判断依据（新旧指纹）只有它同时拿得到。
    """
    from backend.travel.graph_state import brief_fingerprint, planning_reset

    message = state.get("user_message", "")
    previous = load_brief(state) if state.get("brief") else None

    brief = extract_brief(message, previous)
    missing = brief.missing_slots()
    clarification = build_clarification(brief)

    fingerprint = brief_fingerprint(brief)
    last_fingerprint = state.get("brief_fingerprint") or ""
    # 仅在「有上一轮指纹且不同」时失效：首次进入（无指纹）不算变化
    brief_changed = bool(last_fingerprint) and last_fingerprint != fingerprint

    logger.info(
        "[TravelSlotFiller] destination=%r days=%s missing=%s changed=%s",
        brief.destination, brief.days, missing, brief_changed,
    )

    update: dict = {
        "brief": brief.model_dump(),
        "brief_missing": missing,
        "clarifications": [clarification] if clarification else [],
        "brief_fingerprint": fingerprint,
        "stage": "slot",
    }

    if brief_changed:
        # 需求变了：旧行程作废，连同执行态一起清掉重新规划
        update.update(planning_reset())
        update["notes"] = ["需求已变化，已按新需求重新规划（上一版行程作废）"]
        logger.info("[TravelSlotFiller] 需求指纹变化 %s→%s，清空规划产物重排",
                    last_fingerprint, fingerprint)

    return update
