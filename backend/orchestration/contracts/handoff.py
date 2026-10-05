"""handoff.py — 主图 → 专属域页 handoff 交接契约 V1（多域隔离收官 M1）

拍板口径（docs/superpowers/specs/2026-10-06-多域隔离收官实施计划.md §M1）：
  **转接 = 目标域 + 参数包 + 状态锚点 + 引导原因**。
  AI 助手不执行域规划，识别到域诉求时经 SSE ``handoff`` AUX 帧下发本契约，
  前端渲染引导卡带参跳转专属页；参数包在落地页只做「预填」，权威解析仍在
  各域 slot_filler（两处抽取必然分叉的老纪律不破——这里的数据永远不进执行链）。

设计约束：
  - 参数包按域声明 schema、extra=forbid（超域字段拒绝），必填最小化——
    全部字段可缺省，参数缺失由落地页的既有追问链兜底，不在这里硬造默认值；
  - 消费方（SSE 发射端 / 前端类型）共享同一份 JSON fixture 对齐
    （backend/tests/orchestration/test_handoff_contract.py ↔
     frontend/src/types/handoff.test.ts），防双端字段漂移；
  - 破坏性演进 = v: 2 并存期，禁止原地改字段语义。
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── 目标域（第四扇门选品页上线前，selection_funnel 落地页为规划中的
#    /selection-funnel 路由；契约先行，域枚举一步到位）────────────────
HandoffTargetDomain = Literal["travel", "customer_service", "selection_funnel"]

CONTRACT_VERSION: Literal[1] = 1


# ── 按域参数包 schema（必填最小化，extra=forbid）────────────────────


class TravelHandoffParams(BaseModel):
    """旅游页 brief 预填参数（destination/days 对应页面既有表单字段）。"""

    model_config = ConfigDict(extra="forbid")

    destination: str = ""
    days: Optional[int] = None
    party_size: Optional[int] = None
    budget_cny: Optional[float] = None
    must_go: list[str] = Field(default_factory=list)


class SelectionFunnelHandoffParams(BaseModel):
    """选品页预填参数（类目/平台，对应漏斗 brief 的两个槽位）。"""

    model_config = ConfigDict(extra="forbid")

    category: str = ""
    platform: str = ""


class CustomerServiceHandoffParams(BaseModel):
    """客服抽屉预填参数（原样带入用户最后一问，不做改写）。"""

    model_config = ConfigDict(extra="forbid")

    prefill_question: str = ""


_PARAMS_MODELS: dict[str, type[BaseModel]] = {
    "travel": TravelHandoffParams,
    "customer_service": CustomerServiceHandoffParams,
    "selection_funnel": SelectionFunnelHandoffParams,
}


class HandoffPayloadV1(BaseModel):
    """handoff 帧契约主体：v=1；未知目标域 / 超域参数在构造期即拒绝。"""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = CONTRACT_VERSION
    target_domain: HandoffTargetDomain
    # 引导原因（预留枚举演进；当前唯一生产取值 = 域诉求送专属页）
    reason: str = "domain_planning_request"
    params: dict = Field(default_factory=dict)
    # 引导话术（前端卡片主文案；后端下发保证旧前端在消息正文里也有引导）
    text: str = ""

    @model_validator(mode="after")
    def _validate_params_by_domain(self) -> "HandoffPayloadV1":
        model = _PARAMS_MODELS[self.target_domain]
        # extra=forbid：超出域 schema 的字段在这里抛 ValidationError
        self.params = model.model_validate(self.params).model_dump()
        return self


# ── 引导话术（后端下发；域归属语义在这里统一，前端不复制第二份）──────

GUIDE_TEXTS: dict[str, str] = {
    "travel": (
        "这是一次行程规划需求，主对话不直接生成完整行程。"
        "已把目的地、天数等整理进下方卡片，点击即可带参跳转「旅游规划」页，"
        "到那边我会实时检索景点、交通与预算并逐日排程。"
    ),
    "selection_funnel": (
        "智能选品是完整漏斗流程（导入 → 粗筛 → 核验 → 经济性 → 排序），"
        "请点击下方卡片进入「智能选品」页导入数据并运行，报告会在页面内生成。"
    ),
    "customer_service": (
        "这个问题更适合在智能客服窗口处理。点击下方卡片打开客服窗口，"
        "你的问题会自动带过去，由客服 Agent 与人工坐席接力处理。"
    ),
}


# ── 轻量参数抽取（只服务「引导卡预填」，不是域内权威解析）────────────
# 纪律与 travel_prefilter 同源：预过滤层不越权做执行用抽取；这里的产出
# 永远只进 handoff.params，落地页仍由 slot_filler 二次确认。

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 「近3天/最近30天/过去7天」类业务时间窗排除口径与 travel_prefilter 一致；
# 「日」入字符类覆盖「两日游/三日游」措辞
_RE_DAYS = re.compile(
    r"(?<![\d近])(?<!过去)(?<!前)([一二两三四五六七八九十\d]{1,3})\s*[天日](?!气)")
_RE_N_PERSON = re.compile(r"(\d{1,2})\s*[个名位]?\s*人")
_RE_FAMILY = re.compile(r"一家([一二两三四五六七八九十\d]+)口")
# 同伴词 → 人数增量（爸/妈合并算 2：出现「爸妈」按父母两位计；
# 「朋友」用负向断言排除「女朋友/男朋友」——后者已在前面单列，避免双计）
_COMPANION_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"爸妈|父母|爹妈|双亲", 2),
    (r"男朋友|女朋友|女友|男友|老公|老婆|爱人|对象", 1),
    (r"(?<![女男])朋友|同事|同学|闺蜜|室友", 1),
    (r"孩子|小孩|女儿|儿子|宝宝", 1),
)
_RE_BUDGET = re.compile(
    r"(?:预算|花费|控制在?)(?:约|大概|不超过|以内)?\s*(\d{3,6})\s*(?:元|块)?"
    r"|(\d{3,6})\s*元(?:以内)?")
_RE_MUST_GO = re.compile(r"(?:必去|一定要去|必须去)(?:的|有)?[：:\s]*([^。，,！!？?；;\s]{2,30})")


def _cn_to_int(text: str) -> Optional[int]:
    """中文/数字混合数量转 int（十位组合：十五/二十/二十五）。"""
    text = text.strip()
    if text.isdigit():
        return int(text)
    if not text:
        return None
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_NUM.get(head, 1) if head else 1
        ones = _CN_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CN_NUM.get(text)
    return None


def extract_days(query: str) -> Optional[int]:
    """抽行程天数（「玩2天」「三天」「两日游」）；业务时间窗已排除。"""
    for m in _RE_DAYS.finditer(query or ""):
        days = _cn_to_int(m.group(1))
        if days and 1 <= days <= 30:
            return days
    return None


def extract_party_size(query: str) -> Optional[int]:
    """抽出行人数：显式 N 人 > 一家 N 口 > 同伴词累加（含本人基准 1）。"""
    q = query or ""
    m = _RE_N_PERSON.search(q)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 20 else None
    m = _RE_FAMILY.search(q)
    if m:
        n = _cn_to_int(m.group(1))
        return n if n and 1 <= n <= 20 else None
    total = 1
    matched = False
    for pattern, add in _COMPANION_PATTERNS:
        if re.search(pattern, q):
            total += add
            matched = True
    return total if matched else None


def extract_budget_cny(query: str) -> Optional[float]:
    """抽预算（元）；500~1_000_000 之外的命中视为噪声丢弃。"""
    m = _RE_BUDGET.search(query or "")
    if not m:
        return None
    value = m.group(1) or m.group(2)
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if 500 <= amount <= 1_000_000 else None


def extract_destination(query: str) -> str:
    """首个城市命中（识别名录单一源 = requirement_agent 的城市名录）。"""
    try:
        from backend.travel.agents.requirement_agent import _iter_city_hits

        for _pos, city in _iter_city_hits(query or ""):
            return city
    except Exception:  # noqa: BLE001 — 名录不可用时目的地留空（落地页追问兜底）
        pass
    return ""


def extract_must_go(query: str) -> list[str]:
    """抽「必去」条目（顿号/和分隔），最多 5 条、单条 ≤ 20 字。"""
    items: list[str] = []
    for m in _RE_MUST_GO.finditer(query or ""):
        for part in re.split(r"[、，,和跟与]", m.group(1)):
            part = part.strip(" 的")
            if part and len(part) <= 20:
                items.append(part)
        if len(items) >= 5:
            break
    return items[:5]


def build_travel_params(query: str) -> dict:
    """「带爸妈福州玩2天」→ {destination:福州, days:2, party_size:3}。"""
    return {
        "destination": extract_destination(query),
        "days": extract_days(query),
        "party_size": extract_party_size(query),
        "budget_cny": extract_budget_cny(query),
        "must_go": extract_must_go(query),
    }


def build_handoff_payload(
    target_domain: str,
    query: str,
    *,
    reason: str = "domain_planning_request",
    extra_params: dict | None = None,
) -> HandoffPayloadV1:
    """从用户原话构造 handoff 契约体（SSE 发射端的唯一构造入口）。

    extra_params：调用方已知的结构化槽位（如选品 prefilter 场景的
    category/platform）优先于正则抽取。
    """
    params: dict = {}
    if target_domain == "travel":
        params = build_travel_params(query)
    elif target_domain == "selection_funnel":
        params = {
            "category": (extra_params or {}).get("category", ""),
            "platform": (extra_params or {}).get("platform", ""),
        }
    elif target_domain == "customer_service":
        params = {"prefill_question": (query or "").strip()}
    return HandoffPayloadV1(
        target_domain=target_domain,  # type: ignore[arg-type]
        reason=reason,
        params=params,
        text=GUIDE_TEXTS.get(target_domain, ""),
    )
