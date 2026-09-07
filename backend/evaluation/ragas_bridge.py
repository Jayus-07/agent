"""RAGAS 官方包适配层 — 与自研 sem_* 指标并行的基准对比。

通过 --ragas CLI 标志或 EVAL_RAGAS=1 环境变量启用。
使用本地模型：bge-small-zh-v1.5 (embeddings) + Ollama qwen2.5:3b (LLM)。
计算失败不阻断主流程（fail-safe）。
"""
from __future__ import annotations

import json
import os
from typing import Any

from backend.shared.logger import logger

_EMBEDDINGS_MODEL = os.getenv("RAGAS_EMBEDDINGS_MODEL", "BAAI/bge-small-zh-v1.5")
_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

_embeddings_cache: Any = None
_llm_cache: Any = None
_patched = False


def _unwrap_nested_json(obj: Any) -> Any:
    """递归剥掉小模型的多余嵌套层。

    qwen2.5:3b 经常输出 {"ContextRecallOutput": {"classifications": [...]}}
    而不是 RAGAS Pydantic 期望的 {"classifications": [...]}。
    对 single-key dict 且 value 也是 dict 的情况，递归向下取。
    """
    if not isinstance(obj, dict) or len(obj) != 1:
        return obj
    key, val = next(iter(obj.items()))
    if isinstance(val, dict):
        return _unwrap_nested_json(val)
    return obj


def _patch_instructor_json() -> None:
    """Monkey-patch instructor registry的 response_parser，处理小模型多余嵌套。

    RAGAS 0.4+ collections 指标通过 instructor 库解析 JSON，
    实际调用链：metric.score() → InstructorLLM.agenerate() →
    retry_sync_v2/retry_async_v2 → handlers.response_parser(...) →
    model_validate_json(text)

    关键发现：instructor v2 使用 mode_registry 注册函数指针（非类方法），
    @register_mode_handler 在导入时创建实例并将 bound method 存入 registry。
    修改类方法无效，必须直接 patch registry 中的 response_parser。

    qwen2.5:3b 输出 {"ContextRecallOutput": {"classifications": [...]}}
    而 Pydantic 期望 {"classifications": [...]}，导致 validation error。
    """
    global _patched
    if _patched:
        return

    try:
        from instructor.v2.core.mode import Mode
        from instructor.v2.core.providers import Provider
        from instructor.v2.core.registry import mode_registry
    except ImportError:
        logger.warning("[RAGAS] 无法导入 instructor registry，跳过 patch")
        return

    patched_count = 0
    for provider in [Provider.OPENAI, Provider.TOGETHER, Provider.ANYSCALE]:
        try:
            handlers = mode_registry.get_handlers(provider, Mode.JSON)
            orig_parser = handlers.response_parser

            def _make_wrapped_parser(_orig):
                def _patched_parser(
                    response: Any,
                    response_model: type,
                    validation_context: dict[str, Any] | None = None,
                    strict: bool | None = None,
                    stream: bool = False,
                    is_async: bool = False,
                ) -> Any:
                    try:
                        return _orig(
                            response=response,
                            response_model=response_model,
                            validation_context=validation_context,
                            strict=strict,
                            stream=stream,
                            is_async=is_async,
                        )
                    except Exception:
                        try:
                            choices = getattr(response, "choices", None)
                            if choices:
                                text = choices[0].message.content or ""
                                obj = json.loads(text)
                                unwrapped = _unwrap_nested_json(obj)
                                if unwrapped is not obj:
                                    parsed = response_model.model_validate_json(
                                        json.dumps(unwrapped),
                                        context=validation_context,
                                        strict=strict,
                                    )
                                    return parsed
                        except Exception:
                            pass
                        raise
                return _patched_parser

            handlers.response_parser = _make_wrapped_parser(orig_parser)
            patched_count += 1
        except Exception:
            continue

    _patched = True
    logger.info(f"[RAGAS] JSON 解析管线已 patch（instructor registry response_parser × {patched_count} providers）")


def is_ragas_enabled() -> bool:
    """检查 RAGAS 是否启用（CLI --ragas 或 EVAL_RAGAS=1）。"""
    return os.getenv("EVAL_RAGAS", "").strip() in ("1", "true", "yes")


def _get_ragas_embeddings() -> Any:
    """延迟初始化本地 embeddings（bge-small-zh-v1.5，使用 ragas 原生类）。"""
    global _embeddings_cache
    if _embeddings_cache is not None:
        return _embeddings_cache

    from ragas.embeddings import HuggingFaceEmbeddings

    _embeddings_cache = HuggingFaceEmbeddings(
        model=_EMBEDDINGS_MODEL,
        device="cpu",
    )
    return _embeddings_cache


def _get_ragas_llm() -> Any:
    """延迟初始化 Ollama LLM（通过 ragas llm_factory + OpenAI 兼容接口）。"""
    global _llm_cache
    if _llm_cache is not None:
        return _llm_cache

    from openai import AsyncOpenAI
    from ragas.llms import llm_factory

    client = AsyncOpenAI(
        base_url=f"{_OLLAMA_HOST}/v1",
        api_key="ollama",
    )
    _llm_cache = llm_factory(model=_OLLAMA_MODEL, provider="openai", client=client)
    return _llm_cache


def _score_metric_safe(name: str, metric: Any, kwargs: dict) -> float | None:
    """调用单个 metric.score()，失败返回 None（不传播异常）。"""
    try:
        result = metric.score(**kwargs)
        val = float(result.value)
        if val != val:  # NaN check
            return None
        return round(val, 4)
    except Exception as e:
        logger.warning(f"[RAGAS] {name} 计算失败: {e}")
        return None


def compute_ragas_metrics(
    question: str,
    retrieved_texts: list[str],
    ground_truth_texts: list[str],
    expected_answer: str | None,
    generated_answer: str,
) -> dict[str, float]:
    """计算 RAGAS 官方 5 指标，返回 ragas_* 前缀字典。

    使用 ragas 0.4+ 的 metrics.collections API：每个 metric 直接调用 score()，
    不走 evaluate() 管道。

    Parameters
    ----------
    question : 用户问题
    retrieved_texts : 检索到的 chunk 全文列表
    ground_truth_texts : ground_truth_context 纯文本列表（保留兼容性，当前未直接使用）
    expected_answer : 期望答案（用作 reference）
    generated_answer : Ollama 生成的答案

    Returns
    -------
    dict[str, float] — 键名以 ragas_ 前缀，失败时返回空 dict。
    """
    if not retrieved_texts:
        return {}

    if not generated_answer.strip():
        logger.warning("[RAGAS] generated_answer 为空，跳过 RAGAS 计算")
        return {}

    reference = expected_answer or generated_answer

    _patch_instructor_json()

    try:
        llm = _get_ragas_llm()
        embeddings = _get_ragas_embeddings()
    except Exception as e:
        logger.warning(f"[RAGAS] LLM/Embeddings 初始化失败: {e}")
        return {}

    try:
        from ragas.metrics.collections import (
            AnswerCorrectness,
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )
    except ImportError:
        logger.warning("[RAGAS] ragas 包未安装，跳过。pip install ragas>=0.4.0")
        return {}

    common = {"user_input": question}

    metrics_spec: list[tuple[str, Any, dict]] = [
        ("ragas_context_recall", ContextRecall(llm=llm), {
            **common, "retrieved_contexts": retrieved_texts, "reference": reference,
        }),
        ("ragas_context_precision", ContextPrecision(llm=llm), {
            **common, "reference": reference, "retrieved_contexts": retrieved_texts,
        }),
        ("ragas_faithfulness", Faithfulness(llm=llm), {
            **common, "response": generated_answer, "retrieved_contexts": retrieved_texts,
        }),
        ("ragas_answer_relevancy", AnswerRelevancy(llm=llm, embeddings=embeddings), {
            **common, "response": generated_answer,
        }),
        ("ragas_answer_correctness", AnswerCorrectness(llm=llm, embeddings=embeddings), {
            **common, "response": generated_answer, "reference": reference,
        }),
    ]

    output: dict[str, float] = {}
    for key, metric, kwargs in metrics_spec:
        val = _score_metric_safe(key, metric, kwargs)
        if val is not None:
            output[key] = val

    return output


def reset_caches() -> None:
    """重置 embeddings/LLM/patch 缓存（测试用）。"""
    global _embeddings_cache, _llm_cache, _patched
    _embeddings_cache = None
    _llm_cache = None
    _patched = False
