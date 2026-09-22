"""resolved_model.py — ResolvedModelContext（2026-09-22 P1 模型归属修复）

问题（llm.stream 模型归属失真）：流式 chunk 无 response_metadata.model_name，
usage 结算回退到 import 期常量 LLM_MODEL —— 请求覆盖 / DB 绑定切换 /
tool_selector 专用模型 / fallback 接管时，用量、成本、trace 被记到错误模型。

方案：模型解析完成时（每次调用实时解析）生成 ResolvedModelContext 存入
ContextVar，usage 结算优先从调用上下文取真实模型，**禁止再靠全局默认值
推断**。ContextVar 按调用上下文隔离，并发请求天然不串线。

字段（用户规格）：
  provider        供应商标识（dashscope/openai/...）
  model_id        本次调用实际使用的模型登记名
  role            模型角色（main / tool_selector / ...）
  binding_source  request_override | db_binding | env_default | factory |
                  code_default | fallback
  request_id / trace_id  本次请求归属
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedModelContext:
    """一次 LLM 调用的模型解析结果（调用时生成，随调用上下文传递）。"""

    model_id: str
    provider: str
    role: str = "main"
    binding_source: str = "code_default"
    request_id: str = ""
    trace_id: str = ""

    def as_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "role": self.role,
            "binding_source": self.binding_source,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
        }


# 按调用上下文隔离（与 _last_call_meta_var 同模式：整体 set/get，并发安全）
_resolved_model_var: contextvars.ContextVar = contextvars.ContextVar(
    "llm_resolved_model_context", default=None,
)


def set_current_resolved_model(ctx: ResolvedModelContext | None) -> None:
    """登记当前调用上下文的模型解析结果（每次 LLM 调用前调用）。"""
    _resolved_model_var.set(ctx)


def get_current_resolved_model() -> ResolvedModelContext | None:
    """读取当前调用上下文的模型解析结果；无登记时返回 None。"""
    return _resolved_model_var.get()


def reset_current_resolved_model() -> None:
    """清空登记（trace 收尾 / 测试用，防线程池复用串味）。"""
    _resolved_model_var.set(None)
