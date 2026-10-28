"""discovery — Tool Discovery 策略与候选解析（决策 #18）

B1：管理端只读 DiscoveryService（策略表 + 模拟器）；
B2：运行链接入——selector 与管理端共用同一策略事实源
（load_policy_for_runtime，带进程内 TTL 缓存）。
"""
from backend.orchestration.discovery.service import (
    DEFAULT_POLICY,
    DiscoveryPolicy,
    load_active_policy,
    load_policy_for_runtime,
    resolve_candidates,
    upsert_policy,
)

__all__ = [
    "DEFAULT_POLICY",
    "DiscoveryPolicy",
    "load_active_policy",
    "load_policy_for_runtime",
    "resolve_candidates",
    "upsert_policy",
]
