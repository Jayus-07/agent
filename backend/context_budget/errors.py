"""context_budget.errors — 上下文预算硬门禁错误（2026-10-01 STOP A）

契约（本轮冻结的执行契约）：每一次实际发给模型的请求都必须满足该次调用
的输入预算。必需内容（System / 当前用户问题 / 活跃工具组 / 业务 pin）
本身放不下、或全部确定性裁剪后仍超窗时，**在调用 provider 之前**以本
异常终止——模型调用次数必须为 0，不得回退原始消息、不得换模型重试
（同一份输入换任何模型都必然再超）。

本异常不是 provider 错误也不是限流：SSE/API 层按稳定错误码
``context_length_exceeded`` 映射为用户可操作提示，retryable=False。
"""

from __future__ import annotations

# SSE / API 层的稳定错误码（对外契约，不得随意改名）
CONTEXT_LENGTH_EXCEEDED_CODE = "context_length_exceeded"


class ContextBudgetExceededError(RuntimeError):
    """输入经全部裁剪后仍超出该次调用的输入预算（fail-closed，拒发）。"""

    def __init__(
        self,
        message: str | None = None,
        *,
        used_tokens: int = 0,
        input_budget: int = 0,
        stage: str = "",
    ):
        super().__init__(message or self.default_message())
        self.used_tokens = max(0, int(used_tokens))
        self.input_budget = max(0, int(input_budget))
        self.stage = stage

    @staticmethod
    def default_message() -> str:
        return (
            "本次输入内容过长，超出模型可用的上下文窗口。"
            "请缩短输入、精简附带材料，或开启新会话后重试。"
        )

    @property
    def code(self) -> str:
        return CONTEXT_LENGTH_EXCEEDED_CODE

    def to_sse_data(self) -> dict:
        """SSE error 帧的 data 体：稳定错误码 + 用户可操作提示，无堆栈。"""
        return {
            "code": self.code,
            "message": self.default_message(),
            "retryable": False,
            "used_tokens": self.used_tokens,
            "input_budget": self.input_budget,
            "stage": self.stage,
        }
