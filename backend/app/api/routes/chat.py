"""对话路由 — Multi-Agent 工作流（Planner → Supervisor → Workers → Reporter）

SSE 流式协议 (v2):
  event: meta   → 握手（node_labels 映射表）
  event: status → 宏观阶段切换（纯 node 字段，前端自行映射）
  event: log    → 详细时间线（含 payload 入参/出参）
  event: delta  → 流式内容块（句子级切分，打字机数据源）
  event: done   → 结束信号（elapsed + sources）
  event: error  → 错误/中止
"""
import asyncio
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.app.api.schemas import ChatRequest, ChatResponse, AbortRequest, ErrorResponse
from backend.app.api.deps import get_multi_agent

router = APIRouter(prefix="/chat", tags=["对话"])

# ── 全局线程池 + 活跃中止标志 ────────────────────
_executor = ThreadPoolExecutor(max_workers=2)
_active_stops: dict[str, threading.Event] = {}


def _request_key(session_id: str, request_id: str) -> str:
    return f"{session_id}:{request_id}"


# ── node_name → emoji 映射表（通过 meta 事件传给前端） ──
_NODE_LABELS = {
    "planner":       "📋 任务规划",
    "supervisor":    "🧠 调度决策",
    "sql_worker":    "📊 数据查询",
    "rag_worker":    "📚 知识检索",
    "report_worker": "📄 报告生成",
    "reporter":      "✍️ 生成回复",
}


def _sse_encode(event: dict) -> str:
    """将事件字典编码为 SSE 文本帧: event: <type>\ndata: <json>\n\n"""
    evt_type = event["event"]
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {evt_type}\ndata: {payload}\n\n"


# ═══════════════════════════════════════════════════
# POST /chat — 同步对话（非流式，兼容旧版）
# ═══════════════════════════════════════════════════
@router.post("", response_model=ChatResponse, responses={500: {"model": ErrorResponse}})
async def chat(req: ChatRequest):
    """提交自然语言问题，Multi-Agent 自动拆解+执行+汇总"""
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    answer = await asyncio.to_thread(agent.ask, req.question, req.session_id, kb_id=kb_id)
    return ChatResponse(
        answer=answer,
        session_id=req.session_id,
        sources=getattr(agent, "_last_sources", []),
    )


# ═══════════════════════════════════════════════════
# POST /chat/stream — SSE 流式对话 (v2)
# ═══════════════════════════════════════════════════
@router.post("/stream", responses={500: {"model": ErrorResponse}})
async def chat_stream(req: ChatRequest):
    """
    SSE 流式对话 v2：4 种事件分流推送。

    事件类型:
      event: meta   — 握手，携带 node_labels 映射表
      event: status — 顶部状态标签
      event: log    — 底部思维链日志（含 payload）
      event: delta  — 中间流式内容（打字机数据源）
      event: done   — 结束信号
      event: error  — 错误/中止
    """
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    request_id = req.request_id or "default"
    key = _request_key(req.session_id, request_id)

    # —— 创建中止标志 ——
    stop_event = threading.Event()
    _active_stops[key] = stop_event

    # —— 线程安全队列（容量 100，防止内存暴涨） ——
    q: queue.Queue = queue.Queue(maxsize=100)

    def producer():
        """在 executor 线程中运行 LangGraph，事件逐个入队"""
        try:
            for evt in agent.stream_events(
                req.question,
                req.session_id,
                kb_id=kb_id,
                stop_event=stop_event,
            ):
                if stop_event.is_set():
                    break
                try:
                    q.put(evt, timeout=0.05)
                except queue.Full:
                    if stop_event.is_set():
                        break
        except Exception as exc:
            try:
                q.put(
                    {"event": "error", "data": {"message": str(exc), "ts": time.time()}},
                    timeout=0.05,
                )
            except queue.Full:
                pass
        finally:
            q.put(None)  # sentinel：通知消费者结束

    async def event_generator():
        """异步生成器：从队列取事件 → SSE 格式化 → yield"""
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_executor, producer)

        # —— 握手事件：发送 node_labels 映射表 ——
        yield _sse_encode({
            "event": "meta",
            "data": {"node_labels": _NODE_LABELS},
        })

        try:
            while True:
                # 非阻塞取事件，避免额外线程开销
                try:
                    evt = q.get_nowait()
                except queue.Empty:
                    if future.done():
                        break
                    await asyncio.sleep(0.01)  # 队列空 → 让出 CPU，等待生产
                    continue

                if evt is None:  # sentinel
                    break

                yield _sse_encode(evt)
                await asyncio.sleep(0)  # 让出事件循环，确保客户端及时收到

        except GeneratorExit:
            # 前端主动断开连接 → 触发中断
            stop_event.set()
        finally:
            # 内存安全：强制清理中止标志
            _active_stops.pop(key, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


# ═══════════════════════════════════════════════════
# POST /chat/abort — 前端主动中止
# ═══════════════════════════════════════════════════
@router.post("/abort")
async def chat_abort(req: AbortRequest):
    """前端点击停止按钮后调用，触发 stop_event 中断后端执行"""
    key = _request_key(req.session_id, req.request_id)
    evt = _active_stops.get(key)
    if evt:
        evt.set()
        return {"status": "aborted", "key": key}
    return {"status": "not_found", "key": key}
