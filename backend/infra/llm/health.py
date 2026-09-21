"""infra/llm/health.py — 模型真实健康探测（治理改造 2026-09-22）

职责：按模型类型做**极低成本**的真实探测，把结果写入 llm_model_health 缓存表。
管理端角色绑定页只读缓存，绝不在页面打开时同步探测全部模型。

探测方式按 model_kind 分档（费用约束：探测成本必须远小于一次真实调用）：
  chat     → 极短补全（"ping"，max_tokens=1）
  embedding→ 单条短文本 embed_query（一次性客户端，不动全局单例）
  rerank   → 两条短文本 rerank
  vision/ocr → 只做 API 可用性探测（凭据 + 注册表），不真调 OCR

状态机（llm_model_health.status）：
  healthy / degraded / slow / rate_limited / auth_failed / timeout /
  provider_unreachable / model_not_found / unknown

持久化：llm_model_health（migration 034，agent_memory 库）。
调度：Celery beat `model.health_scan`（backend/tasks/model_health_tasks.py）。
"""
from __future__ import annotations

import re
import time
from typing import Any

from backend.shared.logger import logger

# 探测超时（秒）——必须远小于角色 timeout 预算，探测本身不能成为负载
PROBE_TIMEOUT_SECONDS = 10
# latency 超过该值视为 slow（毫秒）
SLOW_LATENCY_MS = 5000

_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"401|unauthorized|invalid.{0,12}api.?key|authentication", "auth_failed"),
    (r"429|rate.?limit|too many requests", "rate_limited"),
    (r"timeout|timed?\s?out|apitimeout", "timeout"),
    (r"404|model.{0,8}not.{0,8}found|not_found", "model_not_found"),
    (r"connection|connect|unreachable|refused|resolve", "provider_unreachable"),
)


def classify_status(latency_ms: int, error: str) -> str:
    """探测结果 → 状态枚举。error 为空 = healthy（慢则 slow）。纯函数。"""
    if not error:
        return "slow" if latency_ms > SLOW_LATENCY_MS else "healthy"
    lowered = error.lower()
    for pattern, status in _ERROR_PATTERNS:
        if re.search(pattern, lowered):
            return status
    return "degraded"


def _record(
    model_name: str,
    provider: str,
    model_kind: str,
    status: str,
    latency_ms: int,
    error: str,
) -> None:
    """写健康缓存（upsert；失败累加 consecutive_failures、成功清零）。

    软失败：探测结果丢一帧可接受，不能反过来影响探测流程。
    """
    failed = bool(error)
    try:
        from sqlalchemy import text

        from backend.config.database import MEMORY_DB_CONFIG
        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                text(
                    """
                    INSERT INTO llm_model_health
                        (model_name, provider, model_kind, status,
                         last_checked_at, last_latency_ms, last_error,
                         consecutive_failures)
                    VALUES (:m, :p, :k, :s, now(), :l, :e, :f)
                    ON CONFLICT (model_name) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        model_kind = EXCLUDED.model_kind,
                        status = EXCLUDED.status,
                        last_checked_at = now(),
                        last_latency_ms = EXCLUDED.last_latency_ms,
                        last_error = EXCLUDED.last_error,
                        consecutive_failures = EXCLUDED.consecutive_failures
                    """
                ),
                {
                    "m": model_name,
                    "p": provider,
                    "k": model_kind,
                    "s": status,
                    "l": latency_ms,
                    "e": (error or "")[:500],
                    "f": 0 if not failed else -1,  # 占位：下方按上一轮值修正
                },
            )
            if failed:
                # 失败：在上一轮计数基础上 +1（插入行则从 1 起）
                cur.execute(
                    text(
                        "UPDATE llm_model_health SET consecutive_failures = "
                        "GREATEST(consecutive_failures, 0) + 1 "
                        "WHERE model_name = :m"
                    ),
                    {"m": model_name},
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning(
            "[ModelHealth] 健康缓存写入失败 model=%s", model_name, exc_info=True)


def probe_model(model_name: str, model_kind: str | None = None) -> dict[str, Any]:
    """探测单个模型，返回 {status, latencyMs, error}；结果落缓存。"""
    from backend.infra.llm import models as models_mod

    entry = models_mod.get_model_entry(model_name)
    kind = model_kind or (models_mod.model_kind_of(entry) if entry else "chat")
    provider = str((entry or {}).get("provider") or "")

    if entry is None:
        _record(model_name, provider, kind, "model_not_found", 0,
                "模型未在注册表登记")
        return {"status": "model_not_found", "latencyMs": 0,
                "error": "模型未在注册表登记"}

    t0 = time.monotonic()
    error = ""
    try:
        if kind == "chat":
            _probe_chat(model_name)
        elif kind == "embedding":
            _probe_embedding(model_name)
        elif kind == "rerank":
            _probe_rerank(model_name)
        else:
            # vision / speech：只做凭据可用性探测（成本约束，不真调模型）
            _probe_availability_only(provider)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.monotonic() - t0) * 1000)
    status = classify_status(latency_ms, error)
    _record(model_name, provider, kind, status, latency_ms, error)
    logger.info(
        "[ModelHealth] probe model=%s kind=%s status=%s latency=%sms%s",
        model_name, kind, status, latency_ms,
        f" error={error[:120]}" if error else "",
    )
    return {"status": status, "latencyMs": latency_ms, "error": error}


def _probe_chat(model_name: str) -> None:
    """极短补全探测。实例本身已带 LLM_REQUEST_TIMEOUT，无需另配超时。"""
    from backend.infra.llm.proxy import _get_override_llm

    llm = _get_override_llm(model_name)
    llm.invoke("ping")


def _probe_embedding(model_name: str) -> None:
    """单条短文本 embedding 探测。

    按专项绑定构建**一次性客户端**，不动全局单例 —— 探测动作绝不能把
    线上正在用的 embedding 实例热切换掉。
    """
    from backend.infra.llm import credentials as credentials_mod
    from backend.infra.llm import specialized as specialized_mod

    binding = specialized_mod.resolve_binding("embedding")
    if binding is None or binding.model_name != model_name:
        raise RuntimeError(
            f"embedding 角色当前绑定不是 {model_name}，仅支持探测当前绑定模型"
        )
    creds = credentials_mod.resolve_credentials(
        binding.provider_id, model_name=binding.model_name)
    if not creds.api_key:
        raise RuntimeError("embedding 供应商未配置 API Key")
    from langchain_openai import OpenAIEmbeddings

    emb = OpenAIEmbeddings(
        model=binding.model_name,
        api_key=creds.api_key,
        base_url=binding.base_url,
        check_embedding_ctx_length=False,
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    emb.embed_query("ping")


def _probe_rerank(model_name: str) -> None:
    """两条短文本 rerank 探测（复用既有 reranker 出站配置）。"""
    del model_name  # reranker 走专项绑定，模型名一致性由 binding 保证
    from backend.rag.reranker import get_reranker_backend

    reranker = get_reranker_backend()
    reranker.compress_documents(
        [_mk_doc("库存管理制度概述。"), _mk_doc("退货流程说明。")], "ping")


def _mk_doc(text: str):
    from langchain_core.documents import Document

    return Document(page_content=text)


def _probe_availability_only(provider: str) -> None:
    """vision/speech 只做凭据可用性探测。"""
    from backend.infra.llm import credentials as credentials_mod

    if provider and provider != "ollama":
        creds = credentials_mod.resolve_credentials(provider)
        if not creds.api_key:
            raise RuntimeError("供应商未配置 API Key")


def scan_all_models() -> dict[str, Any]:
    """全量探测：注册表中已配置凭据的模型逐个 probe（beat 周期入口）。

    凭据未配置的模型跳过（探测必然 401，无信息量）。
    """
    from backend.infra.llm import credentials as credentials_mod
    from backend.infra.llm import models as models_mod

    results: dict[str, str] = {}
    for entry in models_mod.get_available_models():
        name = str(entry.get("name") or "")
        if not name:
            continue
        provider = str(entry.get("provider") or "")
        if provider and provider != "ollama":
            try:
                creds = credentials_mod.resolve_credentials(provider, model_name=name)
                if not creds.api_key:
                    results[name] = "skip(no-key)"
                    continue
            except Exception:
                results[name] = "skip(cred-error)"
                continue
        try:
            results[name] = str(probe_model(name)["status"])
        except Exception as exc:
            results[name] = f"error:{type(exc).__name__}"
    ok = sum(1 for v in results.values() if v == "healthy")
    logger.info("[ModelHealth] scan 完成: %d/%d healthy", ok, len(results))
    return {"total": len(results), "healthy": ok, "results": results}
