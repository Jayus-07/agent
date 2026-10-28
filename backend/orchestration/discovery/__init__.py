"""discovery — 管理端 Tool Discovery（决策 #18，B1）

只读候选解析 + 策略存取，零运行时改动：本包不接入 /agent 路由链路，
仅供管理端模拟器与策略 API 使用。
"""
from backend.orchestration.discovery.service import (
    DEFAULT_POLICY,
    load_active_policy,
    resolve_candidates,
    upsert_policy,
)

__all__ = ["DEFAULT_POLICY", "load_active_policy", "resolve_candidates", "upsert_policy"]
