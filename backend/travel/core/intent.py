"""travel/core/intent.py — 会话意图分类（v3 §2.1，P0-A，纯规则零 LLM）

v3 裁决 #1 的代码化：**先判意图，再合并 TripBrief，最后检查下一步所需
条件**。没有意图层时，「丽江好玩吗」会抽到目的地、缺天数 → 被误追问
「玩几天」；「把第二天换成室内」指纹不变 → 旧行程被原样重报。意图先于
槽位条件，问答/探索/修改诉求才能拿到正确的出口。

优先级（v3 §2.1，自上而下首中即返回）：
  1. MODIFY    指向已有行程的改动（要求 has_itinerary，且抽取器没把它
               理解成结构化改动——后者走既有「指纹变化→重排」链）
  2. PLAN      明确规划动词（v3：显式动作词是用户主权，压过同句疑问）
  3. QUERY_DYNAMIC 带日期/现时的实时状态问题（开门/票价/天气/余票）
  4. QUERY_STATIC 背景与主观观点问题（好玩吗/值得去/怎么样）
  5. DISCOVER  还在选择目的地（去哪玩/推荐个城市）
  6. None      未分类 —— 走既有链路（补槽/重排/追问），不默认进灵感

与既有信号的关系：cancel/new_run（core/intent_signals.py）是**运行生命
周期**信号，优先级在本分类之外（上游已消费）；avoid_patch 是**结构化
改动**信号（slot_filler.is_avoid_patch_query），分类不出 MODIFY 时互不
干扰。本模块零 IO 零 LLM，可单测穷举。
"""
from __future__ import annotations

import enum
import re


class TravelIntent(str, enum.Enum):
    PLAN = "plan"
    MODIFY = "modify"
    QUERY_DYNAMIC = "query_dynamic"
    QUERY_STATIC = "query_static"
    DISCOVER = "discover"


# supervisor/reporter 按意图转问答出口的家族（PLAN 不在其中）
QUERY_INTENTS: frozenset[str] = frozenset({
    TravelIntent.QUERY_DYNAMIC.value,
    TravelIntent.QUERY_STATIC.value,
    TravelIntent.DISCOVER.value,
})

# ── 1) MODIFY：指向已有行程的逐条改动 ──────────────────────────────
# 「第X天 + 动词」是强信号；裸「换成/重排」要求 has_itinerary 才判。
# 显式天数表达（改成3天）不是 MODIFY —— 那是 days 槽位更新，走重排。
_RE_MODIFY = re.compile(
    r"第[一二三四五六七八九十\d]{1,3}天.{0,12}?(换成|改成|替换|调整为|去掉|删除|取消|移除)"
    r"|(?:换成|改成|替换成|调整为|重排|调整下?顺序|删掉|去掉|加一?天|多住一?天|少去)"
)
_RE_HAS_DAYS_EXPR = re.compile(r"(?<![第\d])\d{1,2}\s*天|(?<!第)[一二两三四五六七八九十]{1,3}\s*天")

# ── 2) PLAN：明确规划动词（用户主权）──
_RE_PLAN_VERB = re.compile(
    r"规划|排个?行程|排行程|做.{0,3}行程|做个?行程|行程单|制定|生成.{0,4}行程"
    r"|帮我.{0,4}(排|规划|做|安排)|安排下?|给我排"
)
# 「城市 + 天数」组合等价规划请求（「福州2天」）；负向断言排除「近/过去/
# 前」引导的业务时间窗（「近3天订单量」类），口径与 prefilter 一致。
_RE_DAYS_COUNT = re.compile(r"(?<![\d近过前])\d{1,2}\s*[天日](?!气)")

# ── 3) QUERY_DYNAMIC：带时间锚的实时状态 ──
_RE_DYNAMIC_FACT = re.compile(
    r"开门|关门|开放吗|闭馆|营业(吗|时间)|现在还有|下雨|天气|气温|温度"
    r"|票价|门票(多少钱|价格)?|余票|儿童票|免票|演出|表演|人多(吗|不)|排队"
)
_RE_TIME_ANCHOR = re.compile(
    r"今天|明天|后天|现在|当晚|本周|这周|下周|[12]?\d月[13]?\d[日号]?|周[一二三四五六日天]|国庆|春节"
)

# ── 4) QUERY_STATIC：背景/主观观点 ──
_RE_QUERY_STATIC = re.compile(
    r"好玩(吗|么)|值得去(吗|么)?|值得玩|怎么样|如何|好玩不|推荐(一下|个|几个)?吗"
    r"|有什么好玩|有什么好吃|好吃吗|美食(推荐|攻略|有哪些)|适合.{0,8}吗"
    r"|好玩的地方|值得一去"
)

# ── 5) DISCOVER：还在选目的地 ──
_RE_DISCOVER = re.compile(
    r"去哪玩|去哪儿玩|哪里好玩|哪好玩|去哪里好|推荐.{0,4}(城市|地方|目的地)"
    r"|目的地推荐|周边游|短途(游|旅行)|小众目的地|不知道去(哪|哪儿)"
)


def classify_intent(
    message: str,
    *,
    has_itinerary: bool = False,
    has_destination: bool = False,
) -> TravelIntent | None:
    """会话意图分类（纯函数，零 LLM；返回 None = 未分类走既有链路）。

    has_destination：消息里能抽出目的地时为 True（「城市+天数」组合只有
    在有目的地时才是规划信号）。
    """
    msg = (message or "").strip()
    if not msg:
        return None

    # 1) MODIFY：必须有已有行程可改；天数更新句（「改成3天」）不是逐条改单
    if has_itinerary and not _RE_HAS_DAYS_EXPR.search(msg):
        if _RE_MODIFY.search(msg):
            return TravelIntent.MODIFY

    # 2) PLAN：明确规划动词压过同句疑问（v3 §2.1 例：「把第二天换成室内，
    #    顺便查天气」主诉求是改；「丽江三天直接规划」主诉求是规划）
    if _RE_PLAN_VERB.search(msg):
        return TravelIntent.PLAN

    # 3) QUERY_DYNAMIC：实时状态问题（开门/票价/天气…）
    if _RE_DYNAMIC_FACT.search(msg):
        return TravelIntent.QUERY_DYNAMIC

    # 4) QUERY_STATIC：背景观点问题（「丽江三天好玩吗」不能凭「三天」启动规划）
    if _RE_QUERY_STATIC.search(msg):
        return TravelIntent.QUERY_STATIC

    # 5) DISCOVER：还在选目的地
    if _RE_DISCOVER.search(msg):
        return TravelIntent.DISCOVER

    # 简写的城市+天数仅在没有问答信号时启动规划。
    if has_destination and _RE_DAYS_COUNT.search(msg):
        return TravelIntent.PLAN

    return None


__all__ = [
    "TravelIntent",
    "QUERY_INTENTS",
    "classify_intent",
]
