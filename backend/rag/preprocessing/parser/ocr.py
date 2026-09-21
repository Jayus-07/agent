"""PDF OCR 兜底 — 扫描件/无文本层 PDF 的文字识别（供应商抽象）。

路由（RAG_OCR_PROVIDER）:
  - rapidocr（默认）: 离线 RapidOCR(onnxruntime)，免费，中文效果可用；
    无 token 概念，不产生用量记录
  - dashscope（备选）: 云端 qwen-vl 视觉模型逐页提取文字，按 token 计费；
    用量写入 llm_usage_store（component="ocr"），在 /observability/tokens
    看板可见
  - off: 关闭 OCR（上传入口的扫描件预检会直接拒绝无文本层 PDF）

设计约束:
  - 惰性导入 + 引擎单例：RapidOCR 首次加载模型需数秒，只在真正遇到扫描件
    时初始化，不拖慢正常上传
  - 逐页调用：单页失败只丢该页，与 pdf_parser 的 per-page 容错口径一致
  - 本模块不做 PDF 渲染（fitz pixmap 由 pdf_parser 负责），只收 PNG 字节
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
import hashlib
import importlib.metadata
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.config import model_roles
from backend.config import rag as rag_cfg
from backend.infra.llm import credentials as credentials_mod
from backend.infra.llm import models as models_mod
from backend.config.database import RAG_DATA_DIR
from backend.rag.indexing.processing_lineage import ModelIdentity
from backend.shared.logger import logger


def _configured_ocr_model() -> str:
    """读取 OCR 角色；无 DB 覆盖时保持旧版 rag 配置语义。"""
    return model_roles.resolve_runtime_name("ocr", rag_cfg.RAG_OCR_DASHSCOPE_MODEL)


def _resolve_ocr_runtime_config() -> dict[str, str]:
    """解析 OCR 当前出站配置，数据库角色绑定是唯一云端配置来源。"""
    effective = model_roles.resolve_effective("ocr")
    model = _configured_ocr_model()
    if effective.get("source") == model_roles.SOURCE_DB:
        provider_id = ""
        base_url = ""
        if model:
            entry = models_mod.get_model_entry(model)
            provider_id = str(entry.get("provider") or "").strip() if entry else ""
            provider = models_mod.get_provider_entry(provider_id) or {}
            if entry and models_mod.model_kind_of(entry) == "chat":
                try:
                    credentials = credentials_mod.resolve_credentials(
                        provider_id,
                        model_name=model,
                    )
                except credentials_mod.UnknownProviderError:
                    credentials = None
                if credentials is not None:
                    base_url = str(
                        credentials.base_url
                        or provider.get("base_url")
                        or ""
                    ).strip().rstrip("/")
                    if provider_id and base_url:
                        return {
                            "provider": provider_id,
                            "model": model,
                            "api_key": credentials.api_key or "",
                            "base_url": base_url,
                        }

        # 已明确选择 DB 角色时禁止静默回退到旧 env，避免显示与实际调用错位。
        return {
            "provider": provider_id or "db",
            "model": model,
            "api_key": "",
            "base_url": base_url,
        }

    # 都用 DB（2026-09-21 拍板）：云端 OCR 的 Key 只来自数据库供应商凭据，
    # 无 DB 绑定时不再回退旧 env（OCR_DASHSCOPE/DASHSCOPE/EMBEDDING），
    # 由 ocr_available / ocr_image 以明确告警/报错拒绝。
    return {
        "provider": "dashscope",
        "model": model,
        "api_key": "",
        "base_url": "",
    }


def _ocr_cloud_configured() -> bool:
    """RAG_OCR_PROVIDER 选择了云端 dashscope 引擎（Key 一律来自数据库绑定）。"""
    effective = model_roles.resolve_effective("ocr")
    return (
        effective.get("source") == model_roles.SOURCE_DB
        or (rag_cfg.RAG_OCR_PROVIDER or "").lower() == "dashscope"
    )


def get_ocr_model_identity() -> ModelIdentity:
    """返回当前 OCR 引擎的非敏感身份信息。"""

    runtime = _resolve_ocr_runtime_config()
    provider = runtime["provider"]
    effective = model_roles.resolve_effective("ocr")
    if provider != "dashscope" or effective.get("source") == model_roles.SOURCE_DB:
        return ModelIdentity(
            role="ocr",
            engine_type="ocr",
            provider=provider,
            model_name=runtime["model"],
            model_revision=None,
            config_source=str(effective.get("source") or ""),
            config_revision=str(effective.get("updated_at") or "") or None,
            artifact_fingerprint=None,
        )

    provider = (rag_cfg.RAG_OCR_PROVIDER or "").lower()
    if provider == "rapidocr":
        try:
            revision = importlib.metadata.version("rapidocr_onnxruntime")
        except importlib.metadata.PackageNotFoundError:
            revision = None
        return ModelIdentity(
            role="ocr",
            engine_type="ocr",
            provider="rapidocr",
            model_name="RapidOCR",
            model_revision=revision,
            config_source="code-default",
            config_revision=None,
            artifact_fingerprint=(
                f"rapidocr_onnxruntime:{revision}" if revision else None
            ),
        )
    if provider == "dashscope":
        info = model_roles.resolve_effective("ocr")
        return ModelIdentity(
            role="ocr",
            engine_type="ocr",
            provider="dashscope",
            model_name=_configured_ocr_model(),
            model_revision=None,
            config_source=str(info.get("source") or ""),
            config_revision=str(info.get("updated_at") or "") or None,
            artifact_fingerprint=None,
        )
    return ModelIdentity(
        role="ocr",
        engine_type="ocr",
        provider=provider or None,
        model_name=None,
        model_revision=None,
        config_source=None,
        config_revision=None,
        artifact_fingerprint=None,
    )


def ocr_available() -> bool:
    """当前配置的 OCR 供应商是否可用（惰性探测，不初始化引擎）。"""
    effective = model_roles.resolve_effective("ocr")
    if effective.get("source") == model_roles.SOURCE_DB:
        if not _resolve_ocr_runtime_config()["api_key"]:
            logger.warning(
                "[OCR] 数据库角色已绑定云端 OCR，但当前供应商没有可用 API Key"
            )
            return False
        return True

    provider = (rag_cfg.RAG_OCR_PROVIDER or "").lower()
    if provider == "off" or not provider:
        return False
    if provider == "rapidocr":
        try:
            import rapidocr_onnxruntime  # noqa: F401
            return True
        except ImportError:
            logger.warning(
                "[OCR] RAG_OCR_PROVIDER=rapidocr 但未安装 rapidocr_onnxruntime，"
                "OCR 兜底不可用（pip install rapidocr_onnxruntime）"
            )
            return False
    if _ocr_cloud_configured():
        if not _resolve_ocr_runtime_config()["api_key"]:
            logger.warning(
                "[OCR] RAG_OCR_PROVIDER=dashscope 但数据库未绑定 OCR 角色（或供应商"
                "缺少可用 API Key）—— 云端 Key 只来自数据库，请先在管理端绑定"
            )
            return False
        return True
    logger.warning(f"[OCR] 未知供应商 {provider!r}，OCR 兜底不可用")
    return False


# ── RapidOCR 引擎单例 ──

_rapidocr_engine: Any = None
_rapidocr_lock = threading.Lock()


def _get_rapidocr_engine():
    global _rapidocr_engine
    if _rapidocr_engine is None:
        with _rapidocr_lock:
            if _rapidocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _rapidocr_engine = RapidOCR()
                logger.info("[OCR] RapidOCR 引擎初始化完成（首次调用含模型加载）")
    return _rapidocr_engine


def _ocr_image_rapidocr(png_bytes: bytes) -> str:
    """RapidOCR 识别单页 PNG → 按行拼接文本。"""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("PNG 解码失败")
    result, _elapse = _get_rapidocr_engine()(img)
    if not result:
        return ""
    # result: [[box, text, score], ...]，按识别顺序（大致阅读序）拼接；
    # 低置信行丢弃（OCR 脏数据由 DocumentCleaner 的 OCR 清洗分支兜底）
    lines = [str(item[1]).strip() for item in result
             if len(item) >= 2 and float(item[2]) >= 0.5 and str(item[1]).strip()]
    return "\n".join(lines)


def _record_ocr_usage(model: str, prompt_tokens: int, completion_tokens: int,
                      duration_ms: int, status: str = "success",
                      provider: str = "dashscope") -> None:
    """DashScope OCR 用量写入 llm_usage_store（软失败不影响 OCR 主流程）。"""
    try:
        from backend.observability.llm_usage_store import get_llm_usage_store
        from backend.observability.llm_usage_store import current_usage_attribution
        attribution = current_usage_attribution()
        get_llm_usage_store().record({
            "component": "ocr",
            "model": model,
            "provider": provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": 0.0,
            "duration_ms": duration_ms,
            "trace_id": attribution["trace_id"],
            "session_id": attribution["session_id"],
            "request_id": attribution["request_id"],
            "user_id": attribution["user_id"],
            "tenant_id": attribution["tenant_id"],
            "run_id": attribution["run_id"],
            "step_id": attribution["step_id"],
            "role": attribution["role"] or "ocr",
            "stage": attribution["stage"] or "ocr",
            "finish_reason": status,
        })
    except Exception as e:
        logger.debug(f"[OCR] 用量记录失败（不影响主流程）: {e}")


def _ocr_image_dashscope(png_bytes: bytes) -> str:
    """兼容 OpenAI Chat 的视觉 OCR；用量计入 token 可观测（component=ocr）。"""
    from openai import OpenAI
    runtime = _resolve_ocr_runtime_config()
    model = runtime["model"]
    client = OpenAI(
        api_key=runtime["api_key"],
        base_url=runtime["base_url"],
        timeout=rag_cfg.RAG_OCR_DASHSCOPE_TIMEOUT,
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text",
                     "text": "提取图片中的全部文字，按原有阅读顺序输出，"
                             "不要翻译、不要总结、不要添加任何解释。"},
                ],
            }],
        )
    except Exception as e:
        _record_ocr_usage(
            model, 0, 0, int((time.monotonic() - t0) * 1000),
            status="error", provider=runtime["provider"],
        )
        raise
    usage = getattr(resp, "usage", None)
    actual_model = str(getattr(resp, "model", "") or "").strip()
    if actual_model:
        _ocr_actual_model.set(actual_model)
    _record_ocr_usage(
        model,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        int((time.monotonic() - t0) * 1000),
        provider=runtime["provider"],
    )
    return resp.choices[0].message.content or ""


def ocr_image(png_bytes: bytes) -> str:
    """按当前配置的供应商识别单页 PNG 文本。供应商不可用时抛异常由调用方容错。

    云端供应商（dashscope）外层套 按页缓存 + 限流（D6 ③：在线 API 有成本，
    幂等重跑不重复调用）；离线 rapidocr 免费，直连不缓存。
    """
    effective = model_roles.resolve_effective("ocr")
    provider = (rag_cfg.RAG_OCR_PROVIDER or "").lower()
    try:
        if effective.get("source") == model_roles.SOURCE_DB:
            if not _resolve_ocr_runtime_config()["api_key"]:
                raise RuntimeError("OCR 数据库供应商未配置 API Key")
            return _ocr_image_cloud_cached(png_bytes)
        if provider == "rapidocr":
            result = _ocr_image_rapidocr(png_bytes)
            _record_ocr_result(status="success", cache_status="miss")
            return result
        if _ocr_cloud_configured():
            if not _resolve_ocr_runtime_config()["api_key"]:
                raise RuntimeError(
                    "云端 OCR 未在数据库绑定 OCR 角色（或供应商缺少 API Key），"
                    "不再回退旧环境变量"
                )
            return _ocr_image_cloud_cached(png_bytes)
        raise RuntimeError(f"OCR 供应商不可用: {provider!r}")
    except Exception:
        _record_ocr_result(status="failed", cache_status="miss")
        raise


# ── 云端 OCR：按页缓存 + 限流（D6 ③）──

_ocr_cache_dir = Path(RAG_DATA_DIR) / "ocr_cache"
_cloud_lock = threading.Lock()
_last_call_mono: float = 0.0
_ocr_result_meta: ContextVar[dict] = ContextVar("ocr_result_meta", default={})
_ocr_actual_model: ContextVar[str | None] = ContextVar(
    "ocr_actual_model", default=None
)


def reset_ocr_tracking() -> None:
    """开始一份文档的 OCR 统计，避免复用 worker 线程时串入上一份文档。"""
    _ocr_result_meta.set({
        "calls": 0,
        "successes": 0,
        "failures": 0,
        "cache_hits": 0,
        "status": "",
        "cache_status": "miss",
        "model_name": None,
        "model_revision": None,
        "provider": None,
    })
    _ocr_actual_model.set(None)


def get_ocr_result_meta() -> dict:
    """返回当前文档 OCR 调用/缓存摘要，不包含图片或识别原文。"""
    return dict(_ocr_result_meta.get() or {})


def _record_ocr_result(
    *, status: str, cache_status: str, model_name: str | None = None
) -> None:
    current = get_ocr_result_meta()
    current["calls"] = int(current.get("calls") or 0) + 1
    if cache_status == "hit":
        current["cache_hits"] = int(current.get("cache_hits") or 0) + 1
    if status in {"success", "cached"}:
        current["successes"] = int(current.get("successes") or 0) + 1
    if status == "failed":
        current["failures"] = int(current.get("failures") or 0) + 1
    current["status"] = status
    current["cache_status"] = cache_status
    identity = get_ocr_model_identity()
    current["model_name"] = (
        model_name
        or _ocr_actual_model.get()
        or identity.model_name
    )
    current["model_revision"] = identity.model_revision
    current["provider"] = identity.provider
    _ocr_result_meta.set(current)


def _cache_path(png_bytes: bytes) -> Path:
    """缓存键包含供应商、模型、配置和指令版本，避免跨版本脏命中。"""
    role_info = model_roles.resolve_effective("ocr")
    runtime = _resolve_ocr_runtime_config()
    h = hashlib.sha256(
        (
            f"{runtime['provider']}|{runtime['model']}|{runtime['base_url']}|"
            f"{role_info.get('source', '')}|{role_info.get('updated_at', '')}|"
            f"{getattr(rag_cfg, 'OCR_PROMPT_VERSION', 'v1')}|"
        ).encode()
        + png_bytes
    ).hexdigest()
    return _ocr_cache_dir / f"{h}.json"


def _throttle_cloud() -> None:
    """相邻两次云端调用强制最小间隔（批量入库限流，防触发平台 429）。"""
    global _last_call_mono
    interval_ms = max(int(rag_cfg.RAG_OCR_MIN_INTERVAL_MS), 0)
    if interval_ms <= 0:
        return
    with _cloud_lock:
        wait = interval_ms / 1000.0 - (time.monotonic() - _last_call_mono)
        if wait > 0:
            time.sleep(wait)
        _last_call_mono = time.monotonic()


def _ocr_image_cloud_cached(png_bytes: bytes) -> str:
    """云端 OCR 带缓存入口：命中直接返回；未命中限流后调用并落盘（软失败）。"""
    if not rag_cfg.RAG_OCR_CACHE_ENABLED:
        _throttle_cloud()
        result = _ocr_image_dashscope(png_bytes)
        _record_ocr_result(status="success", cache_status="miss")
        return result
    path = _cache_path(png_bytes)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("text"), str):
                _record_ocr_result(status="cached", cache_status="hit")
                return data["text"]
    except Exception as e:
        logger.debug(f"[OCR] 缓存读取失败（走直连）: {e}")
    text = _throttled_call(png_bytes)
    _record_ocr_result(status="success", cache_status="miss")
    try:
        _ocr_cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"text": text, "model": _configured_ocr_model(),
             "ts": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 原子落盘，多 worker 同页竞争无半写文件
    except Exception as e:
        logger.debug(f"[OCR] 缓存写入失败（不影响主流程）: {e}")
    return text


def _throttled_call(png_bytes: bytes) -> str:
    """限流包裹的云端调用（独立函数便于测试桩替换）。"""
    _throttle_cloud()
    return _ocr_image_dashscope(png_bytes)
