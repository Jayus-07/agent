"""input_guard — 用户问题输入门禁（Query Guard）

分层检测（不是每个问题都调 LLM）：
  L0 FormatChecker  格式检查（空/超长/超token/重复/异常Unicode），纯规则
  L1 RuleGuard      注入/有害/垃圾/问候/范围/敏感域，句式级规则
  L3 LLMGuard       仅处理规则层无法拍板的边界问题（配置开关，默认关闭）

用法：
    from backend.security.input_guard import guard_query
    result = guard_query(question, session_id=session_id)
    if result.action in (GuardAction.BLOCK, GuardAction.CLARIFY):
        return result.message  # 短路，不进入 Router/Planner
"""
from backend.security.input_guard.types import (
    GuardAction,
    GuardCategory,
    GuardResult,
    RiskLevel,
)
from backend.security.input_guard.guard import (
    InputGuard,
    get_input_guard,
    guard_query,
)

__all__ = [
    "GuardAction",
    "GuardCategory",
    "GuardResult",
    "RiskLevel",
    "InputGuard",
    "get_input_guard",
    "guard_query",
]
