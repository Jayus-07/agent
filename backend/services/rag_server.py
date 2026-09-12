"""backend/services/rag_server.py — RAG 独立服务（端口 8090）

RAG 服务化（阶段 1）：embedding 模型 / Chroma / BM25 / 索引同步全部收进本进程，
app 等消费方通过 RAG_MODE=remote + backend/rag/client.py 代理调用。
本服务自身【强制本地模式】：直接调 _get_local_pipeline()，绕过 RAG_MODE 路由，
防止误配 RAG_MODE=remote 时服务端自我代理成环。

端点:
  POST /ask        完整问答（检索→rerank→LLM→evidence gate，30-120s）
  POST /retrieve   轻量检索（只检索不生成，~3-5s）
  GET  /healthz    liveness：进程活着即 200
  GET  /readyz     readiness：索引同步完成才 200（start_period 需容忍数十分钟）
  POST /admin/warmup  幂等触发后台初始化（正常由启动钩子自动做，兜底用）

启动:
    python -m uvicorn backend.services.rag_server:app --host 0.0.0.0 --port 8090
"""
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.shared.logger import logger

app = FastAPI(
    title="RAG Service",
    version="1.0.0",
    description="知识库检索/问答独立服务（重资源：embedding + Chroma + LLM）",
)


# ==================== 请求/响应模型 ====================

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, description="用户问题")
    session_id: str = Field("default", description="会话 ID（多轮缓存判定用）")
    kb_id: str = Field("default", description="知识库 ID")
    kb_ids: list[str] | None = Field(None, description="多知识库指定（优先级高于 kb_id）")


class RetrieveRequest(BaseModel):
    question: str = Field(..., min_length=1, description="检索问题")
    kb_id: str = Field("default", description="知识库 ID")
    top_k: int = Field(3, ge=1, le=20, description="返回 chunk 数")


# ==================== 本地 pipeline 接入 ====================

def _get_pipeline():
    """强制本地模式获取 pipeline（服务端永不走 remote 路由）。

    在线程池中调用（FastAPI def 端点已在线程池）；首次调用含全量
    索引同步，可能阻塞数十分钟 —— readyz 在此期间返回 503。
    """
    from backend.rag.pipeline import _get_local_pipeline
    return _get_local_pipeline()


def _kick_init() -> None:
    """后台线程触发初始化（幂等：已有初始化/已就绪则不重复）。"""
    from backend.rag import pipeline as _p
    from backend.config.rag import RAG_MODE
    if RAG_MODE == "remote":
        logger.error("[rag-server] RAG_MODE=remote 配置错误：rag-service 必须以本地模式运行")
    if _p._pipeline_singleton is not None or _p._pipeline_initializing:
        return

    def _bg():
        try:
            _get_pipeline()
        except Exception as e:
            logger.warning(f"[rag-server] 后台 pipeline 初始化失败: {e}")

    threading.Thread(target=_bg, name="rag-server-init", daemon=True).start()


@app.on_event("startup")
def _startup() -> None:
    """启动即后台预热索引，不阻塞端口监听（readyz 期间返回 503）。"""
    _kick_init()


# ==================== 端点 ====================

@app.post("/ask")
def ask(req: AskRequest) -> dict[str, Any]:
    """完整问答。返回 {answer, meta}，meta 对齐 RAGPipeline.last_answer_meta。"""
    try:
        pipeline = _get_pipeline()
    except RuntimeError as e:
        # 初始化失败（重试中）：503 让调用方走兜底/重试
        raise HTTPException(status_code=503, detail=str(e)) from e
    answer = pipeline.ask(
        question=req.question,
        session_id=req.session_id,
        kb_id=req.kb_id,
        kb_ids=req.kb_ids,
    )
    return {"answer": answer, "meta": getattr(pipeline, "last_answer_meta", {}) or {}}


@app.post("/retrieve")
def retrieve(req: RetrieveRequest) -> dict[str, Any]:
    """轻量检索（不生成），供 BusinessAnalyzer 等下游分析用。"""
    try:
        pipeline = _get_pipeline()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    result = pipeline.retrieve_knowledge(
        question=req.question, kb_id=req.kb_id, top_k=req.top_k,
    )
    return {"result": result}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """liveness：进程活着即 200（初始化中也算活）。"""
    return {"status": "ok", "service": "rag-service"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    """readiness：索引同步完成才 200；初始化中/失败 503。

    非阻塞（_get_local_pipeline_state 只读标志），可在事件循环线程调用。
    """
    from backend.rag.pipeline import _get_local_pipeline_state
    state = _get_local_pipeline_state()
    if state["state"] == "ready":
        return {"ready": True, "state": "ready"}
    if state["state"] == "not_started":
        _kick_init()
    raise HTTPException(
        status_code=503,
        detail={
            "code": "SERVICE_NOT_READY",
            "state": state["state"],
            "message": state.get("message") or state.get("error", "初始化中"),
            "retry_after": 30,
        },
    )


@app.post("/admin/warmup")
def admin_warmup() -> dict[str, Any]:
    """幂等触发后台初始化（readyz 已自动做，此处供手动兜底/运维触发）。"""
    from backend.rag.pipeline import _get_local_pipeline_state
    state = _get_local_pipeline_state()
    _kick_init()
    return {"state": state, "message": "初始化已触发（若未在进行中）"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
