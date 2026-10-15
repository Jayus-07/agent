"""customer_service/errors.py — 客服系统错误分类体系

设计原则（§14.2）：
  - 不吞没错误：禁止 except Exception: pass
  - 不泄露内部信息：user_message 与 message 分离
  - 可追踪：每个错误关联 trace_id + conversation_id
  - 可恢复：区分可重试和不可重试错误
  - 用户友好：返回中文友好提示，不暴露技术细节
"""
from __future__ import annotations


class CustomerServiceError(Exception):
    """客服系统基础错误"""

    retryable: bool = False

    def __init__(
        self,
        message: str,
        code: str = "CS_ERROR",
        user_message: str | None = None,
        *,
        conversation_id: str | None = None,
        trace_id: str | None = None,
    ):
        self.message = message
        self.code = code
        self.user_message = user_message or "系统繁忙，请稍后重试"
        self.conversation_id = conversation_id
        self.trace_id = trace_id
        super().__init__(message)


# ── 认证 / 授权 ──────────────────────────────────────────

class AuthenticationError(CustomerServiceError):
    """用户未认证"""

    def __init__(self, message: str = "用户未认证", **kw):
        super().__init__(message, "AUTH_FAILED", "请先登录后再使用此功能", **kw)


class AuthorizationError(CustomerServiceError):
    """权限不足"""

    def __init__(self, message: str = "无权执行此操作", **kw):
        super().__init__(message, "PERMISSION_DENIED", "您没有权限执行此操作", **kw)


# ── 参数校验 ──────────────────────────────────────────────

class ValidationError(CustomerServiceError):
    """参数校验失败"""

    def __init__(self, message: str = "参数校验失败", **kw):
        super().__init__(message, "VALIDATION_ERROR", "输入信息有误，请检查后重试", **kw)


# ── 业务规则 ──────────────────────────────────────────────

class BusinessRuleError(CustomerServiceError):
    """业务规则不满足"""

    def __init__(self, message: str, user_message: str | None = None, **kw):
        super().__init__(message, "BUSINESS_RULE", user_message or message, **kw)


class OrderNotFoundError(BusinessRuleError):
    def __init__(self, message: str = "订单不存在", **kw):
        CustomerServiceError.__init__(
            self, message, "ORDER_NOT_FOUND", "未找到相关订单信息", **kw,
        )


class AccountNotFoundError(BusinessRuleError):
    """账户记录不存在（查无此账户行——与「未认证」是两件事，2026-10-07 口径修正）"""

    def __init__(self, message: str = "账户不存在", **kw):
        CustomerServiceError.__init__(
            self, message, "ACCOUNT_NOT_FOUND", "未找到相关账户信息", **kw,
        )


class OrderNotEligibleError(BusinessRuleError):
    def __init__(self, message: str, **kw):
        CustomerServiceError.__init__(
            self, message, "ORDER_NOT_ELIGIBLE", message, **kw,
        )


# ── 检索 / 外部服务 / 数据库 ─────────────────────────────

class RetrievalError(CustomerServiceError):
    """RAG 检索失败"""

    retryable = True

    def __init__(self, message: str = "知识检索失败", **kw):
        super().__init__(message, "RETRIEVAL_ERROR", "暂时无法查询相关信息", **kw)


class ExternalServiceError(CustomerServiceError):
    """外部服务异常"""

    retryable = True

    def __init__(self, message: str = "外部服务异常", **kw):
        super().__init__(message, "EXTERNAL_SERVICE", "系统繁忙，请稍后重试", **kw)


class DatabaseError(CustomerServiceError):
    """数据库异常"""

    retryable = True

    def __init__(self, message: str = "数据库异常", **kw):
        super().__init__(message, "DATABASE_ERROR", "系统繁忙，请稍后重试", **kw)


# ── 操作执行 / 人工转接 ──────────────────────────────────

class ActionExecutionError(CustomerServiceError):
    """操作执行失败"""

    def __init__(self, message: str, user_message: str | None = None, **kw):
        super().__init__(message, "ACTION_FAILED", user_message or "操作执行失败，请稍后重试", **kw)


class RefundPolicyUnavailableError(DatabaseError):
    """退款政策配置缺失或非法 —— 写路径 fail-closed。

    继承 DatabaseError 是有意的：动作专家对「事实源不可用」已有统一的
    降级话术（不发起申请 + 引导转人工），政策不可用与之同类，不另造分支。
    """

    def __init__(self, message: str = "退款政策不可用", **kw):
        super().__init__(message, **kw)
        self.code = "REFUND_POLICY_UNAVAILABLE"


class RefundRequestUnavailableError(BusinessRuleError):
    """退款申请后端未启用或没有真实业务网关能力。"""

    def __init__(self, message: str = "退款申请服务暂不可用", **kw):
        super().__init__(
            message,
            user_message=(
                "当前业务通道暂不支持提交退款申请，未创建申请记录；"
                "请联系人工客服协助。"
            ),
            **kw,
        )


class RefundRequestConflictError(BusinessRuleError):
    """退款案件已存在且与当前确认请求不一致。"""

    def __init__(self, message: str = "退款申请与已有案件冲突", **kw):
        super().__init__(
            message,
            user_message=(
                "该会话已有待处理的退款案件，系统没有重复创建申请；"
                "请查询申请状态或联系人工客服。"
            ),
            **kw,
        )


class HumanHandoffError(CustomerServiceError):
    """人工转接异常"""

    retryable = True

    def __init__(self, message: str = "转接失败", **kw):
        super().__init__(message, "HANDOFF_ERROR", "转接人工客服失败，请稍后重试", **kw)


# ── 错误码 → 用户响应映射（§14.3）────────────────────────

ERROR_USER_MESSAGES: dict[str, str | None] = {
    "AUTH_FAILED":       "请先登录后再使用此功能。",
    "PERMISSION_DENIED": "您没有权限执行此操作。",
    "VALIDATION_ERROR":  "输入信息有误，请检查后重试。",
    "ORDER_NOT_FOUND":   "未找到相关订单信息，请确认订单号是否正确。",
    "ACCOUNT_NOT_FOUND": "未找到相关账户信息，请确认后再试。",
    "BUSINESS_RULE":     None,
    "RETRIEVAL_ERROR":   "暂时无法查询相关信息，请稍后重试。",
    "EXTERNAL_SERVICE":  "系统繁忙，请稍后重试。",
    "DATABASE_ERROR":    "系统繁忙，请稍后重试。",
    "ACTION_FAILED":     "操作执行失败，请稍后重试。",
    "HANDOFF_ERROR":     "转接人工客服失败，请稍后重试。",
}
