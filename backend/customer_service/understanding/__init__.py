"""customer_service/understanding/ — CSUnderstanding 统一理解层（P1 步骤 3）

规划稿 §五：原文只用于审计和展示；路由、检索、实体提取使用规范化文本。
订单号、物流号、金额、邮箱和手机号不得被纠错器改写（0 改写原则）。

事实源复用（G2）：
- 规范化：security/input_guard/normalize.normalize_query（唯一规范化入口）
- 意图画像：customer_service/router/intents.INTENT_PROFILES
- 域枚举：customer_service/router/types

本包只做「理解」（纯函数、零 IO、零 LLM）；接线 router_node 属下一增量。
"""
from backend.customer_service.understanding.service import build_understanding
from backend.customer_service.understanding.types import (
    CSUnderstanding,
    DecisionLayer,
    EntitySpan,
    EntityType,
    NextAction,
    Sentiment,
    Urgency,
)

__all__ = [
    "build_understanding",
    "CSUnderstanding",
    "EntitySpan",
    "EntityType",
    "NextAction",
    "Sentiment",
    "Urgency",
    "DecisionLayer",
]
