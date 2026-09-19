"""metadata_llm.py — 阶段2：元数据统一 LLM 抽取（主流化改造）。

背景：原链路元数据由 6+ 个独立步骤拼出——正则计分分类、LLM 胶着仲裁、
低置信复验、LLM 摘要、规则+LLM 关键词、正则实体/时间——步骤间强耦合
（doc_type 必须先算），且一次上传最多触发 4 类 LLM 调用。

主流做法（Contextual Extraction / schema-driven extraction）：单次 LLM
调用按 JSON schema 抽齐 doc_type / confidence / business_domain / summary /
keywords / entities / time_refs，正则链路整体降级为 fallback。

契约：
  - extract_metadata_llm() 成功 → 返回与 _build_doc_metadata 兼容的字段子集，
    调用方把 llm_used 置 True、来源标 metadata_llm；
  - 任何失败（超时/坏 JSON/doc_type 越界）→ 返回 None，调用方走原规则路径。
"""
import json
import re

from backend.config.llm import LLM_REQUEST_TIMEOUT
from backend.infra.async_utils import async_safe_call_with_timeout
from backend.observability.metrics import metadata_route_total
from backend.observability.tracer import SpanKind, trace_collector
from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
from backend.shared.logger import logger


class MetadataExtractError(Exception):
    """抽取失败信号（调用方据此降级到规则路径）。"""


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
) -> dict | None:
    """单次 LLM 调用抽齐文档级元数据（async，供 _build_doc_metadata await）。

    走 invoke_metadata_llm（云端 proxy，qwen 关思考模式，自动计量落库）。
    成功返回字段子集（不含 minhash/complexity 等规则产物），失败返回 None。
    """
    if not full_text or not full_text.strip():
        return None

    from backend.config.rag import METADATA_LLM_EXTRACT_MAX_CHARS
    from backend.rag.preprocessing.taxonomy_spec import get_taxonomy

    taxonomy = get_taxonomy()
    valid_types = set(taxonomy.doc_types)
    doc_types_str = ", ".join(taxonomy.doc_types)
    domains_str = ", ".join(taxonomy.domains)

    # 采样：头部 + 中部 + 尾部，兼顾标题区/正文/结尾签名区
    max_chars = METADATA_LLM_EXTRACT_MAX_CHARS
    if len(full_text) <= max_chars:
        sample = full_text
    else:
        head = full_text[: int(max_chars * 0.6)]
        mid_start = max(0, len(full_text) // 2 - int(max_chars * 0.1))
        mid = full_text[mid_start: mid_start + int(max_chars * 0.2)]
        tail = full_text[-int(max_chars * 0.2):]
        sample = f"{head}\n\n[...中间省略...]\n\n{mid}\n\n[...省略...]\n\n{tail}"

    try:
        from backend.prompts.service import prompt_service
        prompt_result = prompt_service.render_sync(
            "rag.preprocessing.metadata_extract",
            doc_types=doc_types_str,
            domains=domains_str,
            filename=filename or "(unknown)",
            text=sample,
        )
        prompt = prompt_result.text
        prompt_version = getattr(prompt_result, "version", None)
    except Exception as e:
        logger.warning(f"[MetaLLM] 渲染抽取提示词失败（降级规则路径）: {e}")
        return None

    from backend.observability.tracer import trace_collector
    span_id = None
    if parent_span_id:
        from backend.observability.tracer import SpanKind
        span_id = trace_collector.start_span(
            "metadata_llm_extract", parent_id=parent_span_id,
            name="LLM metadata extract (unified)",
            type="llm", kind=SpanKind.INDEX_METADATA.value,
        )

    try:
        # invoke_metadata_llm 是同步调用，async_safe_call_with_timeout 会把它
        # 放线程池执行并施加超时（与摘要路径 build_llm_summary_cached 同款）；
        # 二者均为模块级导入，测试可直接 monkeypatch
        response = await async_safe_call_with_timeout(
            invoke_metadata_llm,
            LLM_REQUEST_TIMEOUT,
            None,
            f"元数据抽取 LLM 超时 ({LLM_REQUEST_TIMEOUT}s)",
            prompt,
        )
        if response is None:
            raise MetadataExtractError("llm timeout")

        content = response.content if hasattr(response, "content") else str(response)
        result = parse_extract_response(content, valid_types)
        result["prompt_version"] = (
            f"v{prompt_version}" if isinstance(prompt_version, int) else "default"
        )
        result["llm_tokens"] = dict(
            getattr(response, "usage_metadata", {}) or {}
        ) or {}
        if span_id:
            trace_collector.end_span(span_id, status="success", metrics={
                "doc_type": result["doc_type"],
                "confidence": result["confidence"],
                "keywords": len(result["keywords"]),
                "risk_level": (result.get("risk") or {}).get("level", "none"),
            })
        metadata_route_total.labels(level="L3", outcome="hit").inc()
        return result
    except MetadataExtractError as e:
        logger.warning(f"[MetaLLM] 抽取结果非法（降级规则路径）: {e}")
        metadata_route_total.labels(level="L3", outcome="error").inc()
        if span_id:
            trace_collector.end_span(span_id, status="error",
                                     metrics={"error": str(e)[:200]})
        return None
    except Exception as e:
        logger.warning(f"[MetaLLM] 抽取调用失败（降级规则路径）: {e}")
        metadata_route_total.labels(level="L3", outcome="error").inc()
        if span_id:
            trace_collector.end_span(span_id, status="error",
                                     metrics={"error": str(e)[:200]})
        return None
