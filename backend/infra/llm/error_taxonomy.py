"""error_taxonomy.py — 模型层错误统一分类（路由专项 Step 3）

问题：provider/model 层失败（配额耗尽、鉴权失败、限流、超时、5xx）此前
以原始异常形态上抛，易被业务层包装成 RAG / SQL / Travel 失败，排障时
看不出是模型侧问题。

本模块在 proxy 层把异常归一为五类，随降级话术 / 告警 / ModelProviderError
透传：
  quota_exhausted  配额耗尽（403 insufficient_quota 等）
  auth_failed      鉴权失败（401 / 403 非配额类）
  rate_limited     限流（429 / rate limit）
  timeout          超时
  provider_error   其余 provider 侧错误（5xx / 参数 / 未知）

分类是**纯函数三通道**判定：status_code → 异常类名 → 消息文本。
"""
from __future__ import annotations

MODEL_ERROR_TYPES = (
    "quota_exhausted",
    "auth_failed",
    "rate_limited",
    "timeout",
    "provider_error",
)


class ModelProviderError(RuntimeError):
    """模型层错误的统一包装（无 fallback 且未开降级话术时上抛）。

    携带分类与归属信息，调用方据此可判断「这是模型问题」而不是把
    它当业务失败继续包装。origin 保留原始异常（__cause__ 同时设置）。
    """

    def __init__(self, message: str, *, error_type: str,
                 provider: str = "", model: str = "",
                 origin: BaseException | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.provider = provider
        self.model = model
        self.origin = origin


def classify_model_error(err: BaseException) -> str:
    """异常 → 五类统一错误类型（永不抛错）。"""
    try:
        status = getattr(err, "status_code", None)
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            status = None
        name = type(err).__name__.lower()
        msg = str(err).lower()

        # 0) 上下文超长（2026-10-01 STOP A）：语义上属「本次输入无法
        #    发送」，换模型/重试都无意义，由 proxy 短路成
        #    ContextBudgetExceededError。五类词表保持不变——该异常在
        #    到达本分类器之前已被拦截，这里是兜底探测入口。
        if is_context_length_error(err):
            return "provider_error"

        # 1) 限流（429 / rate limit 语义）
        if status == 429 or "ratelimit" in name or "rate limit" in msg \
                or "too many requests" in msg:
            return "rate_limited"

        # 2) 超时
        if "timeout" in name or "timedout" in name or "timed out" in msg:
            return "timeout"

        # 3) 403/401：先分配额耗尽，再判鉴权失败
        if status in (401, 403) or "permissiondenied" in name \
                or "authentication" in name:
            if "quota" in msg or "insufficient" in msg or "funds" in msg \
                    or "free tier" in msg or "balance" in msg:
                return "quota_exhausted"
            return "auth_failed"

        # 4) 配额语义（非 401/403 状态码携带，如 400 insufficient_quota）
        if "insufficient_quota" in msg or "quota exceeded" in msg \
                or "exceeded your current quota" in msg:
            return "quota_exhausted"

        # 5) 5xx / 其余 → provider_error
        if status is not None and 500 <= status < 600:
            return "provider_error"
        return "provider_error"
    except Exception:
        return "provider_error"


# 上下文超长的确定性特征（2026-10-01 STOP A）。只认显式语义标记，
# 不凭 400 状态码推断——普通参数 400 仍应走正常 provider 错误路径。
_CONTEXT_LENGTH_MARKERS = (
    "maximum context length",
    "context length",
    "context_length",
    "context window",
    "prompt is too long",
    "input is too long",
    "prompt too long",
    "input too long",
    "exceeds the maximum length",
    "input length exceeds",
    "requested too many tokens",
    "longer than the model's context",
)


def is_context_length_error(err: BaseException) -> bool:
    """provider 异常是否为「输入超出模型上下文窗口」类错误（永不抛错）。

    判定通道：异常类名（ContextLengthError 等）→ 消息文本显式标记。
    刻意保守：避免把普通 400 参数错误误判成超长（误判会让本可成功的
    请求被硬门禁拒绝）。
    """
    try:
        name = type(err).__name__.lower().replace("_", "")
        if "contextlength" in name:
            return True
        msg = str(err).lower()
        return any(m in msg for m in _CONTEXT_LENGTH_MARKERS)
    except Exception:
        return False
