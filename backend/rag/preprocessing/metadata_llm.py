"""metadata_llm.py — 阶段2：元数据统一 LLM 抽取（主流化改造）。

职责：在规则分类确定后，按预算门禁可选调用一次 LLM，增强摘要、精确
专名实体、风险信号和时间信息。Schema 保留 doc_type/business_domain 等
历史字段，但调用方始终用规则分类覆盖模型返回值；关键词不另起模型调用。

契约：
  - extract_metadata_llm() 成功 → 返回兼容字段子集及 Provider/usage 证据；
  - 任何失败（超时/坏 JSON/doc_type 越界）→ 返回 None，调用方走原规则路径。
"""
import hashlib
import json
import re
import asyncio
import threading
import weakref
import os
import time
from contextvars import ContextVar
from functools import partial

# LLM 背压槽位等待超时（秒）：等待超过此值计一次可观测降级并直接执行，
# 避免元数据抽取在并发洪峰下无限排队（P2 审查修复 2026-10-19）。
METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT = float(
    os.getenv("METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT", "10"))

# LLM 背压信号量按事件循环管理（P2 审查修复 2026-10-19）：
# asyncio.Semaphore 绑定创建时的 loop，跨 loop 复用会抛
# "bound to a different event loop"；weakref 键在 loop 销毁后自动回收。
_llm_semaphores_by_loop: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_llm_semaphores_lock = threading.Lock()
_last_metadata_call_meta: ContextVar[dict] = ContextVar(
    "metadata_extract_last_call_meta", default={}
)


def get_last_metadata_call_meta() -> dict:
    """读取本次元数据抽取调用证据，包含失败时已消耗的 Provider 用量。"""
    return dict(_last_metadata_call_meta.get() or {})


def _set_last_metadata_call_meta(**values) -> None:
    current = dict(_last_metadata_call_meta.get() or {})
    current.update(values)
    _last_metadata_call_meta.set(current)


def _get_loop_llm_semaphore() -> "asyncio.Semaphore":
    loop = asyncio.get_running_loop()
    width = max(int(os.getenv("METADATA_LLM_CONCURRENCY", "8")), 1)
    with _llm_semaphores_lock:
        entry = _llm_semaphores_by_loop.get(loop)
        if entry is None or entry[0] != width:
            entry = (width, asyncio.Semaphore(width))
            _llm_semaphores_by_loop[loop] = entry
        return entry[1]


from backend.config.llm import LLM_REQUEST_TIMEOUT
from backend.infra.async_utils import async_safe_call_with_timeout
from backend.observability.metrics import metadata_route_total
from backend.observability.tracer import SpanKind, trace_collector
from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
from backend.shared.logger import logger


class MetadataExtractError(Exception):
    """抽取失败信号（调用方据此降级到规则路径）。"""


def _normalize_usage_metadata(raw_usage) -> dict:
    """把不同 OpenAI 兼容供应商的 usage 字段统一成平台口径。"""
    if not isinstance(raw_usage, dict) or not raw_usage:
        return {}

    prompt = raw_usage.get("prompt_tokens", raw_usage.get("input_tokens"))
    completion = raw_usage.get(
        "completion_tokens", raw_usage.get("output_tokens")
    )
    normalized = {}
    if prompt is not None:
        normalized["prompt_tokens"] = int(prompt or 0)
    if completion is not None:
        normalized["completion_tokens"] = int(completion or 0)
    total = raw_usage.get("total_tokens")
    if total is not None:
        normalized["total_tokens"] = int(total or 0)
    elif "prompt_tokens" in normalized or "completion_tokens" in normalized:
        normalized["total_tokens"] = (
            int(normalized.get("prompt_tokens") or 0)
            + int(normalized.get("completion_tokens") or 0)
        )

    for key in ("cached_tokens", "reasoning_tokens"):
        if raw_usage.get(key) is not None:
            normalized[key] = int(raw_usage[key] or 0)
    if raw_usage.get("cost_usd") is not None:
        normalized["cost_usd"] = float(raw_usage["cost_usd"] or 0.0)
    return normalized


def _strip_code_fence(text: str) -> str:
    """剥掉 LLM 可能包裹的 ```json ... ``` 围栏。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行 ```json / ```，去掉尾行 ```
        lines = t.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines).strip()
    return t


def _first_json_object(text: str) -> str:
    """从回复中截取第一个平衡的 {...}（容错模型在 JSON 前后夹杂说明）。"""
    start = text.find("{")
    if start < 0:
        raise MetadataExtractError("no json object in response")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise MetadataExtractError("unbalanced json object")


def parse_extract_response(content: str, valid_types: set[str]) -> dict:
    """解析并校验 LLM 抽取结果。非法输出抛 MetadataExtractError。

    校验逻辑收敛到 metadata_schema.UnifiedMetadata（规划阶段 2.1：统一 schema
    是唯一契约）；valid_types 仍以调用方运行时派生的集合为准（DOC_TYPE_RULES
    ∪ general），与 schema 枚举由一致性测试锁定同源。
    """
    from pydantic import ValidationError

    from backend.rag.preprocessing.metadata_schema import UnifiedMetadata

    obj = json.loads(_first_json_object(_strip_code_fence(content)))
    if not isinstance(obj, dict):
        raise MetadataExtractError("response is not a json object")

    try:
        model = UnifiedMetadata.model_validate(obj)
    except ValidationError as e:
        # doc_type 越界等结构性错误 → 与旧行为一致按抽取失败处理
        raise MetadataExtractError(f"schema validation failed: {e.errors()[0]['msg']}") from e

    if model.doc_type not in valid_types:
        raise MetadataExtractError(f"doc_type out of enum: {model.doc_type!r}")

    return model.to_extract_dict()


def extract_metadata_llm(
    full_text: str,
    filename: str,
    parent_span_id: str = "",
) -> dict | None:
    """同步入口（供线程池/非 async 场景）：内部起 loop 跑 async 版本。

    注意：不能在已有事件循环的线程里调用（会 RuntimeError），
    async 上下文请直接 await extract_metadata_llm_async()。
    """
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        raise RuntimeError(
            "extract_metadata_llm 不能在运行中的事件循环里调用，"
            "请改用 await extract_metadata_llm_async()"
        )
    return asyncio.run(extract_metadata_llm_async(full_text, filename, parent_span_id))


async def extract_metadata_llm_async(
    full_text: str,
    filename: str,
    parent_span_id: str = "",
    include_summary: bool = True,
) -> dict | None:
    """单次 LLM 调用抽齐文档级元数据（async，供 _build_doc_metadata await）。

    走 invoke_metadata_llm（云端 proxy，qwen 关思考模式，自动计量落库）。
    成功返回字段子集（不含 minhash 等规则产物），失败返回 None。
    """
    _last_metadata_call_meta.set({})
    if not full_text or not full_text.strip():
        _set_last_metadata_call_meta(
            provider_call_attempted=False,
            provider_called=False,
            llm_tokens={},
            llm_usage_status="unavailable",
            fallback_reason="empty_input",
        )
        return None

    from backend.config.rag import (
        METADATA_LLM_EXTRACT_MAX_CHARS,
        METADATA_LLM_EXTRACT_MAX_OUTPUT_TOKENS,
    )
    from backend.rag.preprocessing.taxonomy_spec import get_taxonomy

    taxonomy = get_taxonomy()
    valid_types = set(taxonomy.doc_types)

    # 采样：章节感知采样（P1，2026-10-24）——超预算文档保留章节标题目录
    # 与中段分层采样，不再退回「头 60% + 中 20% + 尾 20%」的旧采样
    # （中间章节缺席曾致摘要/关键词偏差，2026-10-13 治理项的 LLM 侧收口）。
    from backend.rag.preprocessing.summary_sampling import sample_for_summary
    sample = sample_for_summary(
        full_text, max_chars=METADATA_LLM_EXTRACT_MAX_CHARS)

    try:
        from backend.prompts.service import prompt_service
        prompt_result = prompt_service.render_sync(
            "rag.preprocessing.metadata_extract",
            filename=filename or "(unknown)",
            text=sample,
        )
        prompt = prompt_result.text
        if not include_summary:
            prompt += (
                "\n\n本文件不需要文档级摘要。JSON 的 summary 字段必须返回空字符串，"
                "不要概括或改写问答内容。"
            )
        prompt_version = getattr(prompt_result, "version", None)
        # STOP B3/B4：显式记录 Prompt 来源与内容指纹。version 为 int = DB
        # active 版本（seed/publish 产生）；None = 内置 defaults（DB 缺失或
        # 未 seed），必须 warning 显式暴露，不允许静默 fallback。
        prompt_source = "db" if isinstance(prompt_version, int) else "yaml"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        if prompt_source != "db":
            logger.warning(
                "[MetaLLM] prompt_fallback=true key=rag.preprocessing.metadata_extract "
                f"source={prompt_source}（DB 无 active 版本，回退内置 defaults）"
            )
    except Exception as e:
        logger.warning(f"[MetaLLM] 渲染抽取提示词失败（降级规则路径）: {e}")
        _set_last_metadata_call_meta(
            provider_call_attempted=False,
            provider_called=False,
            llm_tokens={},
            llm_usage_status="unavailable",
            fallback_reason="prompt_render_error",
            error_message=str(e)[:200],
        )
        return None

    from backend.observability.tracer import trace_collector
    span_id = None
    if parent_span_id:
        from backend.observability.tracer import SpanKind
        span_id = trace_collector.start_span(
            "metadata_llm_extract", parent_id=parent_span_id,
            name="LLM metadata extract (unified)",
            type="llm_call", kind=SpanKind.INDEX_METADATA.value,
        )

    provider_call_attempted = False
    provider_called = False
    provider_duration_ms = 0
    response = None
    try:
        # invoke_metadata_llm 是同步调用，async_safe_call_with_timeout 会把它
        # 放线程池执行并施加超时（与摘要路径 build_llm_summary_cached 同款）；
        # 二者均为模块级导入，测试可直接 monkeypatch。
        # P0-3（2026-10-19）：原 metadata_runtime.run_limited 随级联退役，
        # 此处内联等价的 LLM 并发背压（宽度=METADATA_LLM_CONCURRENCY）。
        # P2 修复（审查 2026-10-19）：asyncio.Semaphore 绑定创建时的事件循环，
        # 模块级单例在同步入口多次 asyncio.run() 的场景下会跨循环复用并抛
        # "bound to a different event loop"（审查复现：第二轮降级规则抽取）。
        # 改为按事件循环管理（模块级 weakref 注册表，循环销毁自动回收），
        # 并保留槽位等待超时（超时计一次可观测降级，不经背压直接执行）。
        sem = _get_loop_llm_semaphore()
        from backend.observability.metrics import metadata_llm_calls_total
        slot_acquired = False
        try:
            await asyncio.wait_for(sem.acquire(), METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT)
            slot_acquired = True
        except asyncio.TimeoutError:
            metadata_llm_calls_total.labels(result="llm_slot_wait_timeout").inc()
            logger.warning(
                f"[MetaLLM] LLM 背压槽位等待超时 "
                f"({METADATA_LLM_SEMAPHORE_WAIT_TIMEOUT}s)，本次不经背压直接执行")
        provider_started = time.monotonic()
        provider_call_attempted = True
        try:
            response = await async_safe_call_with_timeout(
                partial(
                    invoke_metadata_llm,
                    max_tokens=METADATA_LLM_EXTRACT_MAX_OUTPUT_TOKENS,
                ),
                LLM_REQUEST_TIMEOUT,
                None,
                f"元数据抽取 LLM 超时 ({LLM_REQUEST_TIMEOUT}s)",
                prompt,
            )
        finally:
            provider_duration_ms = round((time.monotonic() - provider_started) * 1000)
            if slot_acquired:
                sem.release()
        provider_called = response is not None
        if response is None:
            _set_last_metadata_call_meta(
                provider_call_attempted=provider_call_attempted,
                provider_called=False,
                provider_duration_ms=provider_duration_ms,
                llm_tokens={},
                llm_usage_status="unavailable",
                prompt_version=(
                    f"v{prompt_version}" if isinstance(prompt_version, int) else "default"
                ),
                prompt_source=prompt_source,
                prompt_hash=prompt_hash,
                fallback_reason="provider_timeout",
            )
            raise MetadataExtractError("llm timeout")

        call_tokens = _normalize_usage_metadata(
            getattr(response, "usage_metadata", {}) or {}
        )
        response_metadata = getattr(response, "response_metadata", {}) or {}
        actual_model = (
            response_metadata.get("model_name")
            or response_metadata.get("model")
            or ""
        )
        _set_last_metadata_call_meta(
            provider_call_attempted=provider_call_attempted,
            provider_called=provider_called,
            provider_duration_ms=provider_duration_ms,
            llm_tokens=call_tokens,
            llm_usage_status="reported" if call_tokens else "unavailable",
            actual_model=str(actual_model),
            prompt_version=(
                f"v{prompt_version}" if isinstance(prompt_version, int) else "default"
            ),
            prompt_source=prompt_source,
            prompt_hash=prompt_hash,
        )
        content = response.content if hasattr(response, "content") else str(response)
        result = parse_extract_response(content, valid_types)
        if not include_summary:
            result["summary"] = ""
        result["prompt_version"] = (
            f"v{prompt_version}" if isinstance(prompt_version, int) else "default"
        )
        result["prompt_source"] = prompt_source
        result["prompt_hash"] = prompt_hash
        result["llm_tokens"] = call_tokens
        result["llm_usage_status"] = (
            "reported" if result["llm_tokens"] else "unavailable"
        )
        result["provider_called"] = provider_called
        result["provider_call_attempted"] = provider_call_attempted
        result["provider_duration_ms"] = provider_duration_ms
        if actual_model:
            # 仅保存模型标识，不保存完整响应；由决策信封带到处理血缘。
            result["actual_model"] = str(actual_model)
        if span_id:
            trace_collector.end_span(span_id, status="success", metrics={
                "provider_call": provider_called,
                "provider_duration_ms": provider_duration_ms,
                "usage_status": result["llm_usage_status"],
                "model": str(actual_model),
                "prompt_tokens": int(result["llm_tokens"].get("prompt_tokens") or 0),
                "completion_tokens": int(result["llm_tokens"].get("completion_tokens") or 0),
                "reasoning_tokens": int(result["llm_tokens"].get("reasoning_tokens") or 0),
                "total_tokens": int(result["llm_tokens"].get("total_tokens") or 0),
                "doc_type": result["doc_type"],
                "confidence": result["confidence"],
                "keywords": len(result["keywords"]),
                "risk_level": (result.get("risk") or {}).get("level", "none"),
            })
        metadata_route_total.labels(level="L3", outcome="hit").inc()
        metadata_llm_calls_total.labels(result="success").inc()
        return result
    except MetadataExtractError as e:
        logger.warning(f"[MetaLLM] 抽取结果非法（降级规则路径）: {e}")
        metadata_route_total.labels(level="L3", outcome="error").inc()
        from backend.observability.metrics import metadata_llm_calls_total
        metadata_llm_calls_total.labels(result="invalid").inc()
        fallback_reason = (
            "provider_timeout" if response is None else "invalid_output"
        )
        _set_last_metadata_call_meta(
            provider_call_attempted=provider_call_attempted,
            provider_called=provider_called,
            provider_duration_ms=provider_duration_ms,
            fallback_reason=fallback_reason,
            error_message=str(e)[:200],
        )
        if span_id:
            trace_collector.end_span(span_id, status="error",
                                     metrics={"provider_call": provider_called,
                                              "provider_call_attempted": provider_call_attempted,
                                              "provider_duration_ms": provider_duration_ms,
                                              "error": str(e)[:200]})
        return None
    except Exception as e:
        logger.warning(f"[MetaLLM] 抽取调用失败（降级规则路径）: {e}")
        metadata_route_total.labels(level="L3", outcome="error").inc()
        from backend.observability.metrics import metadata_llm_calls_total
        metadata_llm_calls_total.labels(result="error").inc()
        call_tokens = _normalize_usage_metadata(
            getattr(response, "usage_metadata", {}) or {}
        ) if response is not None else {}
        response_metadata = getattr(response, "response_metadata", {}) or {}
        _set_last_metadata_call_meta(
            provider_call_attempted=provider_call_attempted,
            provider_called=provider_called,
            provider_duration_ms=provider_duration_ms,
            llm_tokens=call_tokens,
            llm_usage_status="reported" if call_tokens else "unavailable",
            actual_model=str(
                response_metadata.get("model_name")
                or response_metadata.get("model")
                or ""
            ),
            prompt_version=(
                f"v{prompt_version}" if isinstance(prompt_version, int) else "default"
            ),
            prompt_source=prompt_source,
            prompt_hash=prompt_hash,
            fallback_reason="provider_error",
            error_message=str(e)[:200],
        )
        if span_id:
            trace_collector.end_span(span_id, status="error",
                                     metrics={"provider_call": provider_called,
                                              "provider_call_attempted": provider_call_attempted,
                                              "provider_duration_ms": provider_duration_ms,
                                              "error": str(e)[:200]})
        return None
