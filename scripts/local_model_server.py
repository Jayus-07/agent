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
import os
import sys
import threading
import time

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="local-model-server", docs_url=None, redoc_url=None)

_LOCK = threading.Lock()
_ST: dict[str, Any] = {}


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


def _get_reranker():
    """懒加载 bge-reranker-v2-m3（交叉编码器，logit → sigmoid 归一）。"""
    with _LOCK:
        if "reranker" not in _ST:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            path = _reranker_path()
            t0 = time.time()
            _log(f"加载 rerank 模型: {path}")
            tok = AutoTokenizer.from_pretrained(path)
            model = AutoModelForSequenceClassification.from_pretrained(path)
            model.to(_device())
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
    return {"status": "ok", "models_loaded": sorted(k for k in _ST)}


@app.post("/embeddings")
def embeddings(req: EmbeddingsRequest):
    inputs = req.input if isinstance(req.input, list) else [req.input]
    texts = [str(t) for t in inputs]
    if not texts:
        return {"object": "list", "data": [], "model": req.model, "usage": {"prompt_tokens": 0, "total_tokens": 0}}
    model = _get_embedder()
    vectors = model.encode(texts, normalize_embeddings=True).tolist()
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
    import math
    import torch

    query = str((req.input or {}).get("query") or "")
    documents = [str(d) for d in (req.input or {}).get("documents") or []]
    top_n = int((req.parameters or {}).get("top_n") or len(documents))
    if not query or not documents:
        return {"output": {"results": []}}

    tok, model = _get_reranker()
    pairs = [[query, doc] for doc in documents]
    with torch.no_grad():
        batch = tok(pairs, padding=True, truncation=True, max_length=512,
                    return_tensors="pt").to(model.device)
        logits = model(**batch).logits.reshape(-1).float()
    scores = [1.0 / (1.0 + math.exp(-float(x))) for x in logits]

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
