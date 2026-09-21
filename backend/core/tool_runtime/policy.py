"""tool_runtime/policy.py — Tool Policy Registry（每 Tool 一份策略，配置化）

不要所有 Tool 用同一套 timeout / retry：rag.search 8s 零重试、
sql.query 5s、写操作零重试…… 默认值在本模块声明，运维可通过
环境变量覆盖（与项目既有 os.getenv 配置风格一致）：

  TOOL_POLICY_JSON  — JSON dict，按 tool key 覆盖任意字段，例：
        {"rag.search": {"timeout_ms": 15000}}
  其余全局参数见 config/settings.py（熔断/隔离舱/Deadline）。

原则：
  - 未注册的 Tool 走 DEFAULT_POLICY（保守：timeout 15s、retry 1），
    Skill 类级 default_timeout 仍可作为显式覆盖来源；
  - 在线请求重试必须保守（快速 jitter 退避，秒级指数退避只属于后台任务）；
  - 写操作 retries 强制 0（executor 层再兜底，双保险）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

from backend.core.tool_runtime.models import OperationType, ToolCriticality


@dataclass(frozen=True)
class ToolPolicy:
    timeout_ms: float = 15_000.0
    retries: int = 1                       # 保守：最多快速重试 1 次
    retry_backoff_ms: float = 100.0        # jitter 退避基准（100~300ms 抖动在 retry.py 加）
    circuit_breaker: bool = True
    cb_failure_threshold: int = 5          # 连续失败 N 次 → OPEN
    cb_recovery_seconds: float = 30.0      # OPEN 30s → HALF_OPEN
    cb_half_open_max: int = 2              # HALF_OPEN 同时允许的探测请求数
    bulkhead_limit: int = 20               # 并发隔离上限
    bulkhead_wait_ms: float = 300.0        # 拿不到槽位的最长等待，超过快速失败
    criticality: ToolCriticality = ToolCriticality.IMPORTANT
    operation_type: OperationType = OperationType.READ
    idempotent: bool = False
    fallback: str = ""                     # 降级策略名（描述性，供 trace/日志）

    def for_write(self) -> "ToolPolicy":
        """写操作视图：重试强制清零（timeout 后状态未知，盲重试有重复副作用风险）。"""
        return replace(self, retries=0, idempotent=self.idempotent)


# ── 默认策略注册表（key = capability 名，与 Skill.capabilities 对齐）──
DEFAULT_POLICIES: dict[str, ToolPolicy] = {
    # RAG：本地模式 pipeline.ask 含 LLM 合成；8s 是在线请求的合理上限，
    # 若生产常态超过（如长文档合成）用 TOOL_POLICY_JSON 调大 —— Deadline 仍兜底
    "rag.search": ToolPolicy(
        timeout_ms=8_000.0, retries=0, bulkhead_limit=20,
        criticality=ToolCriticality.IMPORTANT, fallback="rag_degraded",
    ),
    "sql.query": ToolPolicy(
        # ask_struct 含 LLM SQL 生成 + 执行，5s 不现实；15s + 零重试 + Deadline 兜底。
        # 若生产常态超过 15s 用 TOOL_POLICY_JSON 调大
        timeout_ms=15_000.0, retries=0, bulkhead_limit=20,
        criticality=ToolCriticality.IMPORTANT, fallback="sql_degraded",
    ),
    "report.generate": ToolPolicy(
        timeout_ms=20_000.0, retries=0, bulkhead_limit=10,
        criticality=ToolCriticality.IMPORTANT, fallback="rag_degraded",
    ),
    "web.search": ToolPolicy(
        timeout_ms=6_000.0, retries=1, bulkhead_limit=10,
        criticality=ToolCriticality.OPTIONAL, fallback="skip",
    ),
    "web.crawl": ToolPolicy(
        timeout_ms=15_000.0, retries=0, bulkhead_limit=5,
        criticality=ToolCriticality.OPTIONAL, fallback="skip",
    ),
    "product.enrichment": ToolPolicy(
        timeout_ms=3_000.0, retries=1, bulkhead_limit=10,
        criticality=ToolCriticality.OPTIONAL, fallback="skip",
    ),
    # 写操作：零自动重试 + 幂等键
    "refund.create": ToolPolicy(
        timeout_ms=8_000.0, retries=0, bulkhead_limit=10,
        criticality=ToolCriticality.REQUIRED, operation_type=OperationType.WRITE,
        idempotent=True, fallback="check_operation_status",
    ),
    "ticket.create": ToolPolicy(
        timeout_ms=8_000.0, retries=0, bulkhead_limit=10,
        criticality=ToolCriticality.REQUIRED, operation_type=OperationType.WRITE,
        idempotent=True, fallback="check_operation_status",
    ),
    "email.send": ToolPolicy(
        timeout_ms=10_000.0, retries=0, bulkhead_limit=5,
        criticality=ToolCriticality.IMPORTANT, operation_type=OperationType.WRITE,
        idempotent=True, fallback="check_operation_status",
    ),
}

# 未注册 Tool 的兜底策略（保守默认）
DEFAULT_POLICY = ToolPolicy()

# 写操作 capability 后缀启发（未显式注册时兜底判定）
_WRITE_SUFFIXES = (".create", ".send", ".update", ".cancel", ".delete", ".submit")

# 环境变量覆盖缓存（进程内一次解析）
_policy_cache: dict[str, ToolPolicy] | None = None


def _apply_env_overrides() -> dict[str, ToolPolicy]:
    """TOOL_POLICY_JSON → 按工具覆盖策略字段（深度只到字段级，非法值忽略并告警）。"""
    policies = dict(DEFAULT_POLICIES)
    raw = os.getenv("TOOL_POLICY_JSON", "").strip()
    if not raw:
        return policies
    try:
        overrides = json.loads(raw)
    except (ValueError, TypeError):
        from backend.shared.logger import logger
        logger.warning("[ToolPolicy] TOOL_POLICY_JSON 非法 JSON，忽略")
        return policies
    from dataclasses import fields as dc_fields
    valid = {f.name: f.type for f in dc_fields(ToolPolicy)}
    for key, patch in (overrides or {}).items():
        if not isinstance(patch, dict):
            continue
        base = policies.get(key, DEFAULT_POLICY)
        clean = {k: v for k, v in patch.items() if k in valid}
        # 枚举字段回填
        if "criticality" in clean:
            clean["criticality"] = ToolCriticality(str(clean["criticality"]))
        if "operation_type" in clean:
            clean["operation_type"] = OperationType(str(clean["operation_type"]))
        try:
            policies[key] = replace(base, **clean)
        except (TypeError, ValueError):
            from backend.shared.logger import logger
            logger.warning(f"[ToolPolicy] {key} 策略覆盖字段非法，忽略: {clean}")
    return policies


def get_policy(tool_key: str) -> ToolPolicy:
    """按 capability 名取策略；未注册走保守默认 + 写操作后缀启发。"""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = _apply_env_overrides()
    policy = _policy_cache.get(tool_key)
    if policy is not None:
        return policy
    if tool_key.endswith(_WRITE_SUFFIXES):
        return DEFAULT_POLICY.for_write()
    return DEFAULT_POLICY


def is_registered(tool_key: str) -> bool:
    """该 tool key 是否有显式注册的策略（区别于兜底默认）。"""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = _apply_env_overrides()
    return tool_key in _policy_cache


def reset_policy_cache() -> None:
    """测试钩子：清空环境变量覆盖缓存。"""
    global _policy_cache
    _policy_cache = None
