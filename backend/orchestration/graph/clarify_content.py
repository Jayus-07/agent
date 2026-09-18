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
  - 防循环：同一会话连续追问上限 1 次（clarify_allowed / mark_clarified），
    守卫故障时放行（fail-open，宁可多问不吞功能）。
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

    Returns:
        需要追问 → {"source", "question", "options", "handoff_available"}；
        不追问 → None（走原链路）。
    """
    if not REFUSAL_CLARIFY_ENABLED or not query:
        return None
    # 客服窗口锁域：不打断客服语境（强信号转出由 redirect_main 负责）
    if (domain_hint or "").strip().lower() in ("customer_service", "cs"):
        return None

    from backend.orchestration.graph.travel_prefilter import (
        travel_has_city,
        travel_signal_hits,
    )

    travel_hits = travel_signal_hits(query)
    if travel_hits and not travel_has_city(query):
        # 1 个信号词、无城市：差"目的地"这一个槽位就能进旅游域
        return {
            "source": "entry_travel_city",
            "question": "看起来您可能想规划行程——想去哪个城市呢？目前支持福州、厦门、杭州。",
            "options": list(_TRAVEL_CITY_OPTIONS),
            "handoff_available": False,
        }

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

    return {
        "source": "refusal_generic",
        "question": "目前知识库暂无相关资料。您可以试试这些业务，或换个问法描述您的需求：",
        "options": list(_GENERIC_OPTIONS),
        "handoff_available": False,
    }


# =====================================================
# 防循环守卫（会话级连续追问上限 1 次）
# =====================================================

_GUARD_CACHE_NAME = "clarify_guard"
_GUARD_TTL_SECONDS = 600  # 10 分钟内的连续追问才算"同一轮对话反复拒答"


def _get_guard_cache():
    """守卫缓存（模块级函数便于测试替换；Redis 不可用降级进程内缓存）。"""
    from backend.infra.cache import get_cache

    return get_cache(_GUARD_CACHE_NAME, ttl=_GUARD_TTL_SECONDS)


def clarify_allowed(session_id: str) -> bool:
    """该会话当前是否还允许追问（连续追问上限 1 次）。"""
    if not session_id:
        return True
    try:
        return not _get_guard_cache().get_json(f"clarified:{session_id}")
    except Exception as e:
        logger.warning(f"[ClarifyGuard] 读取守卫状态失败，放行追问: {e}")
        return True


def mark_clarified(session_id: str) -> None:
    """记录本会话已追问过一次（TTL 内不再追问，超时自动复位）。"""
    if not session_id:
        return
    try:
        _get_guard_cache().set_json(f"clarified:{session_id}", True,
                                    ttl=_GUARD_TTL_SECONDS)
    except Exception as e:
        logger.warning(f"[ClarifyGuard] 写入守卫状态失败: {e}")
