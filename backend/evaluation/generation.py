"""Ollama 生成后端 — 非阻塞 LLM Judge 的本地推理链路。

直接 HTTP 调用 localhost:11434，不依赖 ollama Python 包。
用于 Phase 4.2 的非阻塞 CI job：真实 LLM 生成 + RAGAS-style 答案相关性。
"""
from __future__ import annotations

import json
import os

from backend.shared.logger import logger

_OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
_GENERATION_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))


def generate_answer_ollama(
    question: str,
    context: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> str:
    """通过 Ollama HTTP API 生成答案。

    Args:
        question: 用户问题
        context: 检索到的上下文片段列表
        model: Ollama 模型名（默认读 OLLAMA_MODEL 环境变量）
        base_url: Ollama 服务地址（默认读 OLLAMA_HOST 环境变量）

    Returns:
        生成的答案文本，失败时返回空字符串。
    """
    import urllib.error
    import urllib.request

    url = f"{base_url or _OLLAMA_BASE_URL}/api/generate"
    model = model or _OLLAMA_MODEL

    context_text = "\n---\n".join(context) if context else "（无检索结果）"
    prompt = (
        f"基于以下参考资料回答问题。如果资料不足以回答，请说明。\n\n"
        f"参考资料:\n{context_text}\n\n"
        f"问题: {question}\n\n"
        f"答案:"
    )

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_GENERATION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except Exception as e:
        logger.warning(f"[Ollama] 生成失败: {e}")
        return ""


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
    import urllib.request

    url = f"{base_url or _OLLAMA_BASE_URL}/api/generate"
    model = model or _OLLAMA_MODEL

    prompt = (
        f"根据以下答案，生成 {n} 个可能导致该答案的问题。\n"
        f"每行一个问题，不要编号，不要其他内容。\n\n"
        f"答案: {answer}\n\n"
        f"问题:"
    )

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 256},
    }).encode("utf-8")

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_GENERATION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data.get("response", "").strip()
            questions = [
                q.strip().lstrip("0123456789.、）) ")
                for q in text.split("\n")
                if q.strip() and len(q.strip()) > 4
            ]
            return questions[:n]
    except Exception as e:
        logger.warning(f"[Ollama] 问题生成失败: {e}")
        return []


def setup_ollama_judge() -> None:
    """将 judge_answer 的 LLM 后端设置为 Ollama。"""
    from backend.evaluation.judge import set_llm_callable

    def _ollama_callable(prompt: str) -> str:
        import urllib.request

        url = f"{_OLLAMA_BASE_URL}/api/generate"
        payload = json.dumps({
            "model": _OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 1024},
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_GENERATION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()

    set_llm_callable(_ollama_callable)
    logger.info("[Ollama] Judge LLM 已设置为 Ollama")
