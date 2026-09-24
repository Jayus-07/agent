"""travel/commerce/health.py — Commerce Health 组件（STOP K6，任务书 §二十）

语义沿用 Provider Health 冻结模型（healthy/degraded/disabled）：
  healthy   模式=fake（执行流可用）——**注意**：fake 是测试数据源，
            healthy 指链路可用，不代表真实供给；
  degraded  模式=live 但配额软停/降级中（真实供应商接入后生效）；
  disabled  模式=off（默认）或 live 未实现适配器（配置选择/未接入，非事故）。

**非 required**（沿用「Travel 域全部 Provider required=false」冻结口径）：
Commerce 任何故障都不得把整体 /health 拖成 unhealthy——本组件只如实呈现。
"""
from __future__ import annotations


def commerce_health() -> dict[str, str]:
    """travel_commerce 健康快照（软失败，任何探测异常按 degraded）。"""
    out: dict[str, str] = {"hotel": "disabled", "flight": "disabled"}
    try:
        from backend.config.travel_commerce import provider_mode

        mode = provider_mode()
        if mode == "fake":
            out["hotel"] = "healthy"
            out["flight"] = "healthy"
        elif mode == "live":
            # 真实适配器未实现（BLOCKED_BY_EXTERNAL_PROVIDER）：
            # 如实呈现 degraded（配置声明了 live 但无实现），而非谎报 disabled
            out["hotel"] = "degraded"
            out["flight"] = "degraded"
        # off → 保持 disabled
        return out
    except Exception:  # noqa: BLE001
        return {"hotel": "degraded", "flight": "degraded"}
