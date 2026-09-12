"""
qwen.py — Qwen Provider（阿里云百炼在线模型，OpenAI 兼容协议）

提供:
  - build_qwen(): 构建 ChatOpenAI 实例（DashScope OpenAI 兼容端点）
  - get_qwen_balance(): Qwen 余额查询（无公开 API，引导官网查询）
"""

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_TEMPERATURE,
    QWEN_API_BASE,
    QWEN_API_KEY,
)


def build_qwen(model_name: str) -> object:
    """构建 Qwen 在线模型实例（通过 DashScope OpenAI 兼容协议）

    注意 enable_thinking：qwen3 系列是推理模型，DashScope 兼容端点上
    thinking 默认开启——非流式调用时 content 为空、实际输出落在
    reasoning_content（LangChain 不解析该字段），下游拿到空回答。
    项目内对话/RAG/评测场景均已显式关闭（ragas_bridge、llm_enrichment
    同款处理）；需要推理链的场景设置 QWEN_ENABLE_THINKING=true。
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "qwen provider 需要 langchain_openai 包，请 pip install langchain-openai"
        ) from e

    import os
    enable_thinking = os.getenv("QWEN_ENABLE_THINKING", "false").strip().lower() in ("1", "true", "yes")

    return ChatOpenAI(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        request_timeout=LLM_REQUEST_TIMEOUT,
        api_key=QWEN_API_KEY,
        base_url=QWEN_API_BASE,
        extra_body={"enable_thinking": enable_thinking},
    )


def get_qwen_balance() -> dict:
    """Qwen 余额（无公开余额 API，返回固定值引导官网查询）"""
    return {
        "ok": True,
        "provider": "qwen",
        "balance": "—",
        "currency": "CNY",
        "note": "Qwen 余额请前往阿里云百炼控制台查询",
    }
