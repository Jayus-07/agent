"""providers/travel/live/health.py — Provider Health 组件（STOP J9 §98）

语义（G 派生自 §98 要求）：
  healthy    配置齐备且未被熔断/软停
  degraded   配置齐备但熔断开路中或日预算软停（非关键 Provider 降级不拖垮
             整体 /health——整体仍 ok，本组件如实呈现）
  disabled   未配置 Key / 开关关闭（配置选择，非事故）

required Provider（当前无一，全部 false）：凭据缺失时才允许 fail-closed
（T24/T25 语义；J0 冻结：Travel 域全部 Provider required=false）。
"""
from __future__ import annotations


def provider_health() -> dict[str, str]:
    """travel Provider 健康快照（软失败，任何探测异常按 degraded）。"""
    out: dict[str, str] = {}

    # tencent:lbs（place + route 共用传输层）
    try:
        from backend.config.map import is_live_map_enabled
        from backend.infra.circuit_breaker import State
        from backend.infra.http.tencent_lbs import _get_lbs_breaker
        from backend.providers.travel.live.quota import current_usage, daily_budget

        if not is_live_map_enabled():
            out["tencent_lbs"] = "disabled"
        else:
            breaker = _get_lbs_breaker()
            if breaker.state == State.OPEN:
                out["tencent_lbs"] = "degraded"
            elif daily_budget("tencent:lbs") > 0 and (
                    current_usage("tencent:lbs") >= daily_budget("tencent:lbs")):
                out["tencent_lbs"] = "degraded"
            else:
                out["tencent_lbs"] = "healthy"
    except Exception:  # noqa: BLE001
        out["tencent_lbs"] = "degraded"

    # weather（同一传输层，独立开关）
    try:
        from backend.config.travel import TRAVEL_WEATHER_ENABLED
        from backend.config.map import is_configured

        if not TRAVEL_WEATHER_ENABLED:
            out["weather"] = "disabled"
        else:
            out["weather"] = "healthy" if is_configured() else "degraded"
    except Exception:  # noqa: BLE001
        out["weather"] = "degraded"

    # qweather：天气备用源（配置即参与 fallback；无独立网络探测，
    # Key 缺失 = disabled 是配置选择非事故）
    try:
        from backend.config.map import is_qweather_configured

        out["qweather_backup"] = "healthy" if is_qweather_configured() else "disabled"
    except Exception:  # noqa: BLE001
        out["qweather_backup"] = "degraded"

    # ticket：契约冻结、无真实适配器（J0-6 决策 B）
    out["ticket"] = "disabled"

    return out
