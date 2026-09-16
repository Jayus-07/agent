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
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.config import rag as rag_cfg
from backend.config.database import RAG_DATA_DIR
from backend.shared.logger import logger


def _resolve_dashscope_key() -> str:
    """DashScope API Key 解析：OCR 专用 → 通用 DashScope → Embedding key 兜底
    （三者通常是同一个阿里云账号）。"""
    import os
    return (
        os.getenv("OCR_DASHSCOPE_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
        or ""
    )


def ocr_available() -> bool:
    """当前配置的 OCR 供应商是否可用（惰性探测，不初始化引擎）。"""
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
    if provider == "dashscope":
        if not _resolve_dashscope_key():
            logger.warning(
                "[OCR] RAG_OCR_PROVIDER=dashscope 但未配置 "
                "OCR_DASHSCOPE_API_KEY/DASHSCOPE_API_KEY/EMBEDDING_API_KEY"
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
                      duration_ms: int, status: str = "success") -> None:
    """DashScope OCR 用量写入 llm_usage_store（软失败不影响 OCR 主流程）。"""
    try:
        from backend.observability.llm_usage_store import get_llm_usage_store
        from backend.observability.tracer import current_trace_context
        trace_id, session_id = current_trace_context()
        get_llm_usage_store().record({
            "component": "ocr",
            "model": model,
            "provider": "dashscope",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": 0.0,
            "duration_ms": duration_ms,
            "trace_id": trace_id or "",
            "session_id": session_id or "",
            "finish_reason": status,
        })
    except Exception as e:
        logger.debug(f"[OCR] 用量记录失败（不影响主流程）: {e}")


def _ocr_image_dashscope(png_bytes: bytes) -> str:
    """DashScope qwen-vl 识别单页 PNG；用量计入 token 可观测（component=ocr）。"""
    from openai import OpenAI
    model = rag_cfg.RAG_OCR_DASHSCOPE_MODEL
    client = OpenAI(
        api_key=_resolve_dashscope_key(),
        base_url=rag_cfg.RAG_OCR_DASHSCOPE_BASE_URL,
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
        _record_ocr_usage(model, 0, 0,
                          int((time.monotonic() - t0) * 1000), status="error")
        raise
    usage = getattr(resp, "usage", None)
    _record_ocr_usage(
        model,
        getattr(usage, "prompt_tokens", 0) or 0,
        getattr(usage, "completion_tokens", 0) or 0,
        int((time.monotonic() - t0) * 1000),
    )
    return resp.choices[0].message.content or ""


def ocr_image(png_bytes: bytes) -> str:
    """按当前配置的供应商识别单页 PNG 文本。供应商不可用时抛异常由调用方容错。

    云端供应商（dashscope）外层套 按页缓存 + 限流（D6 ③：在线 API 有成本，
    幂等重跑不重复调用）；离线 rapidocr 免费，直连不缓存。
    """
    provider = (rag_cfg.RAG_OCR_PROVIDER or "").lower()
    if provider == "rapidocr":
        return _ocr_image_rapidocr(png_bytes)
    if provider == "dashscope":
        return _ocr_image_cloud_cached(png_bytes)
    raise RuntimeError(f"OCR 供应商不可用: {provider!r}")


# ── 云端 OCR：按页缓存 + 限流（D6 ③）──

_ocr_cache_dir = Path(RAG_DATA_DIR) / "ocr_cache"
_cloud_lock = threading.Lock()
_last_call_mono: float = 0.0


def _cache_path(png_bytes: bytes) -> Path:
    """缓存键 = sha256(provider|model|页面图像字节)。图像不变即命中。"""
    h = hashlib.sha256(
        f"{(rag_cfg.RAG_OCR_PROVIDER or '').lower()}|{rag_cfg.RAG_OCR_DASHSCOPE_MODEL}|".encode()
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
        return _ocr_image_dashscope(png_bytes)
    path = _cache_path(png_bytes)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("text"), str):
                return data["text"]
    except Exception as e:
        logger.debug(f"[OCR] 缓存读取失败（走直连）: {e}")
    text = _throttled_call(png_bytes)
    try:
        _ocr_cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"text": text, "model": rag_cfg.RAG_OCR_DASHSCOPE_MODEL,
             "ts": int(time.time())}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 原子落盘，多 worker 同页竞争无半写文件
    except Exception as e:
        logger.debug(f"[OCR] 缓存写入失败（不影响主流程）: {e}")
    return text


def _throttled_call(png_bytes: bytes) -> str:
    """限流包裹的云端调用（独立函数便于测试桩替换）。"""
    _throttle_cloud()
    return _ocr_image_dashscope(png_bytes)
