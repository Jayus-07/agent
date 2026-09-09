"""Ollama 生成后端 — 非阻塞 LLM Judge 的本地推理链路。

使用 langchain_ollama.ChatOllama 替代原始 HTTP 调用。
用于评测系统的答案生成和 LLM-based 答案相关性评估。
"""
from __future__ import annotations

import os

from backend.shared.logger import logger

_OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

_token_usage = {"prompt_tokens": 0, "completion_tokens": 0}


def get_token_usage() -> dict[str, int]:
    """返回累计的 token 消耗。"""
    return dict(_token_usage)


def reset_token_usage() -> None:
    """重置 token 计数器（新一轮评测前调用）。"""
    _token_usage["prompt_tokens"] = 0
    _token_usage["completion_tokens"] = 0


def _make_chat(model: str | None = None, base_url: str | None = None, temperature: float = 0.1):
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=model or _OLLAMA_MODEL,
        temperature=temperature,
        base_url=base_url or _OLLAMA_BASE_URL,
    )


def _invoke_chat(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
) -> str:
    """调用 ChatOllama，累计 token 用量，返回文本。"""
    from langchain_core.messages import HumanMessage

    chat = _make_chat(model, base_url, temperature)
    try:
        response = chat.invoke([HumanMessage(content=prompt)])
        usage = getattr(response, "usage_metadata", None) or {}
        _token_usage["prompt_tokens"] += usage.get("input_tokens", 0)
        _token_usage["completion_tokens"] += usage.get("output_tokens", 0)
        return response.content.strip()
    except Exception as e:
        logger.warning(f"[Ollama] 调用失败: {e}")
        return ""


def generate_answer_ollama(
    question: str,
    context: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """通过 ChatOllama 生成答案。

    Args:
        question: 用户问题
        context: 检索到的上下文片段列表
        model: Ollama 模型名（默认读 OLLAMA_MODEL 环境变量）
        base_url: Ollama 服务地址（默认读 OLLAMA_HOST 环境变量）

    Returns:
        生成的答案文本，失败时返回空字符串。
    """
    context_text = "\n---\n".join(context) if context else "（无检索结果）"
    prompt = (
        f"基于以下参考资料回答问题。如果资料不足以回答，请说明。\n\n"
        f"参考资料:\n{context_text}\n\n"
        f"问题: {question}\n\n"
        f"答案:"
    )
    return _invoke_chat(prompt, model=model, base_url=base_url, temperature=0.1)


def answer_relevancy_llm(
    question: str,
    answer: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> float:
    """RAGAS-style 答案相关性 — LLM 从答案生成 N 个问题，取 embedding cosine 均值。

    方法：
    1. 让 LLM 从答案反向生成 3 个可能的问题
    2. 用 embedding 模型计算原始问题与生成问题的 cosine 相似度
    3. 取均值作为答案相关性分数

    Returns:
        0~1 的相关性分数，失败时返回 0.0。
    """
    if not answer.strip():
        return 0.0

    generated_questions = _generate_questions_from_answer(
        answer, n=3, model=model, base_url=base_url,
    )
    if not generated_questions:
        return 0.0

    try:
        from backend.evaluation.semantic import get_eval_scorer
        scorer = get_eval_scorer()
        scores = scorer.score_pairs(
            [question] * len(generated_questions),
            generated_questions,
        )
        return round(sum(scores) / len(scores), 4)
    except Exception as e:
        logger.warning(f"[Ollama] 答案相关性计算失败: {e}")
        return 0.0


def _generate_questions_from_answer(
    answer: str,
    *,
    n: int = 3,
    model: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """让 LLM 从答案反向生成 N 个问题。"""
    prompt = (
        f"根据以下答案，生成 {n} 个可能导致该答案的问题。\n"
        f"每行一个问题，不要编号，不要其他内容。\n\n"
        f"答案: {answer}\n\n"
        f"问题:"
    )
    text = _invoke_chat(prompt, model=model, base_url=base_url, temperature=0.7)
    if not text:
        return []
    questions = [
        q.strip().lstrip("0123456789.、）) ")
        for q in text.split("\n")
        if q.strip() and len(q.strip()) > 4
    ]
    return questions[:n]
