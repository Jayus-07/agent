"""
minimax.py — MiniMax Provider（Anthropic Messages API，官方推荐）

提供:
  - build_minimax(): 构建 ChatAnthropic 实例（MiniMax Anthropic 兼容端点）
  - get_minimax_balance(): MiniMax 余额查询

端点来源：`credentials.MINIMAX_ANTHROPIC_URL`（Anthropic 兼容端点）。
⚠️ 不要改读 config 的 `MINIMAX_API_BASE` —— 它的语义是 **OpenAI 兼容端点**，与这里走的
Anthropic Messages API 不是一套协议；`backend/config/llm.py` 里它的默认值是**空串**
（不是某个域名），`.env` 里当前恰好同值只是巧合。改读 config 后，`.env` 一旦缺失或变更
就会静默漂移到另一套协议，而 `PROVIDERS["minimax"]["driver"] == "anthropic"` 仍宣称相反。

凭据在**调用时**解析（credentials，见 infra/llm/credentials.py）。
"""

from backend.config import (
    LLM_CONTEXT_LENGTH,
    LLM_REQUEST_TIMEOUT,
    LLM_TEMPERATURE,
    MINIMAX_API_KEY,
)
from backend.infra.llm.credentials import MINIMAX_ANTHROPIC_URL, ProviderCredentials


def build_minimax(
    model_name: str, credentials: ProviderCredentials | None = None
) -> object:
    """构建 MiniMax 模型实例（Anthropic Messages API，官方推荐路径）

    MiniMax 文档推荐使用 Anthropic 兼容 API:
      - 支持 thinking: {"type": "disabled"} 关闭强制思考
      - 支持 interleaved thinking 高级特性
      - Chat Completions 仅作为 OpenAI SDK 用户迁移备选
    """
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:
        raise ImportError(
            "minimax provider 需要 langchain_anthropic 包，请 pip install langchain-anthropic"
        ) from e

    api_key = credentials.api_key if credentials is not None else ""
    base_url = credentials.base_url if credentials is not None else ""

    headers = {"x-api-key": api_key}
    if credentials and credentials.extra_headers:
        headers.update(credentials.extra_headers)

    # MiniMax Anthropic 端点
    return ChatAnthropic(
        model=model_name,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_CONTEXT_LENGTH,
        timeout=LLM_REQUEST_TIMEOUT,
        anthropic_api_key=api_key,
        anthropic_api_url=base_url,
        default_headers=headers,
    )


def get_minimax_balance(credentials: ProviderCredentials | None = None) -> dict:
    """MiniMax 余额（官网查询，此处返回固定值）"""
    return {
        "ok": True,
        "provider": "minimax",
        "balance": "—",
        "currency": "CNY",
        "note": "MiniMax 余额请前往官网查询",
    }
