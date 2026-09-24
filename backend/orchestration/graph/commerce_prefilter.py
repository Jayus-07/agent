"""commerce_prefilter.py — Router 内的商务域预过滤（STOP K6，与 travel_prefilter 同层）

职责边界（K0 §1 定稿）：
  - 只回答「是不是**纯**酒店/机票查询诉求」，不抽取参数（域图 slot_filler
    职责——两处抽取必然分叉，与旅游域同纪律）
  - 行程信号词在场 → 让路旅游域（「行程里想住方便的」是规划诉求不是库存查询）
  - CS 售后词在场 → 让路（机票订单/退票是客服诉求；且 CS prefilter 在前，
    本层负向词表是第二道保险）
  - 任何异常向上抛，由 router_node 兜底回退主 Router（与既有 prefilter 契约一致）
"""
from __future__ import annotations

from backend.shared.logger import logger


def is_commerce_request(query: str) -> str | None:
    """纯商务意图判定（复用 extract.detect_commerce_intent——单一事实源，
    prefilter 与域图 slot_filler 的意图口径必然一致）。"""
    from backend.travel.commerce.extract import detect_commerce_intent

    return detect_commerce_intent(query)


def try_commerce_prefilter(query: str, state: dict) -> dict | None:
    """商务域预过滤。

    Returns:
        命中 → 主图 state 更新 dict（route_mode="travel_commerce"）；
        未命中 / 开关关闭 / 判定异常 → None（继续后续链路）。
    """
    try:
        from backend.config.travel_commerce import is_commerce_enabled

        if not is_commerce_enabled():
            return None
    except Exception:
        return None

    intent = is_commerce_request(query)
    if not intent:
        return None

    logger.info("[CommercePrefilter] 商务域命中: type=%s query=%s...",
                intent, query[:60])

    # 与 try_travel_prefilter 同构的最小输出：**不引入自定义 context 键**
    # ——主图 OrchestratorState 的 updates 流会剥离未登记键（travel_context
    # 漏登记即在案教训），Commerce 域图只消费 question/session_id 等
    # 既有键，无需透传任何新键
    return {
        "route_decision": None,
        "route_mode": "travel_commerce",
    }
