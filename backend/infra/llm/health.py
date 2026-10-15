"""infra/llm/health.py — 模型真实健康探测（治理改造 2026-09-22）

职责：按模型类型做**极低成本**的真实探测，把结果写入 llm_model_health 缓存表。
管理端角色绑定页只读缓存，绝不在页面打开时同步探测全部模型。

探测方式按 model_kind 分档（费用约束：探测成本必须远小于一次真实调用）：
  chat     → 极短补全（"ping"，max_tokens=1）
  embedding→ 单条短文本 embed_query（一次性客户端，不动全局单例）
  rerank   → 两条短文本 rerank
  vision    → API 可用性探测（凭据 + 注册表）
  ocr       → 仅管理员手动做最小上游调用；周期扫描不假报健康

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


_SAFE_STATUS_ERRORS = {
    "degraded": "模型探测未能完成，请检查模型与供应商配置",
    "slow": "模型响应较慢，请检查供应商状态",
    "rate_limited": "上游请求过于频繁，请稍后重试",
    "auth_failed": "上游鉴权失败，请检查 API Key、账户额度和模型权限",
    "timeout": "上游探测超时，请稍后重试",
    "provider_unreachable": "无法连接供应商，请检查服务地址和网络",
    "model_not_found": "上游未找到该模型，请检查上游模型名和模型权限",
    "unknown": "模型健康状态暂时无法确认，请重新执行手动验证",
}


def safe_error_for_status(status: str) -> str:
    """返回可展示的固定文案，不把 Provider 异常或响应原文带到浏览器。"""
    if status == "healthy":
        return ""
    return _SAFE_STATUS_ERRORS.get(status, _SAFE_STATUS_ERRORS["degraded"])


def _record(
    model_name: str,
    provider: str,
    model_kind: str,
    status: str,
    latency_ms: int,
    error: str,
    probe_source: str = "scheduled",
) -> None:
    """写健康缓存（upsert；失败累加 consecutive_failures、成功清零）。

    软失败：探测结果丢一帧可接受，不能反过来影响探测流程。
    """
    failed = bool(error)
    try:
        from backend.config.database import MEMORY_DB_CONFIG
        from backend.infra.db import engine_for

        conn = engine_for(MEMORY_DB_CONFIG).raw_connection()
        try:
            cur = conn.cursor()
            # 原生 psycopg2 游标：只认 %s 占位符（text()/:name 是 SQLAlchemy
            # 方言，raw_connection 下会炸 —— 实机验证发现的坑）。
            cur.execute(
                """
                INSERT INTO llm_model_health
                    (model_name, provider, model_kind, status,
                     last_checked_at, last_latency_ms, last_error,
                     consecutive_failures, probe_source)
                VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s)
                ON CONFLICT (model_name) DO UPDATE SET
                    provider = EXCLUDED.provider,
                    model_kind = EXCLUDED.model_kind,
                    status = EXCLUDED.status,
                    last_checked_at = now(),
                    last_latency_ms = EXCLUDED.last_latency_ms,
                    last_error = EXCLUDED.last_error,
                    consecutive_failures = EXCLUDED.consecutive_failures,
                    probe_source = EXCLUDED.probe_source
                """,
                (model_name, provider, model_kind, status,
                 latency_ms, (error or "")[:500], 0 if not failed else -1,
                 probe_source),
            )
            if failed:
                # 失败：在上一轮计数基础上 +1（插入行则从 1 起）
                cur.execute(
                    "UPDATE llm_model_health SET consecutive_failures = "
                    "GREATEST(consecutive_failures, 0) + 1 "
                    "WHERE model_name = %s",
                    (model_name,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.warning(
            "[ModelHealth] 健康缓存写入失败 model=%s", model_name, exc_info=True)


def probe_model(
    model_name: str,
    model_kind: str | None = None,
    *,
    probe_source: str = "scheduled",
) -> dict[str, Any]:
    """探测单个模型并落缓存；OCR 必须走真实上游手动探测。"""
    from backend.infra.llm import models as models_mod

    entry = models_mod.get_model_entry(model_name)
    kind = model_kind or (models_mod.model_kind_of(entry) if entry else "chat")
    provider = str((entry or {}).get("provider") or "")

    if entry is None:
        _record(model_name, provider, kind, "model_not_found", 0,
                "模型未在注册表登记", probe_source)
        return {"status": "model_not_found", "latencyMs": 0,
                "error": "模型未在注册表登记", "probeSource": probe_source}

    if kind == "ocr":
        message = "OCR 上游调用需由管理员手动验证；凭据已配置不代表模型可调用"
        _record(model_name, provider, kind, "unknown", 0, message, probe_source)
        return {
            "status": "unknown",
            "latencyMs": 0,
            "error": message,
            "probeSource": probe_source,
        }

    t0 = time.monotonic()
    error = ""
    error_type = ""
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
        error_type = type(exc).__name__
    latency_ms = int((time.monotonic() - t0) * 1000)
    status = classify_status(latency_ms, error)
    safe_error = safe_error_for_status(status)
    # 保留既有语义：slow 是成功但变慢，不递增连续失败次数。
    recorded_error = "" if status == "slow" else safe_error
    _record(model_name, provider, kind, status, latency_ms, recorded_error, probe_source)
    logger.info(
        "[ModelHealth] probe model=%s kind=%s status=%s latency=%sms error_type=%s",
        model_name, kind, status, latency_ms, error_type,
    )
    return {
        "status": status,
        "latencyMs": latency_ms,
        "error": safe_error,
        "probeSource": probe_source,
    }


async def probe_ocr_model(model_name: str) -> dict[str, Any]:
    """管理员手动做一次低成本上游 OCR 模型访问验证。

    最小文本请求只验证模型名和凭据可调用，不代表图片识别质量已验证。
    只持久化安全的中文结论，不保存厂商原始报文或凭据。
    """
    import asyncio

    from backend.infra.llm import credentials as credentials_mod
    from backend.infra.llm import models as models_mod
    from backend.services import provider_probe

    entry = models_mod.get_model_entry(model_name)
    provider = str((entry or {}).get("provider") or "")
    if entry is None:
        message = "模型未在注册表登记"
        await asyncio.to_thread(
            _record, model_name, provider, "ocr", "model_not_found", 0,
            message, "manual",
        )
        return {
            "status": "model_not_found", "latencyMs": 0,
            "error": message, "probeSource": "manual",
        }

    kind = models_mod.model_kind_of(entry)
    if kind != "ocr":
        return await asyncio.to_thread(
            probe_model, model_name, kind, probe_source="manual",
        )

    t0 = time.monotonic()
    try:
        provider_entry = models_mod.get_provider_entry(provider) or {}
        credentials = credentials_mod.resolve_credentials(
            provider, model_name=model_name,
        )
        base_url = credentials.base_url or provider_entry.get("base_url") or ""
        driver = str(
            provider_entry.get("driver")
            or models_mod.get_provider_driver(provider)
            or "openai"
        )
        network_scope = str(provider_entry.get("network_scope") or "public")
        extra_headers = credentials.resolved_headers(
            provider_entry.get("extra_headers") or {},
        )

        if not base_url:
            status = "provider_unreachable"
            message = "供应商未配置服务地址，请先完善供应商配置"
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        elif driver != "ollama" and not credentials.api_key:
            status = "auth_failed"
            message = "供应商未配置 API Key"
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        else:
            result = await provider_probe.probe_provider(
                driver=driver,
                base_url=base_url,
                api_key=credentials.api_key or "",
                model_name=model_name,
                upstream_model_name=(
                    str(entry.get("upstream_name") or "").strip() or model_name
                ),
                network_scope=network_scope,
                extra_headers=extra_headers,
                model_kind="ocr",
            )
            elapsed_ms = sum(step.elapsed_ms for step in result.steps)
            if result.ok:
                status = "healthy"
                message = "上游已成功调用该模型；图片识别效果尚未验证"
            else:
                failed_step = next(
                    (step for step in reversed(result.steps)
                     if step.status == provider_probe.STATUS_FAIL),
                    None,
                )
                status = _ocr_probe_failure_status(
                    failed_step.reason if failed_step else None,
                )
                message = (
                    failed_step.summary if failed_step
                    else "上游模型调用失败，请检查供应商配置"
                )
    except credentials_mod.UnknownProviderError:
        status = "degraded"
        message = "模型关联的供应商未登记，请检查供应商配置"
        elapsed_ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        # 不把异常原文（可能含 URL 或上游响应）存进健康表、返回浏览器或日志。
        logger.warning(
            "[ModelHealth] OCR 手动探测失败 model=%s error_type=%s",
            model_name, type(exc).__name__,
        )
        status = "degraded"
        message = "上游模型调用未完成，请检查服务地址、协议和模型权限"
        elapsed_ms = int((time.monotonic() - t0) * 1000)

    await asyncio.to_thread(
        _record,
        model_name,
        provider,
        kind,
        status,
        elapsed_ms,
        "" if status == "healthy" else message,
        "manual",
    )
    return {
        "status": status,
        "latencyMs": elapsed_ms,
        "error": "" if status == "healthy" else message,
        "probeSource": "manual",
    }


def _ocr_probe_failure_status(reason: str | None) -> str:
    """将供应商探测原因映射到健康表既有枚举。"""
    return {
        "api_key": "auth_failed",
        "model_name": "model_not_found",
        "timeout": "timeout",
        "rate_limited": "rate_limited",
        "unreachable": "provider_unreachable",
        "blocked": "provider_unreachable",
        "invalid_url": "provider_unreachable",
        "base_url": "provider_unreachable",
    }.get(reason or "", "degraded")


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
    import asyncio

    from backend.infra.llm import credentials as credentials_mod
    from backend.infra.llm import models as models_mod
    from backend.infra.llm import registry_store

    # 目录是 DB-only 动态层：uvicorn 启动会刷，但 worker/beat 进程不会——
    # 不在此刷新的话，beat 周期任务永远扫 0 个模型（实机验证发现的坑）。
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(registry_store.refresh_registry())  # 同步上下文（worker 任务）
    else:  # pragma: no cover - 极少在已运行 loop 内被调
        asyncio.ensure_future(registry_store.refresh_registry())
    results: dict[str, str] = {}
    for entry in models_mod.get_available_models():
        name = str(entry.get("name") or "")
        if not name:
            continue
        if models_mod.model_kind_of(entry) == "ocr":
            results[name] = "skip(manual-only)"
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
