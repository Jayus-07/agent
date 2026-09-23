"""token_budget.py — 对话上下文 token 预算工具

历史窗口（L1=20 条）与 RAG 证据（top_k 条数）此前均按"条数"控制，
长消息/长文档会挤爆 LLM_CONTEXT_LENGTH（4096）。本模块提供统一的
token 计数与按预算裁剪：

  - trim_messages_to_budget: 历史消息裁剪（SystemMessage 全保留，
    其余从最新往回保留）
  - trim_texts_to_budget:    按顺序保留的通用文本裁剪（RAG 证据等，
    输入顺序即相关性顺序，从头保留）

计数自 2026-09-23 起统一委托 context_budget.token_counter（P0-1 模型
感知：openai→tiktoken 兼容口径；DeepSeek/Qwen 等→CJK 标定估算 ×安全
系数）。本模块只保留裁剪算法；业务层禁止直接调用 tiktoken。
"""
import threading

from langchain_core.messages import BaseMessage

_lock = threading.Lock()


def count_tokens(text: str) -> int:
    """统计文本 token 数（模型感知，见 context_budget.token_counter）。"""
    if not text:
        return 0
    from backend.context_budget.token_counter import count_tokens as _count
    return _count(text)


def count_message_tokens(msg: BaseMessage) -> int:
    """统计单条 LangChain 消息 token 数（含模板开销与多模态图片估算）。"""
    from backend.context_budget.token_counter import count_message_tokens as _count
    return _count(msg)


def _is_system(msg: BaseMessage) -> bool:
    return type(msg).__name__ == "SystemMessage"


def trim_messages_to_budget(
    messages: list[BaseMessage], budget: int
) -> tuple[list[BaseMessage], int]:
    """按 token 预算裁剪消息列表。

    规则：
      - SystemMessage 全保留（L2 摘要 / L3 长期记忆注入，优先级最高）
      - 其余消息从最新往回保留，放不下的旧消息丢弃
      - budget <= 0 表示关闭预算，原样返回

    Returns:
        (kept_messages, dropped_count)
    """
    if budget <= 0:
        return list(messages), 0

    kept: list[BaseMessage] = []
    dropped = 0
    used = 0
    for msg in reversed(messages):
        if _is_system(msg):
            kept.append(msg)
            continue
        t = count_message_tokens(msg)
        if used + t > budget:
            dropped += 1
            continue
        used += t
        kept.append(msg)
    kept.reverse()
    return kept, dropped


def trim_texts_to_budget(
    texts: list[str], budget: int
) -> tuple[list[str], int]:
    """按 token 预算保留文本序列（顺序即优先级，从头保留）。

    用于 RAG 证据裁剪：rerank 后的文档按相关性排序，超出预算的尾部
    整体丢弃（不截断单条文本，避免破坏引用标注）。

    Returns:
        (kept_texts, dropped_count)
    """
    if budget <= 0:
        return list(texts), 0

    kept: list[str] = []
    dropped = 0
    used = 0
    for text in texts:
        t = count_tokens(text)
        if used + t > budget and kept:
            dropped += 1
            continue
        used += t
        kept.append(text)
    return kept, dropped
