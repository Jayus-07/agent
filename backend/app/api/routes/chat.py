"""对话路由 — Multi-Agent 工作流（Planner → Supervisor → Workers → Reporter）

SSE 流式协议 (v2):
  event: meta   → 握手（node_labels 映射表）
  event: status → 宏观阶段切换（纯 node 字段，前端自行映射）
  event: log    → 详细时间线（含 payload 入参/出参）
  event: delta  → 流式内容块（句子级切分，打字机数据源）
  event: thinking → 思考链增量（推理模型 reasoning_content，"已思考"折叠面板数据源）
  event: done   → 结束信号（elapsed + sources）
  event: error  → 错误/中止
"""
import asyncio
import json
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.shared.logger import logger
from backend.config.settings import (
    CHAT_SSE_MAX_WORKERS,
    CHAT_SSE_QUEUE_MAXSIZE,
    CHAT_SSE_GET_TIMEOUT,
)

from backend.app.api.schemas import ChatRequest, ChatResponse, AbortRequest, ErrorResponse
from backend.app.api.deps import get_multi_agent
from backend.infra.llm.rate_limiter import require_rate_limit
from backend.observability.metrics import (
    StreamLatencyTracker,
    chat_request_total,
    chat_request_duration_seconds,
    chat_stream_event_dropped_total,
    chat_stream_event_produced_total,
)

router = APIRouter(prefix="/chat", tags=["对话"])

# ── 全局线程池（按 CPU 自适应，预留 1 核给 event loop） ──
# SSE 流的 worker 数 = SSE 并发上限；同时第 N+1 路会排队等待有空闲 worker。
# 阈值可通过 CHAT_SSE_MAX_WORKERS 覆盖（P1-14：配置收敛到 config/settings.py）。
_SSE_MAX_WORKERS = CHAT_SSE_MAX_WORKERS
_executor = ThreadPoolExecutor(
    max_workers=_SSE_MAX_WORKERS,
    thread_name_prefix="chat-sse",
)
# 用户停止信号字典（aborted 路径在 chat_abort 中按 key 触发）
_active_stops: dict[str, threading.Event] = {}
# 容量 1024 → 在 100Hz 输出下可撑 ~10s；超出时 backpressure（增量记 metric + set stop）
_SSE_QUEUE_MAXSIZE = CHAT_SSE_QUEUE_MAXSIZE
# consumer 阻塞拉取超时（秒）→ CPU 占用从 100Hz 轮询降到 ~0.5Hz
_SSE_GET_TIMEOUT = CHAT_SSE_GET_TIMEOUT
# SSE 心跳间隔（秒）：空闲超过此值发 ping 保活（实测链路空闲 ~97s 断流，15s 余量充足）
_SSE_PING_INTERVAL = 15.0


def _request_key(session_id: str, request_id: str) -> str:
    return f"{session_id}:{request_id}"


# ── node_name → 用户可读标签映射表（通过 meta 事件传给前端）──
# 2026-09-15 收敛：单一事实源在 builder._NODE_LABELS（build_graph 时动态
# 补入域图标签）。旧实现自维护一份含过期节点名（sql_worker/rag_worker，
# 实际节点为 sql_skill/rag_skill）的映射，标签与真实节点漂移。
from backend.orchestration.graph.builder import _NODE_LABELS


def _sse_encode(event: dict) -> str:
    """将事件字典编码为 SSE 文本帧: event: <type>\ndata: <json>\n\n"""
    evt_type = event["event"]
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {evt_type}\ndata: {payload}\n\n"


# ═══════════════════════════════════════════════════
# POST /chat — 同步对话（非流式，兼容旧版）
# ═══════════════════════════════════════════════════
@router.post("", response_model=ChatResponse, responses={500: {"model": ErrorResponse}})
async def chat(req: ChatRequest, request: Request,
               _rate=Depends(require_rate_limit)):
    """提交自然语言问题，Multi-Agent 自动拆解+执行+汇总"""
    t0 = time.monotonic()
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    try:
        answer = await asyncio.to_thread(
            agent.ask, req.question, req.session_id, kb_id=kb_id,
            model=req.model or "", domain_hint=req.domain_hint or "")
        chat_request_total.labels(status="ok").inc()
        return ChatResponse(
            answer=answer,
            session_id=req.session_id,
            sources=getattr(agent, "_last_sources", []),
        )
    except Exception:
        chat_request_total.labels(status="error").inc()
        raise
    finally:
        chat_request_duration_seconds.observe(time.monotonic() - t0)


# ═══════════════════════════════════════════════════
# POST /chat/stream — SSE 流式对话 (v2)
# ═══════════════════════════════════════════════════
@router.post("/stream", responses={500: {"model": ErrorResponse}})
async def chat_stream(
    r: Request,
    _rate=Depends(require_rate_limit),
):
    """手动从 body 解析 ChatRequest，规避 FastAPI 自动 body 解析对中文 payload 的 bug。

    当前 FastAPI/Pydantic 组合对 body 解析在某些中文 payload 下会抛
    "There was an error parsing the body"（即使 chat_stream 本身能正常工作），
    这里直接读 request.json() 手动反序列化，已验证可稳定运行。
    """
    raw = await r.json()
    try:
        req = ChatRequest(**raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"ChatRequest 解析失败: {e}")

    t0 = time.monotonic()
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    request_id = req.request_id or "default"

    # user_id 解析：统一走 identity.py（P3 收敛）。
    # legacy=请求体优先+网关头兜底（现网行为不变）；header/strict=网关权威，body 身份被无视。
    from backend.app.api.identity import resolve_identity
    ident = resolve_identity(r, body_user_id=req.user_id)
    user_id = ident.user_id or "default"
    key = _request_key(req.session_id, request_id)

    # —— 队列与中止标志延后到生成器内部，确保只在真正进入流式后注册 _active_stops；
    #    之前的代码在请求 body 解析前就注册，r.json() 抛错时不会清理（P1-10 修复）。 ——
    q: queue.Queue = queue.Queue(maxsize=_SSE_QUEUE_MAXSIZE)
    stop_event: threading.Event = threading.Event()

    # —— 握手事件：发送 node_labels 映射表 ——
    meta_event = _sse_encode({
        "event": "meta",
        "data": {"node_labels": _NODE_LABELS},
    })

    def producer():
        """在 executor 线程中运行 LangGraph，事件逐个入队。

        Backpressure（P0-1）：队列满时不再静默丢弃——
          ① 记 metric（可观测性）；
          ② 设 stop_event，让上游 LLM 链路尽快退出；
          ③ 入队 sentinel 让 consumer 干净收尾。
        """
        nonlocal stop_event
        try:
            for evt in agent.stream_events(
                req.question,
                req.session_id,
                kb_id=kb_id,
                stop_event=stop_event,
                user_id=user_id,
                department=req.department or "",
                model=req.model or "",
                domain_hint=req.domain_hint or "",
            ):
                if stop_event.is_set():
                    break
                # 累计产出 metric（与 dropped 对比，监控常态丢弃率）
                evt_name = evt.get("event", "?")
                try:
                    chat_stream_event_produced_total.labels(event=evt_name).inc()
                except Exception:
                    logger.debug("[P1-10] produced_total 指标上报失败", exc_info=True)
                try:
                    q.put(evt, timeout=0.05)
                except queue.Full:
                    # backpressure：服务端快 / 客户端慢 → 不能丢，否则流式内容跳字
                    chat_stream_event_dropped_total.labels(reason="queue_full").inc()
                    if stop_event.is_set():
                        break
                    # 触发上游停止，并把 None 入队让 consumer 干净收尾
                    stop_event.set()
                    break
        except Exception as exc:
            chat_stream_event_dropped_total.labels(reason="producer_error").inc()
            try:
                q.put(
                    {"event": "error", "data": {"message": str(exc), "ts": time.time()}},
                    timeout=0.05,
                )
            except queue.Full:
                pass
        finally:
            try:
                q.put(None, timeout=0.05)  # sentinel
            except queue.Full:
                pass

    async def event_generator():
        """异步生成器：从队列取事件 → SSE 格式化 → yield。"""
        # 注册中止标志（cleanup 在 finally 强制执行）
        _active_stops[key] = stop_event
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_executor, producer)

        client_aborted = False
        final_status = "ok"
        _latency_tracker = StreamLatencyTracker(start=t0)

        def _record_status(status: str):
            """真实记录 ok/error/abort 计数（P0-2：原 _record_stream_metrics 是死代码）。"""
            try:
                chat_request_total.labels(status=status).inc()
            except Exception:
                logger.debug("[P1-10] request_total 指标上报失败", exc_info=True)

        try:
            yield meta_event

            last_yield_at = time.monotonic()  # 心跳节流基准（P0 保活）

            while True:
                # 阻塞拉取：避免 100Hz 轮询消耗 CPU（P1-14）
                try:
                    evt = await loop.run_in_executor(None, q.get, True, _SSE_GET_TIMEOUT)
                except queue.Empty:
                    # 超时：检查 producer 是否已结束；未结束则继续等
                    if future.done() and q.empty():
                        break
                    # SSE 心跳保活（P0）：长 workflow（market_research 156s）执行期
                    # 事件稀疏，代理/浏览器对无数据连接按空闲超时断流（实测 ~97s），
                    # final_answer 因此到不了页面。q.get 0.5s 一轮，按 WALL间隔 15s
                    # 节流发 ping——字节流重置链路空闲计时，前端解析器透传、
                    # store 归约层 default 忽略，零副作用。
                    now = time.monotonic()
                    if now - last_yield_at >= _SSE_PING_INTERVAL:
                        last_yield_at = now
                        yield _sse_encode({"event": "ping", "data": {"ts": time.time()}})
                    continue

                if evt is None:  # sentinel → producer 走完（正常/异常）
                    break

                evt_type = evt.get("event")
                if evt_type == "error":
                    final_status = "error"
                elif evt_type == "delta":
                    # TTFT / TPOT：首 delta 记 TTFT，收尾统一算 TPOT（P1 流式核心验收指标）
                    try:
                        _latency_tracker.on_delta(time.monotonic())
                    except Exception:
                        logger.debug("[P1] 流式延迟指标记录失败", exc_info=True)
                yield _sse_encode(evt)
                last_yield_at = time.monotonic()  # 有真实事件流动，心跳计时重置
                await asyncio.sleep(0)  # 让出事件循环

        except GeneratorExit:
            # 前端主动断开连接 → 触发后端停止
            client_aborted = True
            stop_event.set()
        finally:
            # 兜底：确保生产者退出（即便异常路径），避免线程悬挂
            stop_event.set()
            _active_stops.pop(key, None)

            # 等待 producer 真正结束再算耗时（更准确）
            try:
                await asyncio.wait_for(future, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

            if client_aborted:
                _record_status("aborted")
            else:
                _record_status(final_status)
            _latency_tracker.finish()
            chat_request_duration_seconds.observe(time.monotonic() - t0)

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
# POST /chat/messages — 持久化会话消息到 PG
# ═══════════════════════════════════════════════════
@router.post("/messages")
def save_messages(req: dict):
    """批量保存会话消息（前端 SSE done 后调用）

    body: { session_id, messages: [{ role, content }, ...] }

    同步 def + memory_manager 桥接：DB engine 绑定在 memory 后台 loop，
    主 loop 直接 await 会抛 "attached to a different loop"（CS 页消息
    持久化失败的根因）。
    """
    from backend.memory.manager import memory_manager
    from backend.memory.service import MemoryService
    return memory_manager.run_tool(
        lambda: MemoryService().save_messages(
            session_id=req.get("session_id", ""),
            messages=req.get("messages", []),
        )
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
