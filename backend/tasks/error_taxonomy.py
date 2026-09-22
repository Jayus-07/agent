"""tasks/error_taxonomy.py — 任务运行时错误分类器（Phase2 Step2）。

职责：把 Worker 执行链路里的任意异常归一为带 retryable 语义的分类，
供 Retry 决策与 TaskState.error_type 落库使用。**不新增一套分类体系**，
全部复用既有资产：

- Provider 层五类：复用 ``infra/llm/error_taxonomy.py`` 的
  ``classify_model_error`` 纯函数与 ``ModelProviderError.error_type``
  （proxy 已在 fallback 耗尽后包装上抛，带 provider/model 归属）
- 协议层语义：对齐 ``shared/error_protocol.py``（INTERNAL_ERROR 可重试、
  INVALID_PARAM/PERMISSION_DENIED 不可重试等既有口径）

runtime 词表（超集＝规格要求全集）：
  timeout               runtime 层超时（SoftTimeLimit，进程活着）
  provider_timeout      provider 侧超时（LLM/Embedding/HTTP）
  rate_limited          限流（429）
  quota_exhausted       配额耗尽（403 insufficient_quota 等）
  auth_failed           鉴权失败（401/403 非配额）
  provider_error        其余 provider 错误（5xx/连接类）
  validation_error      参数/输入/文档结构类永久错误
  permission_denied     权限拒绝
  illegal_transition    状态机非法跳转
  internal_error        未归类的进程内错误（budget 内限次重试）
  worker_lost / cancelled 由 Recovery / 控制路径表达，不经本分类器。

Retry 口径（规格 §七）：
  retryable=True ：provider_timeout / rate_limited / provider_error(临时) /
                   timeout / internal_error（budget 限次）
  retryable=False：quota_exhausted / auth_failed / validation_error /
                   permission_denied / illegal_transition
"""
from __future__ import annotations

from dataclasses import dataclass

from celery.exceptions import SoftTimeLimitExceeded

from backend.shared.logger import logger

#: runtime 错误类型全集（tasks.error_type 列的取值域；异常类名不再落库）
TASK_ERROR_TYPES = (
    "timeout",
    "provider_timeout",
    "rate_limited",
    "quota_exhausted",
    "auth_failed",
    "provider_error",
    "validation_error",
    "permission_denied",
    "illegal_transition",
    "internal_error",
)

_RETRYABLE_TYPES = frozenset({
    "timeout", "provider_timeout", "rate_limited", "provider_error",
    "internal_error",
})

#: source 维度（规格 §六）：runtime / provider / validation / security / worker
_SOURCE_BY_TYPE = {
    "timeout": "runtime",
    "provider_timeout": "provider",
    "rate_limited": "provider",
    "quota_exhausted": "provider",
    "auth_failed": "security",
    "provider_error": "provider",
    "validation_error": "validation",
    "permission_denied": "security",
    "illegal_transition": "runtime",
    "internal_error": "runtime",
}


@dataclass(frozen=True)
class TaskErrorClassification:
    """一次异常的运行时分类结果（retry 决策 + TaskState 落库口径）。"""

    error_type: str
    retryable: bool
    source: str
    provider: str = ""
    model: str = ""

    @property
    def retryable_by_budget(self) -> bool:
        """retryable 分类仍需配合 retry budget 判定，此属性仅供语义提示。"""
        return self.retryable


def classify_task_error(exc: BaseException) -> TaskErrorClassification:
    """异常 → 任务运行时分类（纯函数，永不抛错）。

    判定顺序：
    1. runtime 已知异常（SoftTimeLimit / 状态机非法跳转 / 权限 / 参数）
    2. ModelProviderError（proxy 已分类，直接采信并回退 classify 校正）
    3. 带,status_code 的原始 provider 异常 / 消息特征 → 复用
       ``classify_model_error`` 三通道
    4. 兜底 internal_error（retryable，受 retry budget 限次）
    """
    try:
        # 1) runtime 已知
        if isinstance(exc, SoftTimeLimitExceeded):
            return TaskErrorClassification(
                "timeout", True, _SOURCE_BY_TYPE["timeout"])
        if type(exc).__name__ == "IllegalTaskTransition":
            return TaskErrorClassification(
                "illegal_transition", False, _SOURCE_BY_TYPE["illegal_transition"])
        if isinstance(exc, PermissionError):
            return TaskErrorClassification(
                "permission_denied", False, _SOURCE_BY_TYPE["permission_denied"])

        # 2) provider 层已分类（ModelProviderError / 带 error_type 的包装异常）
        model_type = getattr(exc, "error_type", "")
        if model_type in ("quota_exhausted", "auth_failed", "rate_limited",
                          "timeout", "provider_error"):
            mapped = ("provider_timeout" if model_type == "timeout"
                      else model_type)
            return TaskErrorClassification(
                mapped, mapped in _RETRYABLE_TYPES, _SOURCE_BY_TYPE[mapped],
                provider=str(getattr(exc, "provider", "") or ""),
                model=str(getattr(exc, "model", "") or ""))

        # 3) 原始 provider 异常：三通道复用（status_code → 类名 → 消息）
        #    （Stub/测试异常带 status_code 或 openai 风格类名即可命中）
        mapped = _map_via_model_taxonomy(exc)
        if mapped is not None:
            return mapped

        # 4) 进程内常规异常
        if isinstance(exc, ValueError):
            return TaskErrorClassification(
                "validation_error", False, _SOURCE_BY_TYPE["validation_error"])
        if isinstance(exc, LookupError):
            # 任务行/资源不存在：重试无意义（查不到永远是查不到）
            return TaskErrorClassification(
                "internal_error", False, _SOURCE_BY_TYPE["internal_error"])
        return TaskErrorClassification(
            "internal_error", True, _SOURCE_BY_TYPE["internal_error"])
    except Exception:  # noqa: BLE001 — 分类失败不掩盖原始异常
        logger.debug("[ErrorTaxonomy] classify failed", exc_info=True)
        return TaskErrorClassification(
            "internal_error", True, _SOURCE_BY_TYPE["internal_error"])


def _map_via_model_taxonomy(exc: BaseException) -> TaskErrorClassification | None:
    """复用 provider 三通道分类；命中 4xx 参数类时细分 validation_error。

    **必须先过 provider 特征门**：classify_model_error 对任意异常都有
    provider_error 兜底（从不拒绝），直接套用会把普通进程内异常
    （ValueError/LookupError...）误分类为可重试的 provider_error。
    只有带 status_code 或类名/消息含 provider 层特征的异常才进入三通道。
    """
    status = getattr(exc, "status_code", None)
    try:
        has_status = status is not None
    except Exception:  # noqa: BLE001
        has_status = False

    name = type(exc).__name__.lower()
    msg = ""
    try:
        msg = str(exc).lower()
    except Exception:  # noqa: BLE001
        msg = ""
    if not has_status and not any(
            marker in name or marker in msg for marker in _PROVIDER_MARKERS):
        return None

    from backend.infra.llm.error_taxonomy import MODEL_ERROR_TYPES

    raw = None
    try:
        from backend.infra.llm.error_taxonomy import classify_model_error

        raw = classify_model_error(exc)
    except Exception:  # noqa: BLE001
        return None
    if raw not in MODEL_ERROR_TYPES:
        return None

    try:
        status_i = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_i = None

    # 400/404/422 参数类错误在 provider_error 兜底里，细分回 validation_error
    if raw == "provider_error" and status_i in (400, 404, 422):
        return TaskErrorClassification(
            "validation_error", False, _SOURCE_BY_TYPE["validation_error"])
    mapped = "provider_timeout" if raw == "timeout" else raw
    return TaskErrorClassification(
        mapped, mapped in _RETRYABLE_TYPES, _SOURCE_BY_TYPE[mapped])


#: provider 层特征标记（类名或消息命中才进入三通道分类）
_PROVIDER_MARKERS = (
    "api", "http", "request", "response", "ratelimit", "rate limit",
    "quota", "timeout", "timed out", "connect", "embedding", "provider",
    "llm", "model", "upstream", "openai", "dashscope", "internal server",
    "service unavailable", "bad gateway", "too many requests",
)


def retryable_of(error_type: str) -> bool:
    """error_type → retryable（用于从 DB error_type 反查口径，永不抛错）。"""
    return error_type in _RETRYABLE_TYPES
