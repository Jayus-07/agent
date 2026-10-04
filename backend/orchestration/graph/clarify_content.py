"""clarify_content.py — 拒答转追问的内容生成（纯函数 + 防循环守卫）

设计（2026-09-19）：把"本来要拒答"的输入转成业务化追问，分两层：
  - L1 入口弱命中（build_entry_clarify）：Router 预过滤层，纯正则 ~1ms，
    只对「差一个槽位就能进域」的输入追问（如 1 个旅游信号词但没说城市），
    短路不进主 Router，TTFT 几乎为零。
  - L2 拒答兜底（build_refusal_clarify）：reporter/cs_graph_node 判定拒答后，
    给通用业务导航或定向选项。

硬约束：
  - 选项文案 = 用户话术：前端点选项即原样重发该文案（ChatView onSelect →
    send(label)），因此每条选项必须能命中对应域的预过滤/规则，由单测保证。
  - 判定全部复用既有 prefilter 信号件（travel_signal_hits / poi_seed 城市表 /
    选品类目暗示），不新增抽取逻辑——预过滤"不越权抽取"的契约不破。
  - 客服域锁（domain_hint=customer_service）下不做 L1 追问（客服窗口不被
    业务追问打断）；L2 客服语境给 CS 定向选项 + 转人工（handoff_available）。
  - 防循环（2026-10-03 企业口径重设计）：①同一问题原文去重——同问在窗口内
    不重复追问，换个问法允许再问；②会话封顶——滑动窗口内最多 N 次，超过
    后降级为裸拒答。计数走缓存原子自增（跨进程一致，故障 fail-open），
    替代旧版「会话 10 分钟一次性」（旧语义把防循环做成了会话级一次性，
    同句重问行为翻转：第一次给卡、重问裸拒——实测 2026-10-03 会话复现）。
"""
from __future__ import annotations

from backend.config import REFUSAL_CLARIFY_ENABLED
from backend.shared.logger import logger

# =====================================================
# 选项文案（用户话术，点击即重发）
# =====================================================
# 每条都要过对应域的 is_*_request / CS 规则——test_clarify_flow.py 有断言。

_TRAVEL_CITY_OPTIONS = tuple(
    f"规划{city}2天的行程" for city in ("福州", "厦门", "杭州")
)

# P0 无真实类目源，选品选项用种子示例；P1 接类目目录后替换
_SELECTION_OPTIONS = (
    "给宠物零食做一次智能选品",
    "给数码配件做一次智能选品",
)

# 通用业务导航（无倾向兜底）：旅游 / 选品 / 数据查询
_GENERIC_OPTIONS = (
    "帮我规划一份旅游行程",
    "我想做商品智能选品",
    "查一下经营数据",
)

# 客服语境选项（CS 域规则可命中）
_CS_OPTIONS = (
    "查询订单状态",
    "怎么申请退货退款",
    "我要投诉",
)

# SQL 空结果定向选项：用户话术，点击即重发。每条都必须被 RuleRouter 以
# 强信号（confidence≥0.85）直拍 sql.query——由 test_clarify_flow 逐条断言，
# 文案改动先跑测试。话术构词刻意覆盖 sql.query 的 rule_keywords
# （统计/本月/上月/数量/金额/多少），不依赖向量层兜底。
_SQL_EMPTY_OPTIONS = (
    "统计一下本月的销售金额",
    "统计一下上月的订单数量",
    "统计一下当前库存数量有多少",
)


# 追问卡片的伴随短文案：卡片（clarification 事件）承担选项交互，正文只
# 如实告知"没直接处理"，不做静默替换。runner guard 拦截与 reporter L1 共用。
CLARIFY_STANDALONE_TEXT = (
    "这条请求我暂时无法直接处理。请从下方选项中选择，或换个说法描述您的需求。"
)


# =====================================================
# L1：入口弱命中追问
# =====================================================

def build_entry_clarify(query: str, domain_hint: str) -> dict | None:
    """Router 预过滤层的弱命中追问判定。

    2026-09-22 路由入口重构：**移除旅游分支**——「帮我规划个行程」这类
    缺目的地的请求现在直接进旅游域图，由 brief 节点（slot_filler）在域内
    追问目的地/天数，不再被全局追问卡拦截（参数缺失由域内处理）。
    仅保留选品类目暗示分支。

    Returns:
        需要追问 → {"source", "question", "options", "handoff_available"}；
        不追问 → None（走原链路）。
    """
    if not REFUSAL_CLARIFY_ENABLED or not query:
        return None
    # 客服窗口锁域：不打断客服语境（强信号转出由 redirect_main 负责）
    if (domain_hint or "").strip().lower() in ("customer_service", "cs"):
        return None

    from backend.orchestration.graph.selection_funnel_prefilter import (
        selection_category_hint,
    )

    if selection_category_hint(query):
        # 提到品类/类目但没有选品动词（选品预过滤不会命中）：差"意图"槽位
        return {
            "source": "entry_selection_category",
            "question": "您想对这个品类做什么？可以先从智能选品开始。",
            "options": list(_SELECTION_OPTIONS),
            "handoff_available": False,
        }

    return None


# =====================================================
# L2：拒答后的兜底追问
# =====================================================

def build_refusal_clarify(query: str, domain_hint: str) -> dict:
    """拒答确认后的追问内容（总开关关闭时也返回内容，由调用方决定是否使用）。

    定向优先：从 query 里识别业务倾向（复用预过滤信号，零 LLM），
    识别不到给通用业务导航。
    """
    is_cs = (domain_hint or "").strip().lower() in ("customer_service", "cs")
    if is_cs:
        return {
            "source": "refusal_cs",
            "question": "为了更快帮您处理，请补充一下您的诉求：",
            "options": list(_CS_OPTIONS),
            "handoff_available": True,
        }

    from backend.orchestration.graph.selection_funnel_prefilter import (
        selection_category_hint,
        selection_signal_hits,
    )
    from backend.orchestration.graph.travel_prefilter import (
        travel_has_city,
        travel_signal_hits,
    )
    from backend.orchestration.router.rule_router import sql_lean_hits

    if travel_signal_hits(query) or travel_has_city(query):
        return {
            "source": "refusal_travel_lean",
            "question": "目前知识库暂无相关资料。看起来您可能想规划行程——目前支持福州、厦门、杭州。",
            "options": list(_TRAVEL_CITY_OPTIONS),
            "handoff_available": False,
        }

    if selection_signal_hits(query) or selection_category_hint(query):
        return {
            "source": "refusal_selection_lean",
            "question": "目前知识库暂无相关资料。您是想做商品智能选品吗？",
            "options": list(_SELECTION_OPTIONS),
            "handoff_available": False,
        }

    # SQL 倾向（2026-10-03 实机缺口修复）：有数据诉求词但缺指标/时间槽位，
    # 与旅游/选品同层的定向追问——实机验证发现这类查询全新会话会被向量
    # 路由拦成低置信 clarify，SQL 不执行，槽位卡必须挂在拒答兜底才触达。
    # 用宽词表（sql_lean_hits）不用路由窄词表：「查一下经营数据」窄表命中 0。
    if sql_lean_hits(query) >= 1:
        return build_sql_empty_clarify(query, executed=False)

    return {
        "source": "refusal_generic",
        "question": "目前知识库暂无相关资料。您可以试试这些业务，或换个问法描述您的需求：",
        "options": list(_GENERIC_OPTIONS),
        "handoff_available": False,
    }


def build_sql_empty_clarify(query: str, *, executed: bool = False) -> dict:
    """SQL 倾向的定向追问卡（时间范围/常用指标槽位）。

    两个阶段同一选项集、不同引导语：
      - executed=True：SQL 真执行后空结果（查不到）——「没有查到相关数据」；
      - executed=False：路由层拒答兜底识别到 SQL 倾向（还没执行）——
        「您想查哪方面的数据」。实机验证（2026-10-03）发现「查一下经营数据」
        在全新会话会被向量路由以低置信拦成 clarify，SQL 根本不执行——
        槽位卡必须也挂在路由层拒答兜底上才能真正触达。

    指标话术是策展短列表（与本模块其他选项卡同一纪律：文案即话术、
    路由可达由单测强制），不做 schema 注释派生（列注释是 DB 元数据
    风格，词料不足以生成用户话术）。
    """
    question = (
        "没有查到相关数据。您可以补充时间范围或换个说法，也可以先从这些常用指标查起："
        if executed
        else "您想查哪方面的数据？可以补充时间范围（如本月/上月），或先从这些常用指标查起："
    )
    return {
        "source": "refusal_sql_empty",
        "question": question,
        "options": list(_SQL_EMPTY_OPTIONS),
        "handoff_available": False,
    }


# =====================================================
# 防循环守卫（问题级去重 + 会话封顶，原子计数）
# =====================================================

_GUARD_CACHE_NAME = "clarify_guard"

# 会话封顶计数窗口：自本会话首次追问起 10 分钟内最多 _SESSION_CLARIFY_LIMIT 次
_SESSION_WINDOW_SECONDS = 600
_SESSION_CLARIFY_LIMIT = 2
# 同一问题去重窗口：同问 30 分钟内不再追问（换问法不受影响）
_QUESTION_DEDUP_TTL_SECONDS = 1800
# 追问卡选项暂存窗口：供下一轮「点击检测」比对新到消息是否为选项原文
_OFFERED_TTL_SECONDS = _SESSION_WINDOW_SECONDS


def _get_guard_cache():
    """守卫缓存（模块级函数便于测试替换；Redis 不可用降级进程内缓存）。"""
    from backend.infra.cache import get_cache

    return get_cache(_GUARD_CACHE_NAME, ttl=_SESSION_WINDOW_SECONDS)


def _normalize_question(question: str) -> str:
    """问题归一化：去全部空白 + 小写（同一句话的空白/大小写差异不算新问法）。"""
    return "".join((question or "").split()).lower()


def _qdup_key(session_id: str, question: str) -> str:
    """问题去重键：必须带会话维度——不同用户/会话问同一常见问题，
    各自都该拿到追问卡（测试 2026-10-03 抓过跨会话误伤缺陷）。"""
    import hashlib

    digest = hashlib.sha1(_normalize_question(question).encode("utf-8")).hexdigest()[:16]
    return f"qdup:{session_id}:{digest}"


def _session_count_key(session_id: str) -> str:
    return f"count:{session_id}"


def _offered_key(session_id: str) -> str:
    return f"offered:{session_id}"


def clarify_allowed(session_id: str, question: str = "") -> bool:
    """该会话对这个问题当前是否还允许追问。

    ① 同一问题（归一化后）在去重窗口内已追问过 → 不再追问；
    ② 会话窗口内追问次数达封顶 → 不再追问（降级为裸拒答）；
    ③ 缓存故障 fail-open（宁可多问一次，不吞掉拒答转追问）。
    """
    if not session_id:
        return True
    try:
        cache = _get_guard_cache()
        if question and cache.get_json(_qdup_key(session_id, question)):
            return False
        count = cache.get_json(_session_count_key(session_id)) or 0
        return int(count) < _SESSION_CLARIFY_LIMIT
    except Exception as e:
        logger.warning(f"[ClarifyGuard] 读取守卫状态失败，放行追问: {e}")
        return True


def mark_clarified(
    session_id: str,
    question: str = "",
    *,
    options: list[str] | tuple[str, ...] | None = None,
    source: str = "",
) -> None:
    """记录一次追问已发出（原子计数 + 问题去重 + 选项暂存供点击检测）。

    计数窗口自首次追问起算（incr 仅首建设 TTL），到点自动复位。
    全程软失败：守卫写坏不影响已发出的追问卡。
    """
    if not session_id:
        return
    try:
        cache = _get_guard_cache()
        cache.incr(_session_count_key(session_id), ttl=_SESSION_WINDOW_SECONDS)
        if question:
            cache.set_json(_qdup_key(session_id, question), True,
                           ttl=_QUESTION_DEDUP_TTL_SECONDS)
        if options:
            cache.set_json(
                _offered_key(session_id),
                {"source": source,
                 "options": [_normalize_question(o) for o in options if o]},
                ttl=_OFFERED_TTL_SECONDS,
            )
    except Exception as e:
        logger.warning(f"[ClarifyGuard] 写入守卫状态失败: {e}")


def consume_clarify_click(session_id: str, question: str) -> dict | None:
    """检测本轮消息是否为上一张追问卡的选项原文（点击即重发话术）。

    命中则消费掉暂存（一次点击只计一次）并返回 {"source": 卡片来源}；
    未命中返回 None（暂存保留，用户可能先自由发言再点选项）。
    每请求一次缓存读（热路径 +1 次 GET，TwoTier 有 30s L1），软失败返 None。
    """
    if not session_id or not question:
        return None
    try:
        cache = _get_guard_cache()
        offered = cache.get_json(_offered_key(session_id))
        if not offered:
            return None
        if _normalize_question(question) in (offered.get("options") or []):
            cache.delete(_offered_key(session_id))
            return {"source": offered.get("source", "")}
        return None
    except Exception as e:
        logger.debug(f"[ClarifyGuard] 点击检测失败（软降级）: {e}")
        return None
