"""表格 chunk LLM 描述（4.3c）— 为行级 kv chunk 生成一句话语义描述。

背景：行级 kv chunk（"科目 货币资金 / Q3金额 1234567"）对 embedding 是
语义稀疏的数字序列，检索命中依赖词面重叠。本模块为表格行批量生成
"该行记录了什么" 的一句话描述，拼入 embedding 前缀【表格】段，
弥合「自然语言提问 ↔ 结构化数据行」的语义鸿沟。

成本控制：
- 只对 chunk_type=table_row 的 chunk 调用（量小）
- 每文档上限 TABLE_DESC_MAX_ROWS 行，超出部分无描述（前缀自动跳段）
- 一次 LLM 调用产出全部行描述（对齐 question_gen 模式）
- 走 invoke_metadata_llm（proxy 自动计量）；失败降级为空描述
"""
from __future__ import annotations

import json
import hashlib
import re
from contextvars import ContextVar

from backend.config.rag import (
    ENABLE_TABLE_DESCRIPTIONS,
    METADATA_CACHE_TTL_SECONDS,
    METADATA_SCHEMA_FINGERPRINT,
    TABLE_DESC_MAX_ROWS,
    TABLE_DESC_PROMPT_VERSION,
)
from backend.shared.logger import logger

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_last_generation_meta: ContextVar[dict] = ContextVar(
    "table_description_last_generation_meta", default={}
)


def _set_generation_meta(**values) -> None:
    _last_generation_meta.set(dict(values))


def get_last_generation_meta() -> dict:
    """返回本次调用的阶段状态和实际响应模型摘要。"""
    return dict(_last_generation_meta.get() or {})


def _read_actual_model() -> str:
    try:
        from backend.infra.llm.proxy import _last_call_meta_var

        return str((_last_call_meta_var.get() or {}).get("model") or "")
    except Exception:
        return ""


def _cache_version() -> str:
    """返回表格描述结果的模型/配置版本，避免跨模型复用旧描述。"""
    try:
        from backend.config import model_roles
        from backend.infra.llm.models import resolve_provider

        effective = model_roles.resolve_effective("table_describe")
        model = str(effective.get("value") or "")
        provider = resolve_provider(model) if model else ""
        source = str(effective.get("source") or "")
        revision = str(effective.get("updated_at") or "")
        return "|".join((provider, model, source, revision,
                         TABLE_DESC_PROMPT_VERSION, METADATA_SCHEMA_FINGERPRINT))
    except Exception as exc:
        logger.debug("[TableDesc] 读取缓存版本失败，按空版本处理: %s", exc)
        return f"|{TABLE_DESC_PROMPT_VERSION}|{METADATA_SCHEMA_FINGERPRINT}"


def _cache_key(scope: list[str], table_summary: str) -> str:
    payload = "\x00".join((_cache_version(), table_summary or "", *scope))
    return "table_desc:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict[int, str] | None:
    try:
        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis

        client = get_redis()
        if client is None:
            return None
        raw = client.get(f"{REDIS_KEY_PREFIX}{key}")
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        return {int(index): str(description) for index, description in value.items()}
    except Exception as exc:
        logger.debug("[TableDesc] 缓存读取失败（按 miss）: %s", exc)
        return None


def _cache_put(key: str, value: dict[int, str]) -> None:
    try:
        from backend.config.redis import REDIS_KEY_PREFIX
        from backend.infra.redis.client import get_redis

        client = get_redis()
        if client is None:
            return
        client.setex(
            f"{REDIS_KEY_PREFIX}{key}",
            int(METADATA_CACHE_TTL_SECONDS),
            json.dumps(value, ensure_ascii=False),
        )
    except Exception as exc:
        logger.debug("[TableDesc] 缓存写入失败: %s", exc)


def _invoke_llm(prompt: str, llm_obj=None):
    """LLM 调用桩（测试 monkeypatch 点）——走 proxy 自动计量。"""
    from backend.rag.preprocessing.llm_enrichment import invoke_metadata_llm
    return invoke_metadata_llm(prompt, llm_obj=llm_obj, role="table_describe")


def _build_prompt(kv_texts: list[str], table_summary: str) -> str:
    lines = "\n".join(f"[行{i}] {kv[:200]}" for i, kv in enumerate(kv_texts))
    return (
        "你是知识库检索索引构建助手。以下是一张表格中的若干数据行"
        "（kv 格式：列名 值）。\n"
        f"表级信息：{table_summary or '（无）'}\n\n"
        f"{lines}\n\n"
        "请为每一行生成一句自然的中文描述（20-40 字），说明该行数据"
        "记录了什么业务含义，便于用自然语言检索到它。\n"
        "只输出 JSON：{\"descriptions\": [\"...\", ...]}，"
        f"数组长度必须等于 {len(kv_texts)}。"
    )


def generate_table_descriptions(kv_texts: list[str],
                                table_summary: str = "") -> dict[int, str]:
    """为行级 kv chunk 批量生成描述。

    Returns:
        {行索引: 描述}；功能关闭/失败/长度不符时返回 {}（调用方无前缀）。
    """
    if not kv_texts:
        _set_generation_meta(status="skipped", skip_reason="no_table_rows")
        return {}
    if not ENABLE_TABLE_DESCRIPTIONS:
        _set_generation_meta(status="skipped", skip_reason="feature_disabled")
        return {}

    scope = kv_texts[:TABLE_DESC_MAX_ROWS]
    cache_key = _cache_key(scope, table_summary)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("[TableDesc] 缓存命中 %s 行", len(cached))
        _set_generation_meta(status="cached", cache_status="hit")
        return cached
    prompt = _build_prompt(scope, table_summary)
    try:
        result = _invoke_llm(prompt)
        content = result.content.strip() if hasattr(result, "content") else str(result)
        actual_model = _read_actual_model()
        match = _JSON_RE.search(content)
        if not match:
            _set_generation_meta(
                status="fallback", fallback_reason="invalid_response",
                model=actual_model,
            )
            return {}
        data = json.loads(match.group())
        raw = data.get("descriptions")
        if not isinstance(raw, list) or len(raw) != len(scope):
            logger.warning(
                "[TableDesc] descriptions 长度不符 (%s/%s)，放弃描述",
                len(raw) if isinstance(raw, list) else 0, len(scope),
            )
            _set_generation_meta(
                status="fallback", fallback_reason="invalid_response",
                model=actual_model,
            )
            return {}
        cleaned = {
            i: str(d).strip()[:80]
            for i, d in enumerate(raw)
            if str(d).strip() and len(str(d).strip()) >= 4
        }
        logger.info(f"[TableDesc] 生成 {len(cleaned)}/{len(scope)} 行描述")
        _cache_put(cache_key, cleaned)
        _set_generation_meta(
            status="success", cache_status="miss", model=actual_model,
        )
        return cleaned
    except Exception as e:
        logger.warning(f"[TableDesc] LLM 调用失败（无描述降级）: {type(e).__name__}: {e}")
        _set_generation_meta(status="fallback", fallback_reason="llm_error")
        return {}
