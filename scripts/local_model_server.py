# -*- coding: utf-8 -*-
"""local_model_server.py — 本地专项模型 HTTP 服务（embedding + rerank）

供管理端专项绑定指向的本机推理服务（默认 127.0.0.1:8088，容器内经
host.docker.internal:8088 访问），与 dev-local-models.bat start/stop 配套：

  POST /embeddings                                 OpenAI 兼容（bge-m3）
  POST /services/rerank/text-rerank/text-rerank    DashScope 兼容（bge-reranker-v2-m3）
  GET  /healthz                                    存活探测

模型路径可用环境变量覆盖：BGE_M3_MODEL_PATH / RERANKER_MODEL_PATH；
默认从本机 HuggingFace / ModelScope 缓存解析。
"""
from __future__ import annotations

import glob
from contextlib import asynccontextmanager, contextmanager
import os
import sys
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """完成真实推理后才接收请求；启动失败交由服务管理脚本报告。"""
    embeddings(EmbeddingsRequest(input=["模型启动预热"]))
    rerank(RerankRequest(input={
        "query": "模型启动预热", "documents": ["模型启动预热"],
    }))
    _log("双模型推理预热完成")
    yield


app = FastAPI(
    title="local-model-server", docs_url=None, redoc_url=None, lifespan=_lifespan,
)

_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_INFERENCE_CAPACITY = threading.BoundedSemaphore(2)
_OOM_UNTIL = 0.0
_ST: dict[str, Any] = {}


@contextmanager
def _inference_slot():
    """两个模型共用单推理槽，只额外允许一个等待者，避免排队撑满内存。"""
    global _OOM_UNTIL
    if time.monotonic() < _OOM_UNTIL:
        raise HTTPException(status_code=503, detail="model_memory_cooldown",
                            headers={"Retry-After": "3"})
    if not _INFERENCE_CAPACITY.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="model_busy",
                            headers={"Retry-After": "1"})
    acquired = False
    try:
        acquired = _INFERENCE_LOCK.acquire(timeout=5)
        if not acquired:
            raise HTTPException(status_code=503, detail="model_busy",
                                headers={"Retry-After": "1"})
        if time.monotonic() < _OOM_UNTIL:
            raise HTTPException(status_code=503, detail="model_memory_cooldown",
                                headers={"Retry-After": "3"})
        try:
            yield
        except Exception as exc:
            import torch

            if not isinstance(exc, (MemoryError, torch.cuda.OutOfMemoryError)):
                raise
            _OOM_UNTIL = time.monotonic() + 3
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            _log("推理内存不足，已停止本次请求并进入短暂冷却")
            raise HTTPException(status_code=503, detail="model_memory_exhausted",
                                headers={"Retry-After": "3"}) from exc
    finally:
        if acquired:
            _INFERENCE_LOCK.release()
        _INFERENCE_CAPACITY.release()


def _validate_texts(texts: list[str], *, max_items: int = 64) -> None:
    """在分配模型张量前限制一次请求的输入规模，不改变既有切词语义。"""
    if len(texts) > max_items or any(len(text) > 32768 for text in texts):
        raise HTTPException(status_code=413, detail="model_input_too_large")


def _log(msg: str) -> None:
    print(f"[local-model-server] {msg}", flush=True)


def _resolve_hf_snapshot(pattern: str) -> str:
    """HF cache 布局 models--BAAI--bge-m3/snapshots/<hash>/ → 取含 config.json 的快照。"""
    candidates = sorted(
        d for d in glob.glob(pattern)
        if os.path.isfile(os.path.join(d, "config.json"))
    )
    if not candidates:
        raise RuntimeError(f"未在缓存中找到模型快照: {pattern}")
    return candidates[-1]


def _m3_path() -> str:
    explicit = os.getenv("BGE_M3_MODEL_PATH", "").strip()
    if explicit:
        return explicit
    return _resolve_hf_snapshot(
        r"C:\Users\wh\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\*")


def _reranker_path() -> str:
    explicit = os.getenv("RERANKER_MODEL_PATH", "").strip()
    if explicit:
        return explicit
    return r"C:\Users\wh\.cache\modelscope\hub\models\BAAI\bge-reranker-v2-m3"


def _device() -> str:
    """GPU 优先（CUDA torch）；无 GPU 回退 CPU。"""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_embedder():
    """懒加载 bge-m3（SentenceTransformer，dense 1024 维）。"""
    with _LOCK:
        if "embedder" not in _ST:
            from sentence_transformers import SentenceTransformer

            path = _m3_path()
            device = _device()
            t0 = time.time()
            _log(f"加载 embedding 模型: {path} (device={device})")
            _ST["embedder"] = SentenceTransformer(path, device=device)
            _log(f"embedding 模型就绪 ({time.time() - t0:.1f}s)")
        return _ST["embedder"]


def _reranker_device() -> str:
    """参考模型服务优先使用可用GPU；可显式指定CPU，加载失败不会伪装就绪。"""
    configured = os.getenv("RERANKER_DEVICE", "auto").strip().lower()
    return _device() if configured == "auto" else configured


def _get_reranker():
    """懒加载 bge-reranker-v2-m3（交叉编码器，logit → sigmoid 归一）。"""
    with _LOCK:
        if "reranker" not in _ST:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            path = _reranker_path()
            device = _reranker_device()
            if device == "cpu":
                torch.set_num_threads(int(os.getenv("RERANKER_CPU_THREADS", "4")))
            t0 = time.time()
            _log(f"加载 rerank 模型: {path} (device={device})")
            tok = AutoTokenizer.from_pretrained(path)
            # 只改变重排计算精度；embedding 模型和归一化策略保持不变。
            dtype = torch.float16 if device.startswith("cuda") else torch.float32
            model = AutoModelForSequenceClassification.from_pretrained(
                path, torch_dtype=dtype,
            )
            model.to(device)
            model.eval()
            _ST["reranker_tok"] = tok
            _ST["reranker"] = model
            _log(f"rerank 模型就绪 ({time.time() - t0:.1f}s)")
        return _ST["reranker_tok"], _ST["reranker"]


class EmbeddingsRequest(BaseModel):
    model: str = ""
    input: Any
    encoding_format: str = "float"


class RerankRequest(BaseModel):
    model: str = ""
    input: dict
    parameters: dict = {}


@app.get("/healthz")
def healthz():
    reranker = _ST.get("reranker")
    return {"status": "ok", "models_loaded": sorted(k for k in _ST),
            "reranker_device": str(reranker.device) if reranker is not None else None,
            "reranker_dtype": str(reranker.dtype) if reranker is not None else None,
            "inference_concurrency": 1, "max_inflight": 2,
            "embedding_batch_size": 1,
            "memory_cooldown": time.monotonic() < _OOM_UNTIL}


@app.post("/embeddings")
def embeddings(req: EmbeddingsRequest):
    inputs = req.input if isinstance(req.input, list) else [req.input]
    texts = [str(t) for t in inputs]
    _validate_texts(texts)
    if not texts:
        return {"object": "list", "data": [], "model": req.model, "usage": {"prompt_tokens": 0, "total_tokens": 0}}
    model = _get_embedder()
    # 两个模型共享有限显存，串行推理避免并发激活张量叠加。
    with _inference_slot():
        vectors = model.encode(texts, batch_size=1, normalize_embeddings=True).tolist()
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": vec}
            for i, vec in enumerate(vectors)
        ],
        "model": req.model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.post("/services/rerank/text-rerank/text-rerank")
def rerank(req: RerankRequest):
    import torch

    query = str((req.input or {}).get("query") or "")
    documents = [str(d) for d in (req.input or {}).get("documents") or []]
    _validate_texts(documents)
    if len(query) > 4096:
        raise HTTPException(status_code=413, detail="model_input_too_large")
    top_n = int((req.parameters or {}).get("top_n") or len(documents))
    if not query or not documents:
        return {"output": {"results": []}}

    tok, model = _get_reranker()
    batch_size = int(os.getenv("RERANKER_BATCH_SIZE", "2"))
    if not 1 <= batch_size <= 2:
        raise ValueError("RERANKER_BATCH_SIZE 必须为1或2，避免显存峰值失控")
    scores = []
    with _inference_slot(), torch.inference_mode():
        for offset in range(0, len(documents), batch_size):
            pairs = [[query, doc] for doc in documents[offset:offset + batch_size]]
            batch = tok(pairs, padding=True, truncation=True, max_length=512,
                        return_tensors="pt").to(model.device)
            logits = model(**batch).logits.reshape(-1).float()
            if not torch.isfinite(logits).all():
                raise ValueError("重排模型返回非有限分数")
            scores.extend(torch.sigmoid(logits).cpu().tolist())
            del batch, logits

    ranked = sorted(
        ({"index": i, "relevance_score": round(s, 6)}
         for i, s in enumerate(scores)),
        key=lambda r: r["relevance_score"], reverse=True,
    )[: max(1, top_n)]
    if (req.parameters or {}).get("return_documents"):
        for r in ranked:
            r["document"] = {"text": documents[r["index"]]}
    return {"output": {"results": ranked}}


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("LOCAL_MODEL_HOST", "127.0.0.1")
    port = int(os.getenv("LOCAL_MODEL_PORT", "8088"))
    _log(f"listening on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
