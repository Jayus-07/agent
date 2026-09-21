"""shared/tool_envelope.py — Tool 层结构化输出的统一封套（成功侧）

与 ``error_protocol.ErrorEnvelope``（失败侧协议）对称，把「返回 JSON 的
结构化 Tool」的成功/失败出口收敛为同一种形状：

    成功: {"status": "success", "data": <业务数据>, ...extra}
    失败: {"status": "failed",  "error": <安全消息>, ...extra}

适用范围：**只约束返回 JSON 字符串的结构化 Tool**（sql / map / poi 等）。
返回 Markdown / 纯文本的 Tool（web_search / report / rag 等）输出是给 LLM
读的，不套封套——套了只会让 prompt 变胖。

消费方约定（边界归一化，参照 skill_adapter f14/f16b 模式）：
  - 边界适配层读 ``status`` 判断成败，成功取 ``data``，失败上抛或透传。
"""
from __future__ import annotations

import json
from typing import Any

from backend.shared.error_protocol import error_envelope_from_exception

__all__ = ["tool_success_result", "tool_error_result", "parse_tool_envelope"]


def tool_success_result(data: Any, **extra: Any) -> str:
    """结构化 Tool 的统一成功出口。

    Args:
        data: 业务数据（dict / list），整体放在 ``data`` 键下。
        **extra: 少量顶层补充字段（如 ``row_count``），不要把业务数据塞这里。

    Returns:
        JSON 字符串（中文不转义）。
    """
    payload: dict[str, Any] = {"status": "success", "data": data}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def tool_error_result(
    message_or_exc: str | BaseException,
    *,
    source: str = "tool",
    trace_id: str = "",
    **extra: Any,
) -> str:
    """结构化 Tool 的统一失败出口（与 success 对称）。

    Args:
        message_or_exc: 安全错误消息（给 LLM/调用方的可执行提示，不是堆栈），
            或一个异常对象（走 error_protocol 映射成安全封套）。
        **extra: 顶层补充字段（如 ``known_cities`` / ``reason``）。

    Returns:
        JSON 字符串：``{"status": "failed", "error": ..., ...extra}``；
        传异常对象时额外带 ``error_protocol``（九字段协议封套）。
    """
    payload: dict[str, Any] = {}
    if isinstance(message_or_exc, BaseException):
        envelope = error_envelope_from_exception(
            message_or_exc, trace_id=trace_id, source=source
        )
        payload["error"] = envelope.message
        payload["error_protocol"] = envelope.to_dict()
    else:
        payload["error"] = str(message_or_exc)
    payload["status"] = "failed"
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse_tool_envelope(raw: str) -> dict[str, Any]:
    """解析 Tool 返回的封套字符串，返回原始 dict。

    边界适配层用 ``status`` / ``data`` 键自行判断，本函数只做 json.loads
    这一步（解析失败抛 ValueError，由调用方决定降级语义）。
    """
    return json.loads(raw)
