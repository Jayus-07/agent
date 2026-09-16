"""
qwen_tp.py — Qwen Token Plan Provider（阿里云百炼模型包端点，OpenAI 兼容协议）

与标准 qwen provider 的区别:
  - 端点: Token Plan 专属端点（token-plan.cn-beijing.maas.aliyuncs.com）
  - Key: sk-sp- 前缀（模型包计划）；不支持 rerank 端点、无余额查询 API
  - 注册名约定: AVAILABLE_MODELS 中带 @tp 后缀（如 qwen3.7-plus@tp），
    构建时剥掉后缀、把真实模型名发给 API

提供:
  - build_qwen_tp(): 构建 ChatOpenAI 实例（Token Plan 端点）
  - get_qwen_tp_balance(): Token Plan 无余额 API，返回用量包提示
"""

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_STREAM_USAGE,
    LLM_TEMPERATURE,
    QWEN_TP_API_BASE,
    QWEN_TP_API_KEY,
)

# 注册名后缀 → 真实模型名的分隔符
_TP_SUFFIX = "@tp"


def build_qwen_tp(model_name: str) -> object:
    """构建 Qwen Token Plan 模型实例

    model_name 带注册后缀（qwen3.7-plus@tp），剥掉后发送真实模型名。
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "qwen_tp provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    real_model = model_name.removesuffix(_TP_SUFFIX)

    import os
    enable_thinking = os.getenv("QWEN_ENABLE_THINKING", "false").strip().lower() in ("1", "true", "yes")

    return ChatOpenAI(
        model=real_model,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=QWEN_TP_API_KEY,
        base_url=QWEN_TP_API_BASE,
        extra_body={"enable_thinking": enable_thinking},
        stream_usage=LLM_STREAM_USAGE,
    )


def get_qwen_tp_balance() -> dict:
    """Token Plan 无公开余额 API（用量包在控制台看），返回提示"""
    return {
        "ok": True,
        "provider": "qwen_tp",
        "balance": "—",
        "currency": "CNY",
        "note": "Token Plan 用量请前往阿里云百炼控制台的模型包页面查询",
    }
