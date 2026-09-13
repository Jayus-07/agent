"""token_budget.py — 对话上下文 token 预算工具

历史窗口（L1=20 条）与 RAG 证据（top_k 条数）此前均按"条数"控制，
长消息/长文档会挤爆 LLM_CONTEXT_LENGTH（4096）。本模块提供统一的
token 计数与按预算裁剪：

  - trim_messages_to_budget: 历史消息裁剪（SystemMessage 全保留，
    其余从最新往回保留）
  - trim_texts_to_budget:    按顺序保留的通用文本裁剪（RAG 证据等，
    输入顺序即相关性顺序，从头保留）

计数用 tiktoken（o200k_base）；加载失败时退化为"中文约 2 字符/token"
的粗估，保证功能可用而非精确。
"""
import threading

from langchain_core.messages import BaseMessage

# 降级粗估：平均每 token 的字符数（中文场景约 1 token ≈ 1.5~2 字符，取保守值 2）
_CHARS_PER_TOKEN_FALLBACK = 2

_lock = threading.Lock()
_encoding = None
_encoding_failed = False


def _get_encoding():
    """懒加载 tiktoken 编码器；失败只尝试一次，之后永久走粗估。"""
    global _encoding, _encoding_failed
    if _encoding is not None or _encoding_failed:
        return _encoding
    with _lock:
        if _encoding is None and not _encoding_failed:
            try:
                import tiktoken
                _encoding = tiktoken.get_encoding("o200k_base")
            except Exception:
                _encoding_failed = True
    return _encoding


def count_tokens(text: str) -> int:
    """统计文本 token 数（tiktoken 精确计数，失败时字符数粗估）。"""
    if not text:
        return 0
    enc = _get_encoding()
    if enc is None:
        return max(1, len(text) // _CHARS_PER_TOKEN_FALLBACK)
    return len(enc.encode(text))


def count_message_tokens(msg: BaseMessage) -> int:
    """统计单条 LangChain 消息的 token 数（content 兼容 str 与多模态 list）。"""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return count_tokens(content)
    if isinstance(content, list):
        # 多模态消息：拼接文本 part 估算（图片 part 不计）
        return count_tokens("".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        ))
    return count_tokens(str(content))


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
