"""travel/core/intent.py — 会话意图分类（v3 §2.1，P0-A，纯规则零 LLM）

v3 裁决 #1 的代码化：**先判意图，再合并 TripBrief，最后检查下一步所需
条件**。没有意图层时，「丽江好玩吗」会抽到目的地、缺天数 → 被误追问
「玩几天」；「把第二天换成室内」指纹不变 → 旧行程被原样重报。意图先于
槽位条件，问答/探索/修改诉求才能拿到正确的出口。

优先级（v3 §2.1，自上而下首中即返回）：
  0. SOCIAL / META / OUT_OF_SCOPE 轻交互与出域消息
  1. MODIFY    指向已有行程的改动（要求 has_itinerary，且抽取器没把它
               理解成结构化改动——后者走既有「指纹变化→重排」链）
  2. PLAN      明确规划动词（v3：显式动作词是用户主权，压过同句疑问）
  2.5 QUERY_TRANSIT 交通/车票查询（最快的车/高铁/怎么去）——只调 Tool 直出
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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictDecisionModel(BaseModel):
    """决策契约拒绝模型未声明字段，避免把自然语言输出变成执行指令。"""

    model_config = ConfigDict(extra="forbid")


class TravelChangeScope(_StrictDecisionModel):
    day_index: int | None = Field(default=None, ge=1)
    poi_id: str | None = Field(default=None, min_length=1, max_length=128)
    time_slot: Literal["morning", "afternoon", "evening", "all"] | None = None


class TravelChange(_StrictDecisionModel):
    op: Literal[
        "set_pace", "set_days", "set_budget", "set_destination",
        "set_preferences", "add_poi", "remove_poi", "replace_poi",
        "set_weather_condition",
    ]
    scope: TravelChangeScope = Field(default_factory=TravelChangeScope)
    value: str | int | float | list[str] | None = None
    target_name: str | None = Field(default=None, max_length=128)
    replacement_name: str | None = Field(default=None, max_length=128)
    evidence_text: str = Field(default="", max_length=500)


class TravelBriefCandidate(_StrictDecisionModel):
    """LLM 可建议的槽位；调用方还要过既有字段值校验与只填空槽合并。"""

    slot: Literal["destination", "days", "party_size", "pace", "preferences"]
    value: str | int | list[str]
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_text: str = Field(default="", max_length=500)


class TravelTaskParams(_StrictDecisionModel):
    """附加任务的受限参数面，不接受任意 Tool 名或自由执行参数。"""

    origin: str = Field(default="", max_length=100)
    destination: str = Field(default="", max_length=100)
    city: str = Field(default="", max_length=100)
    travel_date: str = Field(default="", max_length=32)
    day_index: int | None = Field(default=None, ge=1)


class TravelAdditionalTask(_StrictDecisionModel):
    task_id: str = Field(
        min_length=1, max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    type: Literal["query_train", "query_weather", "query_poi", "query_hotel"]
    params: TravelTaskParams = Field(default_factory=TravelTaskParams)


class TravelTurnDecision(_StrictDecisionModel):
    """单轮语义决策：主任务、受限改动、独立附加任务与追问状态。"""

    primary_action: Literal[
        "answer", "discover", "create_plan", "modify_plan", "replan_plan",
    ]
    changes: list[TravelChange] = Field(default_factory=list)
    additional_tasks: list[TravelAdditionalTask] = Field(
        default_factory=list, max_length=4)
    brief_candidates: list[TravelBriefCandidate] = Field(default_factory=list)
    needs_clarification: bool = False
    missing_fields: list[Literal[
        "destination", "days", "origin", "start_date", "target_day",
        "selected_poi_id", "replacement_poi", "desired_change",
    ]] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    parse_source: Literal[
        "structured", "rule", "llm", "rule_fallback", "clarification",
    ] = "rule"

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> "TravelTurnDecision":
        task_ids = [task.task_id for task in self.additional_tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("additional_tasks task_id 必须唯一")
        task_types = [task.type for task in self.additional_tasks]
        if len(task_types) != len(set(task_types)):
            raise ValueError("每轮每种附加任务最多执行一次")
        if self.primary_action in {"answer", "discover"} and self.changes:
            raise ValueError("answer/discover 决策不能携带行程修改")
        if self.needs_clarification and not self.missing_fields:
            raise ValueError("needs_clarification=true 时必须说明 missing_fields")
        return self


class TravelIntent(str, enum.Enum):
    PLAN = "plan"
    MODIFY = "modify"
    SOCIAL = "social"
    META = "meta"
    QUERY_DYNAMIC = "query_dynamic"
    QUERY_STATIC = "query_static"
    # 交通/车票查询（2026-10-08 #2）：「明天去厦门最快的车」类纯查询——
    # 只调 12306 查车次直出，不启动规划链（此前词表无交通词，被当成
    # 规划需求追问「玩几天」或把旧行程原样重吐）。
    QUERY_TRANSIT = "query_transit"
    DISCOVER = "discover"
    # M2 出域引导（2026-10-03）：明确指向非旅游域的诉求（订单/退款/写代码…）。
    # 优先级最高——这类词与行程改动几乎不会同句出现，先拦下避免 slot_filler
    # 硬解析成莫名行程；reporter 有对应引导出口（去 /agent）。
    OUT_OF_SCOPE = "out_of_scope"


# supervisor/reporter 按意图转问答出口的家族（PLAN 不在其中）
QUERY_INTENTS: frozenset[str] = frozenset({
    TravelIntent.QUERY_DYNAMIC.value,
    TravelIntent.QUERY_STATIC.value,
    TravelIntent.QUERY_TRANSIT.value,
    TravelIntent.DISCOVER.value,
})

# 这些意图只能走轻量回答，不得进入 POI / transit / weather / budget / risk
# 完整规划链。它们与 QUERY_INTENTS 分开，避免影响既有 LLM query 家族契约。
NON_PLANNING_INTENTS: frozenset[str] = QUERY_INTENTS | frozenset({
    TravelIntent.SOCIAL.value,
    TravelIntent.META.value,
    TravelIntent.OUT_OF_SCOPE.value,
})

# ── 0) OUT_OF_SCOPE：明确非旅游域诉求（M2 出域引导）──
# 只收「强域信号」词，宁漏勿滥：漏了走既有链路只是答得普通，
# 误判会把真行程诉求拦在门外。
_RE_OUT_OF_SCOPE = re.compile(
    r"订单|退款|退货|换货|发票|物流|快递|发货|库存|补货|上架"
    r"|账号|密码|登录|注册|实名|绑卡|支付失败"
    r"|写代码|编程|数据库|\bSQL\b|报表|考勤|工资|社保|报销"
)

# 轻量社交/元问题必须在槽位抽取前完成语义裁决；使用整句匹配避免把
# 「好吃吗」「好玩吗」等旅游问题误判成「好的」。
_RE_SOCIAL = re.compile(
    r"^(?:谢谢(?:你)?|多谢|感谢(?:你)?|辛苦了?|好的?|好哒|明白了?|"
    r"了解了?|收到|懂了|谢了|嗯嗯?|行了?|可以了?|ok)[，。！!？?、\s]*$",
    re.IGNORECASE,
)
_RE_META = re.compile(
    r"你.*(?:是|属于).*(?:模型|机器人|人工智能|AI)|"
    r"(?:你|您)?(?:能|可以|会)(?:够)?做什么|"
    r"你是谁|你是什么模型|你的能力是什么|"
    r"(?:记住|以后|长期|平时|一般).{0,20}(?:喜欢|偏好|倾向|慢节奏|"
    r"轻松|历史文化|人文|美食|不吃辣|素食)",
    re.IGNORECASE,
)

# ── 1) MODIFY：指向已有行程的逐条改动 ──────────────────────────────
# 「第X天 + 动词」是强信号；裸「换成/重排」要求 has_itinerary 才判。
# 显式天数表达（改成3天）不是 MODIFY —— 那是 days 槽位更新，走重排。
_RE_MODIFY = re.compile(
    r"第[一二三四五六七八九十\d]{1,3}天.{0,16}?(换成|改成|替换|调整为|去掉|删除|取消|移除|别|不要|不想|太赶|太满|早点结束|早些结束|加一个|增加|添加|必须同时安排)"
    r"|(?:换成|改成|替换成|调整为|重排|调整下?顺序|删掉|去掉|加一?天|多住一?天|少去|加一个|增加|添加)"
    r"|(?:这个|那个|选中的|刚才的?)(?:景点|地方).{0,8}(?:换掉|替换|改掉|删掉)"
)
_RE_HAS_DAYS_EXPR = re.compile(r"(?<![第\d])\d{1,2}\s*天|(?<!第)[一二两三四五六七八九十]{1,3}\s*天")

# ── 2) PLAN：明确规划动词（用户主权）──
_RE_PLAN_VERB = re.compile(
    r"规划|排个?行程|排行程|做.{0,3}行程|做个?行程|行程单|制定|生成.{0,4}行程"
    r"|帮我.{0,4}(排|规划|做|安排)|安排下?|给我排"
)
# 「城市 + 天数」组合等价规划请求（「福州2天」）；负向断言排除「近/过去/
# 前」引导的业务时间窗（「近3天订单量」类），口径与 prefilter 一致。
_RE_DAYS_COUNT = re.compile(
    r"(?<![\d近过前第一二两三四五六七八九十])"
    r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[天日](?!气)"
)

# ── 2.5) QUERY_TRANSIT：交通/车票查询 ──
# 只收交通名词与出行方式问法，不收裸「票/坐」（防「门票」「演出票」误伤——
# 那是 QUERY_DYNAMIC 的景区语义）。放在 PLAN 之后（「帮我规划…顺便看高铁」
# 主诉求是规划）、QUERY_DYNAMIC 之前（「高铁票价多少钱」按车票查询处理，
# 不落罐头话术）。
_RE_TRANSIT_QUERY = re.compile(
    r"车票|高铁|火车|动车|城际|班车|大巴|客运|航班|机票|飞机"
    r"|(?:最快|最早|最晚|最便宜|首班|末班).{0,2}?(?:的车|车次|班次|一?班)"
    r"|怎么去|怎么走|坐车|乘车"
)

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

    # 0) OUT_OF_SCOPE：非旅游域强信号词首中即拦（M2 出域引导）
    if _RE_OUT_OF_SCOPE.search(msg):
        return TravelIntent.OUT_OF_SCOPE

    # SOCIAL / META：已有行程也必须在图入口轻量收尾。
    if _RE_SOCIAL.fullmatch(msg):
        return TravelIntent.SOCIAL
    if _RE_META.search(msg):
        return TravelIntent.META

    # 1) MODIFY：必须有已有行程可改；天数更新句（「改成3天」）不是逐条改单
    if has_itinerary and not _RE_HAS_DAYS_EXPR.search(msg):
        if _RE_MODIFY.search(msg):
            return TravelIntent.MODIFY

    # 2) PLAN：明确规划动词压过同句疑问（v3 §2.1 例：「把第二天换成室内，
    #    顺便查天气」主诉求是改；「丽江三天直接规划」主诉求是规划）
    if _RE_PLAN_VERB.search(msg):
        return TravelIntent.PLAN

    # 2.5) QUERY_TRANSIT：交通/车票查询（「明天去厦门最快的车」）
    if _RE_TRANSIT_QUERY.search(msg):
        return TravelIntent.QUERY_TRANSIT

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
    "NON_PLANNING_INTENTS",
    "classify_intent",
]
