"""评测答案生成后端 — LLM Judge 的推理链路。

两个后端：
- Ollama（本地）：仅 ENV_MODE=local 启用；ENV_MODE=cloud 时跳过（不连本地服务）
- DashScope（云）：OpenAI 兼容端点，与 ragas_bridge 同配置族（QWEN_API_KEY/
  QWEN_API_BASE/RAGAS_CLOUD_MODEL）。**仅在评测显式 opt-in（--judge/--ragas）
  时使用**——默认行为保持"cloud 模式不生成答案"，避免改变既有基线口径。

统一入口 generate_answer(question, context, allow_cloud=...)：
allow_cloud=True 且本地 Ollama 不可用时走云后端；否则回落 Ollama 行为。
"""
from __future__ import annotations

import os

from backend.config.llm import OLLAMA_BASE_URL, OLLAMA_ENABLED, OLLAMA_MODEL
from backend.shared.logger import logger

_ollama_skip_logged = False
_cloud_skip_logged = False

_token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
_chat_cache: dict[tuple, object] = {}


def get_token_usage() -> dict[str, int]:
    """返回累计的 token 消耗。"""
    return dict(_token_usage)


def reset_token_usage() -> None:
    """重置 token 计数器（新一轮评测前调用）。"""
    _token_usage["prompt_tokens"] = 0
    _token_usage["completion_tokens"] = 0


def _make_chat(model: str | None = None, base_url: str | None = None, temperature: float = 0.1):
    from langchain_ollama import ChatOllama
    key = (model or OLLAMA_MODEL, base_url or OLLAMA_BASE_URL, temperature)
    cached = _chat_cache.get(key)
    if cached is not None:
        return cached
    chat = ChatOllama(
        model=key[0],
        temperature=key[2],
        base_url=key[1],
    )
    _chat_cache[key] = chat
    return chat


def _invoke_chat(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
) -> str:
    """调用 ChatOllama，累计 token 用量，返回文本。"""
    global _ollama_skip_logged
    if not OLLAMA_ENABLED:
        if not _ollama_skip_logged:
            logger.info("[Ollama] ENV_MODE=cloud，本地 Ollama 已禁用，评测生成走降级路径")
            _ollama_skip_logged = True
        return ""
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


def _build_prompt(question: str, context: list[str]) -> str:
    """构造生成 prompt — 复用生产 prompt 模板链。

    2026-09-11 口径统一：评测专用硬编码 prompt 与生产链漂移，导致
    S6/S7/citation 等 answer 类指标度量的不是线上真实答案质量。
    现按生产链装配方式复刻：rag.qa 模板 system 段（含 {context}）+
    rag.document Evidence 格式 + human 段（{input}）。
    模板渲染失败时回退旧硬编码 prompt（保底可用）。
    """
    try:
        from backend.prompts.service import prompt_service

        full = prompt_service.get_template_sync("rag.qa")
        parts = full.split("---", 1)
        doc_tpl = prompt_service.get_template_sync("rag.document")
        docs = []
        for i, c in enumerate(context, 1):
            docs.append(doc_tpl.format(
                index=i, query_label="", doc_label="", section_label="",
                chunk_label="", type_label="", domain_label="",
                page_content=c,
            ))
        evidence = "\n\n---\n\n".join(docs) if docs else "（无检索结果）"

        if len(parts) == 2:
            system_text = parts[0].strip()
            human_text = parts[1].strip()
            return (
                system_text.replace("{context}", evidence)
                + "\n\n"
                + human_text.replace("{input}", question)
            )
        # 模板无 "---" 分隔符：整段作为 prompt 正文，{context}/{input} 均在其中
        return full.strip().replace("{context}", evidence).replace("{input}", question)
    except Exception:
        pass
    # 回退：旧评测专用 prompt
    context_text = "\n---\n".join(context) if context else "（无检索结果）"
    return (
        f"基于以下参考资料回答问题。如果资料不足以回答，请说明。\n\n"
        f"参考资料:\n{context_text}\n\n"
        f"问题: {question}\n\n"
        f"答案:"
    )


def _invoke_cloud(prompt: str) -> str:
    """DashScope 云生成（OpenAI 兼容端点），累计 token 用量，失败返回空串。"""
    global _cloud_skip_logged
    api_key = os.getenv("QWEN_API_KEY", "")
    if not api_key:
        if not _cloud_skip_logged:
            logger.warning("[CloudGen] QWEN_API_KEY 未配置，云生成不可用")
            _cloud_skip_logged = True
        return ""
    from langchain_core.messages import HumanMessage

    chat = _make_cloud_chat(api_key)
    try:
        response = chat.invoke([HumanMessage(content=prompt)])
        usage = getattr(response, "usage_metadata", None) or {}
        _token_usage["prompt_tokens"] += usage.get("input_tokens", 0)
        _token_usage["completion_tokens"] += usage.get("output_tokens", 0)
        return (response.content or "").strip()
    except Exception as e:
        logger.warning(f"[CloudGen] 调用失败: {e}")
        return ""


def _make_cloud_chat(api_key: str):
    from langchain_openai import ChatOpenAI
    key = (
        os.getenv("RAGAS_CLOUD_MODEL", "qwen3.7-plus"),
        os.getenv("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        0.1,
    )
    cached = _chat_cache.get(("cloud",) + key)
    if cached is not None:
        return cached
    chat = ChatOpenAI(
        model=key[0],
        temperature=key[2],
        max_tokens=2048,
        request_timeout=120,
        api_key=api_key,
        base_url=key[1],
        extra_body={"enable_thinking": False},
    )
    _chat_cache[("cloud",) + key] = chat
    return chat


def _strip_meta(raw: str) -> str:
    """剥离生产模型输出的 META 注释块（前端解析用，评测指标不需要）。"""
    import re as _re
    return _re.sub(r"<!--META.*?-->", "", raw, flags=_re.S).strip()


def generate_answer(
    question: str,
    context: list[str],
    *,
    allow_cloud: bool = False,
) -> str:
    """统一生成入口。

    allow_cloud=True（评测显式 opt-in --judge/--ragas）且本地 Ollama 不可用时，
    走 DashScope 云后端；其余情况回落 generate_answer_ollama 原行为
    （cloud 环境返回空串，保持既有基线口径不变）。
    """
    prompt = _build_prompt(question, context)
    if allow_cloud and not OLLAMA_ENABLED:
        raw = _invoke_cloud(prompt)
        if raw:
            return _strip_meta(raw)
    return _invoke_chat(prompt)


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
        base_url: Ollama 服务地址（默认取 config.OLLAMA_BASE_URL，跟随 OLLAMA_BASE_URL 环境变量）

    Returns:
        生成的答案文本（已剥离 META 注释块），失败时返回空字符串。
    """
    prompt = _build_prompt(question, context)
    raw = _invoke_chat(prompt, model=model, base_url=base_url, temperature=0.1)
    if not raw:
        return raw
    # 剥离生产模型输出的 META 注释块（前端解析用，评测指标不需要）
    import re as _re
    return _re.sub(r"<!--META.*?-->", "", raw, flags=_re.S).strip()


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
