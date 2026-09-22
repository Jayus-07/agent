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
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from backend.shared.logger import logger
from backend.shared.error_protocol import (
    ErrorCode,
    ErrorEnvelope,
    sse_error_event as build_sse_error_event,
)
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


def _resolve_request_id(raw: str | None) -> str:
    """#14：客户端未提供有效 id（空或 "default" 占位）→ 服务端生成唯一 id。

    同一 session 的并发流都落到 "default" 时，_active_stops 互相覆盖、
    abort 误中止别人的流。服务端唯一化后经 meta 事件 + X-Request-Id 头回传。
    """
    if raw and raw != "default":
        return raw
    return uuid.uuid4().hex


def _aq_put_nowait(aq: asyncio.Queue, evt) -> None:
    """call_soon_threadsafe 回调：非阻塞入队（满时计 metric，不抛回事件循环）。"""
    try:
        aq.put_nowait(evt)
    except asyncio.QueueFull:
        chat_stream_event_dropped_total.labels(reason="queue_full").inc()


def _put_final_frame(aq: asyncio.Queue, loop, stop_event: threading.Event,
                     evt, wait: float = 5.0) -> None:
    """收尾帧（error/sentinel）尽力投递：队列满时等 consumer 腾位（#16）。

    stop_event 已置（客户端断开）时跳过等待——此时无人消费，等也等不到。
    提为模块级便于单测（TestClient 的 asgi transport 缓冲无限，无法端到端
    模拟真实传输背压）。
    """
    deadline = time.monotonic() + wait
    while aq.full() and time.monotonic() < deadline and not stop_event.is_set():
        time.sleep(0.01)
    try:
        loop.call_soon_threadsafe(_aq_put_nowait, aq, evt)
    except RuntimeError:
        pass  # loop 已关闭（应用关停），放弃投递


def _validate_model_override(model: str | None) -> None:
    """API 边界 fail-fast：请求级模型覆盖非法 → 400。

    与上下文绑定层（proxy.set_request_model）的宽容静默**分层**（契约见
    docs/model-config-admin-ui-design.md §13.1）：
      - 本层拒绝：HTTP 边界是唯一能拿到「把原因还给用户」的上下文；
        否则非法 model 被静默吞掉、实际跑全局默认，用户以为在用自己选的模型；
      - 绑定层仍 warning + 清空：非 HTTP 调用方（评测生成 / 脚本）无请求可报错，
        且 tests/test_llm_bind_tools.py 有 4 例锁定该静默语义。

    空 model = 不覆盖，直接放行。
    """
    if not model:
        return
    from backend.infra.llm.models import validate_override_model

    ok, reason = validate_override_model(model)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail={"error": "InvalidModelOverride", "message": reason},
        )


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


def _sse_error_event(exc: BaseException, trace_id: str = "") -> dict:
    """SSE 失败帧统一走错误协议，保留 event:error 兼容旧客户端。"""
    event = build_sse_error_event(exc, trace_id=trace_id, source="sse")
    # ts 是旧版前端 ErrorEvent 的兼容字段；协议字段由统一适配器负责。
    event["data"]["ts"] = time.time()
    return event


# ═══════════════════════════════════════════════════
# POST /chat — 同步对话（非流式，兼容旧版）
# ═══════════════════════════════════════════════════
@router.post("", response_model=ChatResponse, responses={500: {"model": ErrorResponse}})
async def chat(req: ChatRequest, request: Request,
               _rate=Depends(require_rate_limit)):
    """提交自然语言问题，Multi-Agent 自动拆解+执行+汇总"""
    _validate_model_override(req.model)
    t0 = time.monotonic()
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    from backend.app.api.identity import resolve_identity
    ident = resolve_identity(request, body_user_id=req.user_id)
    user_id = ident.user_id or "default"
    try:
        answer = await asyncio.to_thread(
            agent.ask, req.question, req.session_id, kb_id=kb_id,
            user_id=user_id, department=ident.department,
            permissions=ident.permissions, model=req.model or "",
            domain_hint=req.domain_hint or "", tenant_id=ident.tenant_id,
            idempotency_key=(request.headers.get("Idempotency-Key") or "").strip(),
            roles=ident.roles)
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

    Chat/RAG 收口（2026-09-22）：解析失败区分「输入超长」（业务语义
    CHAT_INPUT_TOO_LARGE + limit_chars，引导走知识库上传）与其它参数错误
    （通用 INVALID_PARAM，实现细节只落日志不进响应）。
    """
    # 请求体字节上限（防超大 payload；APISIX client_max_body_size=0 不限，
    # 产品限制以 app 层为权威 —— 超限给业务错误而非网关/解析器裸错误）
    from backend.app.exceptions import _http_payload
    from backend.config.chat_input import CHAT_INPUT_MAX_BYTES, CHAT_INPUT_MAX_CHARS

    def _too_large_response(limit_bytes: int | None = None) -> JSONResponse:
        envelope = ErrorEnvelope(
            code=ErrorCode.INVALID_PARAM,
            retryable=False,
            handoff_available=False,
            message="输入内容过长，请缩短内容或通过知识库文件上传处理。",
            source="http",
            details={"reason": "CHAT_INPUT_TOO_LARGE",
                     "limit_chars": CHAT_INPUT_MAX_CHARS,
                     **({"limit_bytes": limit_bytes} if limit_bytes else {})},
        )
        return JSONResponse(status_code=422, content=_http_payload(envelope))

    content_length = int(r.headers.get("content-length") or 0)
    if content_length > CHAT_INPUT_MAX_BYTES:
        return _too_large_response(limit_bytes=CHAT_INPUT_MAX_BYTES)

    try:
        raw = await r.json()
    except Exception as e:
        logger.warning(f"[ChatStream] body 解析失败: {type(e).__name__}")
        envelope = ErrorEnvelope(
            code=ErrorCode.INVALID_PARAM,
            retryable=False,
            handoff_available=False,
            message="请求参数有误，请检查后重试。",
            source="http",
        )
        return JSONResponse(status_code=422, content=_http_payload(envelope))
    try:
        req = ChatRequest(**raw)
    except ValidationError as e:
        too_long = any(
            "question" in (err.get("loc") or ())
            and err.get("type") in ("string_too_long", "length_error", "value_error")
            for err in e.errors()
        )
        if too_long:
            return _too_large_response()
        envelope = ErrorEnvelope(
            code=ErrorCode.INVALID_PARAM,
            retryable=False,
            handoff_available=False,
            message="请求参数有误，请检查后重试。",
            source="http",
        )
        return JSONResponse(status_code=422, content=_http_payload(envelope))
    except Exception as e:
        logger.warning(f"[ChatStream] ChatRequest 解析失败: {type(e).__name__}")
        envelope = ErrorEnvelope(
            code=ErrorCode.INVALID_PARAM,
            retryable=False,
            handoff_available=False,
            message="请求参数有误，请检查后重试。",
            source="http",
        )
        return JSONResponse(status_code=422, content=_http_payload(envelope))

    _validate_model_override(req.model)

    t0 = time.monotonic()
    agent = get_multi_agent()
    kb_id = req.kb_id or "default"
    request_id = _resolve_request_id(req.request_id)

    # user_id 解析：统一走 identity.py（P3 收敛）。
    # legacy=请求体优先+网关头兜底（现网行为不变）；header/strict=网关权威，body 身份被无视。
    from backend.app.api.identity import resolve_identity
    ident = resolve_identity(r, body_user_id=req.user_id)
    user_id = ident.user_id or "default"
    key = _request_key(req.session_id, request_id)

    # —— 中止标志延后到生成器内部注册，确保只在真正进入流式后注册 _active_stops；
    #    之前的代码在请求 body 解析前就注册，r.json() 抛错时不会清理（P1-10 修复）。 ——
    # #15：producer（executor 线程）→ consumer（事件循环）改用 asyncio.Queue +
    #    call_soon_threadsafe 投递。旧实现 consumer 侧 run_in_executor(None, q.get)
    #    每路流常驻占用默认线程池 1 线程做 0.5s 轮询，并发流多时挤占
    #    asyncio.to_thread（/chat 同步路径）等默认池消费者。
    stop_event: threading.Event = threading.Event()
    loop = asyncio.get_running_loop()
    aq: asyncio.Queue = asyncio.Queue(maxsize=_SSE_QUEUE_MAXSIZE)

    # —— 握手事件：发送 node_labels 映射表 + 服务端 request_id（#14 回传）——
    meta_event = _sse_encode({
        "event": "meta",
        "data": {"node_labels": _NODE_LABELS, "request_id": request_id},
    })

    def _threadsafe_put(evt) -> bool:
        """producer 线程跨线程投递到 consumer 的 asyncio.Queue；loop 已关则放弃。"""
        try:
            loop.call_soon_threadsafe(_aq_put_nowait, aq, evt)
            return True
        except RuntimeError:
            return False

    def producer():
        """在 executor 线程中运行 LangGraph，事件跨线程投递到 asyncio.Queue。

        Backpressure（P0-1 + #16）：队列满时不再静默丢弃——
          ① 记 metric（可观测性）；
          ② 先入队 error 帧告知前端「流被截断」（不能静默收尾让前端误判正常结束）；
          ③ 设 stop_event 让上游 LLM 链路尽快退出；
          ④ 投递 sentinel 让 consumer 干净收尾。
        """
        try:
            for evt in agent.stream_events(
                req.question,
                req.session_id,
                kb_id=kb_id,
                stop_event=stop_event,
                user_id=user_id,
                department=ident.department,
                permissions=ident.permissions,
                model=req.model or "",
                domain_hint=req.domain_hint or "",
                tenant_id=ident.tenant_id,
                idempotency_key=(r.headers.get("Idempotency-Key") or "").strip(),
                roles=ident.roles,
            ):
                if stop_event.is_set():
                    break
                # 累计产出 metric（与 dropped 对比，监控常态丢弃率）
                evt_name = evt.get("event", "?")
                try:
                    chat_stream_event_produced_total.labels(event=evt_name).inc()
                except Exception:
                    logger.debug("[P1-10] produced_total 指标上报失败", exc_info=True)
                if aq.full():
                    time.sleep(0.05)  # 给 consumer 腾位（与旧 q.put(timeout=0.05) 节流等价）
                if aq.full():
                    # backpressure：服务端快 / 客户端慢 → 截断流（丢当前帧会跳字）
                    chat_stream_event_dropped_total.labels(reason="queue_full").inc()
                    if stop_event.is_set():
                        break
                    # #16：先入队 error 帧再置 stop，前端才能区分「截断」与「正常结束」
                    _put_final_frame(aq, loop, stop_event, _sse_error_event(
                        RuntimeError("服务端背压：客户端消费过慢，流已截断"),
                    ))
                    stop_event.set()
                    break
                if not _threadsafe_put(evt):
                    break
        except Exception as exc:
            chat_stream_event_dropped_total.labels(reason="producer_error").inc()
            _put_final_frame(aq, loop, stop_event, _sse_error_event(exc))
        finally:
            _put_final_frame(aq, loop, stop_event, None)  # sentinel

    async def event_generator():
        """异步生成器：从 asyncio.Queue 取事件 → SSE 格式化 → yield。"""
        # 注册中止标志（cleanup 在 finally 强制执行）
        _active_stops[key] = stop_event
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
                # 直接 await 队列（#15）：不再经默认线程池轮询 q.get；
                # wait_for 超时后继续兜心跳与 producer 完成检测
                try:
                    evt = await asyncio.wait_for(aq.get(), timeout=_SSE_GET_TIMEOUT)
                except asyncio.TimeoutError:
                    # 超时：检查 producer 是否已结束；未结束则继续等
                    if future.done() and aq.empty():
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
            "X-Request-Id": request_id,  # #14：服务端唯一 request_id 回传（meta 事件冗余一份）
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
    # #14：服务端可能自行生成了唯一 request_id（meta 事件回传），旧客户端未回传时
    # 精确 key 未命中 → 按 session 前缀兜底（abort 语义 = 停该会话的活跃流）
    prefix = f"{req.session_id}:"
    hits = [e for k, e in _active_stops.items() if k.startswith(prefix)]
    if hits:
        for e in hits:
            e.set()
        return {"status": "aborted", "key": f"{prefix}*"}
    return {"status": "not_found", "key": key}
