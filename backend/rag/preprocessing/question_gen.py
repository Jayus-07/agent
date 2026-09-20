"""模拟问题生成（Document Expansion）— S0 修复产物。

背景（2026-09-14 发现的静默失效）：
F1 重构把 `_build_doc_metadata` 的 summary/keywords/entities 三路并发化时
漏迁了第四任务（问题生成），`enrich_metadata_llm` 失去全部调用方，
`questions_by_chunk` 恒为空 → chunk metadata 永远不写 simulated_questions →
`IncrementalIndexer._embed_text_for` 的「【相关问题】」前缀在生产链路
永不触发，注释宣称的召回收益（+10-15%）实际为死特性。

本模块以独立轻量职责恢复该能力：
- LLM 路径：一次调用，prompt 只带 chunk 预览（300 字/chunk）。
  旧合并调用（enrich_metadata_llm）需携带全文+全部 chunk，长文档 prompt
  巨大且长度不符即整单 fallback——这是它被摘掉的真实原因，本模块按
  chunk 独立校验，坏一块不影响其余。
- 规则降级：LLM 失败/未配置/长度不符 → 无害占位问句（不引入召回噪声）。
- 计量：统一走 `invoke_metadata_llm`（proxy 层自动落 llm_usage_store），
  tokens 经返回值 (questions, tokens) 回传给 indexer 汇总——
  2026-09-16 由模块级 LAST_QUESTION_GEN_TOKENS 改为返回值回传：
  信号量限并发 ≤2 下模块级全局会被并发上传互相覆盖（与 proxy.py
  ContextVar 重构前同款问题）。

对接契约（被 tests/rag/test_simulated_questions_pipeline.py 锁定）：
    generate_chunk_questions(chunks_text, doc_type="general")
        -> tuple[list[list[str]], dict]   # (questions_by_chunk, tokens)
    ENABLE_SIMULATED_QUESTIONS / QUESTION_GEN_MAX_CHUNKS / _invoke_llm
    均为模块级可 monkeypatch 属性。
"""
from __future__ import annotations

import json
import re
from contextvars import ContextVar
from typing import Callable, Optional

from backend.shared.logger import logger

# ---- 模块级开关（测试 monkeypatch 点） ----
from backend.config.rag import (
    ENABLE_SIMULATED_QUESTIONS,
    QUESTION_GEN_MAX_CHUNKS,
    QUESTION_GEN_PROMPT_VERSION,
)

_LAST_META_CLEANUP_RE = re.compile(r"\{.*\}", re.DOTALL)
_last_generation_meta: ContextVar[dict] = ContextVar(
    "question_generation_last_meta", default={}
)


def _set_generation_meta(**values) -> None:
    _last_generation_meta.set(dict(values))


def get_last_generation_meta() -> dict:
    """返回本次问题生成的阶段状态，不改变历史 tokens 返回契约。"""
    return dict(_last_generation_meta.get() or {})


def _fallback_questions(chunk_text: str) -> list[str]:
    """规则降级：无害占位问句。

    设计原则与旧 _fallback_questions 一致：宁可该 chunk 召回率低，
    也不让错误问题把不相关问句召回来。空 chunk 返回空列表
    （调用方对空列表不写 metadata，避免 ChromaDB 非空列表校验报错）。
    """
    first = re.split(r"[。！？；\n]", (chunk_text or "").strip(), maxsplit=1)[0].strip()
    if not first:
        return []
    return ["这段内容讲什么？"]


def _extract_json_questions(content: str, expected_len: int,
                            chunks_text: list[str]) -> Optional[list[list[str]]]:
    """解析 LLM 输出的 simulated_questions。

    Returns:
        合法的 questions_by_chunk；完全不可用返回 None（调用方整体降级）。
    """
    match = _LAST_META_CLEANUP_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except (ValueError, TypeError):
        return None
    raw = data.get("simulated_questions") if isinstance(data, dict) else None
    if not isinstance(raw, list) or len(raw) != expected_len:
        logger.warning(
            "[QuestionGen] simulated_questions 缺失或长度不符 (%s/%s)，整体规则降级",
            len(raw) if isinstance(raw, list) else 0, expected_len,
        )
        return None

    result: list[list[str]] = []
    for i, qs in enumerate(raw):
        if isinstance(qs, list):
            cleaned = [str(q).strip() for q in qs
                       if str(q).strip() and len(str(q).strip()) >= 4][:3]
            result.append(cleaned if cleaned else _fallback_questions(chunks_text[i]))
        else:
            result.append(_fallback_questions(chunks_text[i]))
    return result


def _build_prompt(chunks_text: list[str], doc_type: str) -> str:
    """构造问题生成 prompt——只带 chunk 预览，控制 prompt 体积。"""
    blocks = []
    for idx, ct in enumerate(chunks_text):
        preview = ct[:300].replace("\n", " ")
        blocks.append(f"[Chunk #{idx}]\n{preview}")
    chunks_block = "\n\n".join(blocks)
    return (
        "你是知识库检索索引构建助手。以下是某文档的各文本块（编号从 0 开始）。\n"
        f"文档类型：{doc_type}\n\n"
        f"{chunks_block}\n\n"
        "请为每个文本块生成 2 个口语化的用户提问，要求：\n"
        "- 严格基于该块内容，不引入块外信息\n"
        "- 只问「是什么/怎么样/标准是什么/如何规定」这类检索常见句式\n\n"
        "只输出 JSON，格式：\n"
        '{"simulated_questions": [["<问题1>", "<问题2>"], ...]}\n'
        f"外层数组长度必须等于 {len(chunks_text)}（文本块数量）。"
    )


def _invoke_llm(prompt: str, llm_obj=None):
    """LLM 调用桩（测试 monkeypatch 点）——默认走 invoke_metadata_llm。

    invoke_metadata_llm 内部走 backend.infra.llm proxy：
    qwen 云模型自动关思考模式，且 proxy 层自动计量落 llm_usage_store。
    """
    from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
    return invoke_metadata_llm(prompt, llm_obj=llm_obj, role="question_gen")


def _read_last_tokens() -> dict:
    """从 proxy ContextVar 读取本次调用的 token 用量（读失败不影响主流程）。"""
    try:
        from backend.infra.llm.proxy import _last_call_meta_var
        meta = _last_call_meta_var.get() or {}
        return {
            "prompt_tokens": int(meta.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(meta.get("completion_tokens", 0) or 0),
            "cost_usd": float(meta.get("cost_usd", 0) or 0),
        }
    except Exception as e:  # pragma: no cover - 计量读取失败仅降级
        logger.debug(f"[QuestionGen] token 读取失败（不影响主流程）: {e}")
        return {}


def _cache_version() -> str:
    """返回影响问题结果的角色、供应商、模型和契约版本。"""
    try:
        from backend.config import model_roles
        from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
        from backend.infra.llm.models import resolve_provider

        effective = model_roles.resolve_effective("question_gen")
        model = str(effective.get("value") or "")
        provider = resolve_provider(model) if model else ""
        source = str(effective.get("source") or "")
        revision = str(effective.get("updated_at") or "")
    except Exception as exc:
        logger.debug(f"[QuestionGen] 读取模型版本失败，使用空版本: {exc}")
        provider = model = source = revision = ""
        try:
            from backend.config.rag import METADATA_SCHEMA_FINGERPRINT
        except Exception:
            METADATA_SCHEMA_FINGERPRINT = ""
    return "|".join(("question_gen", provider, model, source, revision,
                     QUESTION_GEN_PROMPT_VERSION, METADATA_SCHEMA_FINGERPRINT))


def _cache_key(chunks_text: list[str], doc_type: str) -> str:
    """批级缓存键：内容、文档类型、实际模型和提示词版本共同寻址。

    内容寻址保证：同内容重索引/副本文档 → 相同问题 → 前缀稳定 →
    embedding 缓存（3.1）可命中。LLM 非确定性输出若不缓存，
    会在重索引时生成不同问题、击穿下游嵌入缓存（运行时验收发现）。
    """
    import hashlib
    joined = _cache_version() + "\x00" + doc_type + "\x00" + "\x00".join(chunks_text)
    return "question_gen:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> list[list[str]] | None:
    try:
        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return None
        raw = r.get(f"{REDIS_KEY_PREFIX}{key}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.debug(f"[QuestionGen] 缓存读取失败（按 miss）: {e}")
        return None


def _cache_put(key: str, value: list[list[str]]) -> None:
    try:
        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return
        r.setex(f"{REDIS_KEY_PREFIX}{key}", 7 * 86400,
                json.dumps(value, ensure_ascii=False))
    except Exception as e:
        logger.debug(f"[QuestionGen] 缓存写入失败: {e}")


def generate_chunk_questions(chunks_text: list[str],
                             doc_type: str = "general") -> tuple[list[list[str]], dict]:
    """为每个 chunk 生成模拟问题（Document Expansion）。

    Args:
        chunks_text: chunk 文本列表（与索引流水线的 chunks 一一对应）。
        doc_type: 文档类型（进 prompt 提升问题措辞相关性）。

    Returns:
        (questions_by_chunk, tokens) 二元组：
        - questions_by_chunk: 长度 == len(chunks_text)，每个元素是该 chunk 的
          1-3 个模拟问题。功能关闭时返回空列表（调用方跳过前缀注入）。
        - tokens: 本次 LLM 调用用量（prompt_tokens/completion_tokens/cost_usd），
          LLM 未调用或缓存命中时为 {}。
    """
    if not chunks_text:
        _set_generation_meta(status="skipped", skip_reason="no_chunks")
        return [], {}
    if not ENABLE_SIMULATED_QUESTIONS:
        _set_generation_meta(status="skipped", skip_reason="feature_disabled")
        return [], {}

    # 内容寻址缓存：同内容重索引/副本 → 相同问题（前缀稳定 → 嵌入缓存可命中）
    ckey = _cache_key(chunks_text, doc_type)
    cached = _cache_get(ckey)
    if cached is not None and len(cached) == len(chunks_text):
        logger.info(f"[QuestionGen] 缓存命中 {len(cached)} chunks")
        _set_generation_meta(status="cached", cache_status="hit")
        return cached, {}

    # 成本护栏：超长文档只对前 N chunk 走 LLM，其余规则兜底
    llm_scope = list(chunks_text[:QUESTION_GEN_MAX_CHUNKS])
    tail_scope = chunks_text[len(llm_scope):]

    fallback_result = [_fallback_questions(ct) for ct in chunks_text]
    if not llm_scope:
        _set_generation_meta(status="fallback", fallback_reason="llm_scope_empty")
        return fallback_result, {}

    prompt = _build_prompt(llm_scope, doc_type)
    try:
        result = _invoke_llm(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result)
        tokens = _read_last_tokens()
        actual_model = ""
        try:
            from backend.infra.llm.proxy import _last_call_meta_var

            actual_model = str((_last_call_meta_var.get() or {}).get("model") or "")
        except Exception:
            pass
        _set_generation_meta(status="success", cache_status="miss", model=actual_model)
        parsed = _extract_json_questions(content, len(llm_scope), llm_scope)
        if parsed is None:
            _set_generation_meta(
                status="fallback", fallback_reason="invalid_response",
                model=actual_model,
            )
            return fallback_result, tokens
        result_questions = parsed + [_fallback_questions(ct) for ct in tail_scope]
        _cache_put(ckey, result_questions)
        return result_questions, tokens
    except Exception as e:
        logger.warning(f"[QuestionGen] LLM 调用失败，规则降级: {type(e).__name__}: {e}")
        _set_generation_meta(status="fallback", fallback_reason="llm_error")
        return fallback_result, {}
