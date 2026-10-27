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
    # RAG：本地模式 pipeline.ask 含 LLM 合成。2026-09-22 依真实 trace 数据
    # （最近 7 次 rag.search：成功样本 3~15s，p50=8.2s，2 次成功落在 8-15s）
    # 将默认从 8s 放宽到 15s；retries=0，183.7s 事故（超时×重试叠加）
    # 不可能复发。仍可用 TOOL_POLICY_JSON 覆盖。
    # 2026-10-12 全链路审计 P0-1：15s 与 RAG 端到端常态结构性相撞——
    # 本地库 627 条 RAG 子 trace P90=16.3s / P95=19.1s，加前置排队 ~4s，
    # 10%+ 请求被 15s 线误杀：父判 timeout、rag-service 侧实际成功
    # （答案被丢弃、模型费用白烧、连续超时误开熔断）。放宽到
    # 45s = P95×2 + 排队余量；retries=0 / bulkhead 不变，超时×重试
    # 叠加形态仍不可能复发。workflow 路径（rag_agent）未接
    # RequestDeadline，本值即唯一约束；主图路径有效超时 = min(本值,
    # 剩余 workflow 预算 − reserve)，Deadline 联动调优见审计报告 §4。
    "rag.search": ToolPolicy(
        timeout_ms=45_000.0, retries=0, bulkhead_limit=20,
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
        # 官方目录请求最多8秒；通用两源各3秒+相关性校验，外层留余量。
        timeout_ms=10_000.0, retries=0, bulkhead_limit=10,
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


def _dynamic_mcp_policy(tool_key: str) -> ToolPolicy | None:
    """MCP 动态工具的策略派生层（ADR-0011 D4）。

    未注册的 `mcp.*` capability 从治理快照的 ToolSpec 派生 ToolPolicy：
    超时/重试/熔断/操作类型取审核治理配置；写操作强制零重试。
    快照无此工具（未审核/停用/漂移）返回 None——执行会被 guard 拦截，
    策略层无需再给默认值。
    """
    if not tool_key.startswith("mcp."):
        return None
    try:
        from backend.infra.mcp_registry import store as _mcp_store

        runtime = _mcp_store.get_runtime(tool_key)
        if runtime is None:
            return None
        spec = runtime.spec
        is_write = spec.operation.value != "read"
        policy = ToolPolicy(
            timeout_ms=float(spec.timeout_ms),
            retries=0 if is_write else spec.retry_max_attempts,
            circuit_breaker=spec.circuit_breaker_enabled,
            cb_failure_threshold=spec.circuit_failure_threshold,
            cb_recovery_seconds=spec.circuit_cooldown_seconds,
            operation_type=(
                OperationType.WRITE if is_write else OperationType.READ
            ),
        )
        return policy.for_write() if is_write else policy
    except Exception:  # noqa: BLE001 — 策略派生失败走保守默认
        return None


def get_policy(tool_key: str) -> ToolPolicy:
    """按 capability 名取策略；未注册走保守默认 + 写操作后缀启发。"""
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = _apply_env_overrides()
    policy = _policy_cache.get(tool_key)
    if policy is not None:
        return policy
    dynamic = _dynamic_mcp_policy(tool_key)
    if dynamic is not None:
        return dynamic
    if tool_key.endswith(_WRITE_SUFFIXES):
        return DEFAULT_POLICY.for_write()
    return DEFAULT_POLICY


def is_registered(tool_key: str) -> bool:
    """该 tool key 是否有显式注册的策略（区别于兜底默认）。

    MCP 动态工具有快照派生策略，视为已注册（base.py 据此决定
    是否用注册表超时替代类级默认）。
    """
    global _policy_cache
    if _policy_cache is None:
        _policy_cache = _apply_env_overrides()
    if tool_key in _policy_cache:
        return True
    return _dynamic_mcp_policy(tool_key) is not None


def reset_policy_cache() -> None:
    """测试钩子：清空环境变量覆盖缓存。"""
    global _policy_cache
    _policy_cache = None
