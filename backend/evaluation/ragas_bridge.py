"""RAGAS 官方包适配层 — 与自研 sem_* 指标并行的基准对比。

LLM 打分使用 DashScope 云 API (qwen-plus)，Embeddings 使用本地
HuggingFaceEmbeddings (bge-small-zh-v1.5, GPU)。
通过 LangchainLLMWrapper / LangchainEmbeddingsWrapper 官方适配层接入 RAGAS。
计算失败不阻断主流程（fail-safe）。

RAGAS 0.4.3 有两套指标 API：
- legacy ``ragas.metrics.*``: 配合 LangchainLLMWrapper，通过 single_turn_score 调用
- collections ``ragas.metrics.collections.*``: 需要 llm_factory 创建的 InstructorLLM

本模块使用 legacy API（LangchainLLMWrapper + single_turn_score(SingleTurnSample)）。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
from typing import Any

from backend.shared.logger import logger

# ── RAGAS LLM 配置 ──────────────────────────────────────────────
# 默认使用 DashScope 云 API（qwen-plus），本地 Ollama 作为可选 fallback
_RAGAS_LLM_BACKEND = os.getenv("RAGAS_LLM_BACKEND", "cloud")  # "cloud" | "local"

# Cloud LLM (DashScope OpenAI 兼容端点)
_RAGAS_CLOUD_MODEL = os.getenv("RAGAS_CLOUD_MODEL", "qwen-plus")
_RAGAS_CLOUD_API_KEY = os.getenv("QWEN_API_KEY", "")
_RAGAS_CLOUD_API_BASE = os.getenv(
    "QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)

# Local LLM (Ollama fallback — 仅 RAGAS_LLM_BACKEND=local 时使用)
_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

# RAGAS 单 case 超时（秒）。Cloud API 4 个指标串行 LLM 调用，180s 留足余量。
_RAGAS_CASE_TIMEOUT = int(os.getenv("RAGAS_CASE_TIMEOUT", "180"))


# ── Monkey-patch: RAGAS 输出解析安全网（cloud 模型极少触发，local 小模型必需） ──

def _robust_extract_json(text: str) -> str:
    """增强版 extract_json — 处理小模型常见的格式问题。"""
    if not text or not text.strip():
        return "{}"

    stripped = text.strip()

    code_fence = stripped.find("```json")
    if code_fence != -1:
        stripped = stripped[code_fence + 7:]
        end_fence = stripped.rfind("```")
        if end_fence > 0:
            stripped = stripped[:end_fence]
        stripped = stripped.strip()

    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            pass

        repaired = _repair_json_string(stripped)
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            pass

    json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", stripped, re.DOTALL)
    if json_match:
        candidate = json_match.group()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return stripped


def _repair_json_string(text: str) -> str:
    """尝试修复常见的 JSON 格式问题。"""
    repaired = text.replace("'", '"')
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    depth_b = 0
    depth_s = 0
    in_string = False
    escape = False
    for ch in repaired:
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_b += 1
        elif ch == "}":
            depth_b -= 1
        elif ch == "[":
            depth_s += 1
        elif ch == "]":
            depth_s -= 1

    if depth_s > 0:
        repaired += "]" * depth_s
    if depth_b > 0:
        repaired += "}" * depth_b

    return repaired


def _default_for_model(output_model_cls: type) -> Any:
    """根据 Pydantic 模型类创建中性默认实例（解析失败时的 fallback）。"""
    from pydantic import BaseModel
    if not isinstance(output_model_cls, type) or not issubclass(output_model_cls, BaseModel):
        return None

    cls_name = output_model_cls.__name__

    try:
        if cls_name == "NLIStatementOutput":
            return output_model_cls(statements=[])
        if cls_name == "StatementGeneratorOutput":
            return output_model_cls(statements=[""])
        if cls_name == "ContextRecallClassifications":
            return output_model_cls(classifications=[])
        if cls_name == "Verification":
            return output_model_cls(reason="parse fallback", verdict=0)
        if cls_name == "ResponseRelevanceOutput":
            return output_model_cls(question="", noncommittal=0)
        if cls_name == "StringIO":
            return output_model_cls(text="{}")

        return output_model_cls.model_construct()
    except Exception:
        return None


async def _patched_parse_output_string(
    self,
    output_string: str,
    prompt_value: Any,
    llm: Any,
    callbacks: Any,
    retries_left: int = 1,
) -> Any:
    """替换 RagasOutputParser.parse_output_string — 去掉 LLM retry，改用本地修复。

    必须是 async def：RAGAS 0.4.3 原版是协程，调用方用 await。
    """
    try:
        jsonstr = _robust_extract_json(output_string)
        return super(type(self), self).parse(jsonstr)
    except Exception:
        pass

    try:
        repaired = _repair_json_string(output_string)
        jsonstr = _robust_extract_json(repaired)
        return super(type(self), self).parse(jsonstr)
    except Exception:
        pass

    default = _default_for_model(self.pydantic_object)
    if default is not None:
        logger.debug(f"[RAGAS-patch] 使用默认实例: {type(default).__name__}")
        return default

    from ragas.prompt.pydantic_prompt import RagasOutputParserException
    raise RagasOutputParserException()


def _apply_ragas_patches() -> None:
    """应用所有 RAGAS monkey-patches。"""
    import ragas.prompt.pydantic_prompt as _pp

    _pp.extract_json = _robust_extract_json
    _pp.RagasOutputParser.parse_output_string = _patched_parse_output_string
    logger.info("[RAGAS-patch] 输出解析安全网已加载")


_apply_ragas_patches()

RAGAS_LEVELS: dict[str, list[str]] = {
    "basic": ["ContextRecall", "Faithfulness"],
    "standard": ["ContextRecall", "Faithfulness", "ContextPrecision", "AnswerRelevancy"],
    "full": [
        "ContextRecall", "Faithfulness", "ContextPrecision",
        "AnswerRelevancy", "AnswerCorrectness",
    ],
}

METRIC_GT_DEPS: dict[str, bool] = {
    "ContextRecall": True,
    "Faithfulness": False,
    "ContextPrecision": True,
    "AnswerRelevancy": False,
    "AnswerCorrectness": True,
}

_METRIC_KEY_MAP: dict[str, str] = {
    "ContextRecall": "ragas_context_recall",
    "Faithfulness": "ragas_faithfulness",
    "ContextPrecision": "ragas_context_precision",
    "AnswerRelevancy": "ragas_answer_relevancy",
    "AnswerCorrectness": "ragas_answer_correctness",
}

_METRIC_NEEDS_EMBED: set[str] = {"AnswerRelevancy", "AnswerCorrectness"}

# 全局 LLM/Embeddings 单例（避免每次调用重新初始化）
_llm_wrapper: Any = None
_embeddings: Any = None


def _init_cloud_llm() -> Any:
    """初始化 LangchainLLMWrapper(ChatOpenAI) — DashScope 云 API。"""
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    if not _RAGAS_CLOUD_API_KEY:
        raise RuntimeError(
            "RAGAS cloud LLM 需要 QWEN_API_KEY 环境变量，"
            "或设置 RAGAS_LLM_BACKEND=local 使用本地 Ollama"
        )

    chat = ChatOpenAI(
        model=_RAGAS_CLOUD_MODEL,
        temperature=0,
        max_tokens=4096,
        request_timeout=60,
        api_key=_RAGAS_CLOUD_API_KEY,
        base_url=_RAGAS_CLOUD_API_BASE,
    )
    return LangchainLLMWrapper(chat)


def _init_local_llm() -> Any:
    """初始化 LangchainLLMWrapper(ChatOllama) — 本地 Ollama（fallback）。"""
    from langchain_ollama import ChatOllama
    from ragas.llms import LangchainLLMWrapper

    chat = ChatOllama(
        model=_OLLAMA_MODEL,
        temperature=0,
        base_url=_OLLAMA_HOST,
        format="json",
        num_predict=1024,
    )
    return LangchainLLMWrapper(chat)


def _init_embeddings() -> Any:
    """初始化 LangchainEmbeddingsWrapper(HuggingFaceEmbeddings)，复用 RAG pipeline 单例。"""
    from backend.rag.embedding_singleton import get_embedding

    hf_emb = get_embedding()
    try:
        from ragas.embeddings import LangchainEmbeddingsWrapper
        return LangchainEmbeddingsWrapper(hf_emb)
    except ImportError:
        logger.warning("[RAGAS] LangchainEmbeddingsWrapper 不可用，使用 fallback")
        return _create_fallback_embeddings(hf_emb)


def _create_fallback_embeddings(hf_emb: Any) -> Any:
    """当 ragas.embeddings.LangchainEmbeddingsWrapper 不可用时的 fallback。"""
    from ragas.embeddings.base import BaseRagasEmbeddings

    class _FallbackEmbeddings(BaseRagasEmbeddings):
        def __init__(self, emb):
            self._embed = emb

        def embed_text(self, text: str) -> list[float]:
            return self._embed.embed_query(text)

        async def aembed_text(self, text: str) -> list[float]:
            return await self._embed.aembed_query(text)

    return _FallbackEmbeddings(hf_emb)


def _get_llm() -> Any:
    """获取全局 LLM 单例（懒初始化）。"""
    global _llm_wrapper
    if _llm_wrapper is None:
        if _RAGAS_LLM_BACKEND == "local":
            _llm_wrapper = _init_local_llm()
            logger.info(f"[RAGAS] LLM 初始化完成（本地 Ollama: {_OLLAMA_MODEL}）")
        else:
            _llm_wrapper = _init_cloud_llm()
            logger.info(f"[RAGAS] LLM 初始化完成（DashScope: {_RAGAS_CLOUD_MODEL}）")
    return _llm_wrapper


def _get_embeddings() -> Any:
    """获取全局 Embeddings 单例（懒初始化）。"""
    global _embeddings
    if _embeddings is None:
        _embeddings = _init_embeddings()
        logger.info("[RAGAS] Embeddings 初始化完成（全局单例）")
    return _embeddings


def compute_ragas_metrics(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
    reference_contexts: list[str] | None = None,
    level: str = "standard",
    llm_wrapper: Any,
    embeddings: Any,
) -> dict[str, float | None]:
    """计算 RAGAS 指标（legacy API: single_turn_score + SingleTurnSample）。

    根据 level 选取指标集；ground_truth 为空时自动移除依赖指标。
    """
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import (
        AnswerCorrectness,
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    cls_map = {
        "ContextRecall": ContextRecall,
        "Faithfulness": Faithfulness,
        "ContextPrecision": ContextPrecision,
        "AnswerRelevancy": AnswerRelevancy,
        "AnswerCorrectness": AnswerCorrectness,
    }

    metric_names = RAGAS_LEVELS.get(level, RAGAS_LEVELS["standard"])

    skipped = [n for n in metric_names if METRIC_GT_DEPS.get(n) and ground_truth is None]
    if skipped:
        logger.warning(f"[RAGAS] ground_truth 为空，跳过依赖指标: {skipped}")
    metric_names = [n for n in metric_names if not METRIC_GT_DEPS.get(n) or ground_truth is not None]

    sample = SingleTurnSample(
        user_input=question,
        response=answer,
        retrieved_contexts=contexts,
        reference=ground_truth or answer,
        reference_contexts=reference_contexts,
    )

    results: dict[str, float | None] = {}
    for name in metric_names:
        key = _METRIC_KEY_MAP[name]
        try:
            kwargs: dict[str, Any] = {"llm": llm_wrapper}
            if name in _METRIC_NEEDS_EMBED:
                kwargs["embeddings"] = embeddings
            metric = cls_map[name](**kwargs)
            val = float(metric.single_turn_score(sample))
            results[key] = round(val, 4) if val == val else None
        except Exception as e:
            logger.warning(f"[RAGAS] {name} 计算失败: {e}")
            results[key] = None

    return results


def _compute_impl(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
    reference_contexts: list[str] | None,
    level: str,
) -> dict[str, float | None]:
    """内部实现：初始化依赖并计算指标。"""
    llm = _get_llm()
    embeddings = _get_embeddings()
    return compute_ragas_metrics(
        question=question,
        answer=answer,
        contexts=contexts,
        ground_truth=ground_truth,
        reference_contexts=reference_contexts,
        level=level,
        llm_wrapper=llm,
        embeddings=embeddings,
    )


def compute_ragas_metrics_safe(
    *,
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
    reference_contexts: list[str] | None = None,
    level: str = "standard",
    timeout_seconds: int = 0,  # 0 = 使用默认 _RAGAS_CASE_TIMEOUT
) -> dict[str, float | None]:
    """公共 API：带超时保护的 RAGAS 指标计算（ThreadPoolExecutor）。

    LLM/Embeddings 使用全局单例（GPU 模型不重载）。
    超时通过 ThreadPoolExecutor + shutdown(wait=False) 实现。
    注意：不能用 ``with`` 语句，因为 __exit__ 会 shutdown(wait=True) 无限等待挂起线程。
    """
    if not contexts or not answer.strip():
        logger.warning("[RAGAS] contexts 或 answer 为空，跳过")
        return {}

    timeout = timeout_seconds if timeout_seconds > 0 else _RAGAS_CASE_TIMEOUT
    metric_names = RAGAS_LEVELS.get(level, RAGAS_LEVELS["standard"])
    null_result = {_METRIC_KEY_MAP[n]: None for n in metric_names}

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            _compute_impl,
            question=question,
            answer=answer,
            contexts=contexts,
            ground_truth=ground_truth,
            reference_contexts=reference_contexts,
            level=level,
        )
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning(f"[RAGAS] 打分超时 ({timeout}s)，跳过记 null")
        return null_result
    except Exception as e:
        logger.warning(f"[RAGAS] 计算失败: {e}")
        return null_result
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def reset_caches() -> None:
    """重置全局 LLM/Embeddings 单例（用于测试或重新配置后）。"""
    global _llm_wrapper, _embeddings
    _llm_wrapper = None
    _embeddings = None
