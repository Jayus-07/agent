"""agent_roundtable.py — 管理端「Agent 圆桌」创意演示（多 Agent 带只读工具对话）

POST /roundtable/stream — 发起一场多域 Agent 人设角色的圆桌对话，SSE 实时推送。

机制与边界（演示功能，与主图/域图零耦合）：
- 每轮按参与者顺序发言。发言 Agent 先经 function calling 判断是否调用
  自己域内的**只读工具**（真实执行走 BaseSkill.execute 治理链路），
  再基于工具结果或直接生成本轮发言；发言进入共享记录，后续角色相互回应。
- 聊完即弃：不落库、无断线续传；客户端断连即停止编排（省 token）。
- 只读白名单：写类能力（email/data_export/competitor 快照等）一律不进
  圆桌——有副作用的 Tool 仍只走主图既有 Governance/审批链路，演示场景
  从根上不给。
- 硬终止自计数：``LLM_BUDGET_MODE`` 默认 off，不能依赖预算链终止；
  轮数、参与者数、总 LLM 调用次数三层上限在此显式收敛。
- 工具失败不伪造：执行失败/异常如实回给模型继续发言，不吞异常。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.infra.llm import llm
from backend.infra.llm.proxy import bind_llm_for_role
from backend.infra.llm.rate_limiter import require_rate_limit
from backend.observability.llm_context import llm_attribution_scope
from backend.shared.logger import logger

router = APIRouter(prefix="/roundtable", tags=["Agent Roundtable"])

# ── 演示硬限制（自计数终止；改大需同步评估 token 成本） ──────────────────
MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 5
MAX_ROUNDS = 4
# 单场上限 = 5 人 × 4 轮 × 2 次（工具决策 + 结果整合），留 4 次余量
MAX_TOTAL_LLM_CALLS = 24
SPEECH_MAX_TOKENS = 350          # 单条发言输出上限（演示节奏 + 成本兜底）
TOOL_TIMEOUT_S = 30.0            # 单次工具执行超时
TOOL_RESULT_MAX_CHARS = 4000     # 回填给模型的工具结果截断
SSE_QUEUE_MAXSIZE = 256
SSE_PING_INTERVAL = 15.0         # 与 chat.py 同款：链路空闲 ~97s 断流，15s 足够
TOPIC_MAX_CHARS = 200

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="roundtable")


# ── 角色人设（演示常量；label 兜底，运行时优先取域图注册表） ─────────────
_PERSONAS: dict[str, dict[str, Any]] = {
    "travel": {
        "display_name": "行程规划师",
        "fallback_label": "旅游规划",
        "persona": (
            "你是圆桌中的「行程规划师」，一位资深旅游规划专家。你热情健谈，"
            "关注行程体验、亲子友好度与路线合理性，习惯给出具体的地点与动线建议。"
        ),
    },
    "customer_service": {
        "display_name": "客服专员",
        "fallback_label": "智能客服",
        "persona": (
            "你是圆桌中的「客服专员」，一位耐心细致的资深客服。你关注用户体验、"
            "退改签政策与售后风险，常引用服务数据和历史工单经验提醒大家避坑。"
        ),
    },
    "selection_funnel": {
        "display_name": "选品分析师",
        "fallback_label": "智能选品",
        "persona": (
            "你是圆桌中的「选品分析师」，数据驱动、理性克制，习惯用趋势、"
            "价格与销量数据说话，给出量化依据后再下结论。"
        ),
    },
}

# ── 域 → 只读工具箱（白名单；写类能力一律不放行） ─────────────────────────
_READONLY_CAPABILITIES = frozenset({
    "web.search", "rag.search", "sql.query", "travel.poi_search", "map.lookup",
})
_DOMAIN_TOOLS: dict[str, tuple[str, ...]] = {
    "travel": ("travel.poi_search", "map.lookup", "web.search"),
    "customer_service": ("rag.search", "sql.query", "web.search"),
    "selection_funnel": ("web.search", "sql.query", "rag.search"),
}

_tools_cache: dict[str, tuple[list[dict], dict[str, str]]] = {}


def _domain_label(route_mode: str) -> str:
    """域展示标签：与域图注册表同源，注册表不可用时退人设兜底值。"""
    try:
        from backend.orchestration.domain_registry import domain_graph_registry

        graph = domain_graph_registry.get(route_mode)
        if graph is not None and graph.label:
            return graph.label
    except Exception:
        logger.debug("[Roundtable] 域标签获取失败: %s", route_mode, exc_info=True)
    return str(_PERSONAS.get(route_mode, {}).get("fallback_label") or route_mode)


def _domain_tools(route_mode: str) -> tuple[list[dict], dict[str, str]]:
    """capability → OpenAI tools schema（capabilities_to_tools 单一事实源）。"""
    if route_mode in _tools_cache:
        return _tools_cache[route_mode]
    from backend.orchestration.tool_schema import capabilities_to_tools

    caps = [c for c in _DOMAIN_TOOLS.get(route_mode, ()) if c in _READONLY_CAPABILITIES]
    tools, fn2cap = capabilities_to_tools(caps)
    _tools_cache[route_mode] = (tools, fn2cap)
    return tools, fn2cap


# ── SSE 帧编码（与 chat.py _sse_encode 同协议的简化版：无 seq/断线续传） ──
def _sse_encode(event: dict) -> str:
    payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
    return f"event: {event['event']}\ndata: {payload}\n\n"


def _evt(event: str, data: dict) -> dict:
    return {"event": event, "data": {"ts": time.time(), **data}}


# ── 工具执行：走 BaseSkill.execute 既有治理链路（校验/权限/Trace） ────────
def _execute_capability(capability: str, args: dict) -> tuple[bool, str, float]:
    """真实执行一个只读能力。返回 (ok, 结果或失败文本, 耗时秒)。失败不抛。"""
    t0 = time.monotonic()
    try:
        from backend.skills import registry as skill_registry

        skill = skill_registry.get(capability)
    except Exception:
        skill = None
    if skill is None:
        return False, f"能力 {capability} 未注册，无法执行。", 0.0

    step_id = f"rt_{uuid.uuid4().hex[:12]}"
    state = {
        "current_step_id": step_id,
        "plan": {
            "nodes": {
                step_id: {
                    "capability": capability,
                    "params": dict(args or {}),
                    "description": "",
                },
            },
        },
        "step_results": {},
    }

    async def _run():
        return await skill.execute(state, step_capability=capability, timeout=TOOL_TIMEOUT_S)

    try:
        # worker 线程无运行中事件循环，asyncio.run 安全（演示编排为串行）
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning("[Roundtable] 工具执行异常: %s", capability, exc_info=True)
        return False, f"工具执行异常：{type(exc).__name__}: {exc}", time.monotonic() - t0

    sr = (result or {}).get("step_results", {}).get(step_id, {})
    elapsed = time.monotonic() - t0
    if sr.get("status") != "success":
        return (
            False,
            f"工具执行失败（{sr.get('status') or 'unknown'}）：{sr.get('error') or '无输出'}",
            elapsed,
        )
    text = json.dumps(sr.get("output"), ensure_ascii=False, default=str)
    if len(text) > TOOL_RESULT_MAX_CHARS:
        text = text[:TOOL_RESULT_MAX_CHARS] + "…（已截断）"
    return True, text or "（工具返回空结果）", elapsed


# ── 单个 Agent 的一次发言（工具决策 → 可选执行 → 发言） ───────────────────
def _system_prompt(p: dict, topic: str, others: list[str]) -> str:
    tool_names = "、".join(_DOMAIN_TOOLS.get(p["route_mode"], ())) or "无"
    return (
        f"{p['persona']}\n\n"
        f"圆桌话题：{topic}\n"
        f"在座其他角色：{'、'.join(others)}\n\n"
        "发言要求：\n"
        "- 用中文，直接说出你的观点，2~5 句话，简洁有信息量；\n"
        "- 从你的专业视角发言，可回应其他角色此前的发言，不要重复他人已说内容；\n"
        "- 不要自称 AI 或语言模型。\n\n"
        f"你可以使用以下只读工具获取真实数据：{tool_names}。\n"
        "仅当发言需要真实数据支撑时才调用工具；纯观点交流直接发言，不要调用。"
    )


def _transcript_text(topic: str, transcript: list[tuple[str, str]], p: dict) -> str:
    lines = [f"【圆桌话题】{topic}"]
    if transcript:
        lines.append("【目前的讨论记录】")
        lines.extend(f"{name}：{content}" for name, content in transcript)
        lines.append(f"\n【轮到你】请以「{p['display_name']}」的身份说出你的发言。")
    else:
        lines.append(f"\n【轮到你】你是第一个发言者，请以「{p['display_name']}」的身份开个头。")
    return "\n".join(lines)


def _content_of(raw: Any) -> str:
    content = getattr(raw, "content", "") or ""
    if isinstance(content, list):  # 多模态分段防御性拼接
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content).strip()


def _agent_turn(
    p: dict,
    topic: str,
    transcript: list[tuple[str, str]],
    others: list[str],
    call_budget: int,
) -> tuple[list[dict], str | None, int]:
    """一个 Agent 的一次发言。返回 (中间事件, 发言文本(失败为 None), 累计调用数)。"""
    events: list[dict] = []
    calls = call_budget
    role_llm = bind_llm_for_role("main", temperature=0.8, max_tokens=SPEECH_MAX_TOKENS)
    sys_prompt = _system_prompt(p, topic, others)
    user_text = _transcript_text(topic, transcript, p)
    msgs: list[tuple[str, str]] = [("system", sys_prompt), ("human", user_text)]

    tools, fn2cap = _domain_tools(p["route_mode"])
    bound = None
    if tools:
        try:
            bound = role_llm.bind_tools(tools)
        except Exception:  # 模型不支持 FC → 降级纯人设发言（能力门控失败不致命）
            logger.warning("[Roundtable] bind_tools 不可用，%s 降级纯发言", p["route_mode"])

    with llm_attribution_scope(
        skill_id="agent_roundtable", agent_domain=p["route_mode"],
    ):
        first = (bound or role_llm).invoke(msgs)
        calls += 1
        content = _content_of(first)
        tool_calls = getattr(first, "tool_calls", None) or []

        if bound is not None and tool_calls and calls < MAX_TOTAL_LLM_CALLS:
            tc = tool_calls[0]
            capability = fn2cap.get(str(tc.get("name", "")))
            args = dict(tc.get("args") or {})
            if capability:
                events.append(_evt("status", {
                    "stage": "tool", "agent": p["route_mode"],
                    "display_name": p["display_name"], "capability": capability,
                    "message": f"正在调用 {capability}…",
                }))
                ok, result_text, elapsed = _execute_capability(capability, args)
                events.append(_evt("tool_result", {
                    "agent": p["route_mode"], "display_name": p["display_name"],
                    "capability": capability, "ok": ok,
                    "summary": (
                        f"{elapsed:.1f}s · {len(result_text)} 字符"
                        if ok else f"失败 · {result_text[:80]}"
                    ),
                }))
                # 工具结果以文本并入上下文重新生成发言——不走原生 tool 消息
                # 回填，规避不同 provider 的 tool_calls 兼容性差异（演示定位）
                calls += 1
                follow = (
                    f"{user_text}\n\n"
                    f"【你刚调用了工具 {capability}，{'结果' if ok else '但执行失败'}如下】\n"
                    f"{result_text}\n\n请基于以上信息完成你的发言；"
                    "若执行失败，请如实说明并继续给出你的观点。"
                )
                second = role_llm.invoke([("system", sys_prompt), ("human", follow)])
                second_text = _content_of(second)
                if second_text:
                    content = second_text
    if not content:
        return events, None, calls
    return events, content, calls


# ── 圆桌编排（producer 线程内运行的同步生成器） ───────────────────────────
def _roundtable_events(
    topic: str,
    participants: list[dict],
    max_rounds: int,
    stop_event: threading.Event,
    request_id: str = "",
) -> Iterator[dict]:
    from backend.observability.tracer import trace_collector

    # 开 trace session：演示也要过治理——Trace 页面可见完整会话树，
    # llm_usage 台账行借此带上 trace_id/request_id，可按请求对账
    trace = trace_collector.start(
        question=f"[圆桌] {topic}",
        session_id=f"roundtable:{request_id}" if request_id else "roundtable",
        workflow_name="agent_roundtable",
    )
    t0 = time.monotonic()
    call_count = 0
    message_count = 0
    transcript: list[tuple[str, str]] = []
    others = [q["display_name"] for q in participants]
    try:
        for round_no in range(1, max_rounds + 1):
            for p in participants:
                if stop_event.is_set():
                    return
                if call_count >= MAX_TOTAL_LLM_CALLS:
                    logger.warning("[Roundtable] 达到调用上限，提前收尾: %d", call_count)
                    yield _evt("status", {
                        "stage": "budget", "message": "已达单场 LLM 调用上限，圆桌提前收尾。",
                    })
                    return
                yield _evt("status", {
                    "stage": "thinking", "agent": p["route_mode"],
                    "display_name": p["display_name"], "round": round_no,
                    "message": f"{p['display_name']} 正在思考…",
                })
                # 发言 span：工具 span 由 BaseSkill.execute 自建并挂同一 trace
                speech_span = trace_collector.start_span(
                    f"speech_r{round_no}_{p['route_mode']}",
                    name=f"{p['display_name']} 发言", type="agent",
                )
                events, content, call_count = _agent_turn(
                    p, topic, transcript, others, call_count,
                )
                for evt in events:
                    yield evt
                if content is None:
                    # LLM 链路异常/空回复：如实终止，不伪造发言
                    trace_collector.end_span(speech_span, status="error")
                    yield _evt("error", {"message": f"{p['display_name']} 发言生成失败，圆桌终止。"})
                    return
                trace_collector.end_span(
                    speech_span, output={"round": round_no, "chars": len(content)},
                )
                transcript.append((p["display_name"], content))
                message_count += 1
                yield _evt("message", {
                    "agent": p["route_mode"], "agent_label": _domain_label(p["route_mode"]),
                    "display_name": p["display_name"], "round": round_no,
                    "content": content,
                })
        yield _evt("done", {
            "elapsed": round(time.monotonic() - t0, 1),
            "rounds": max_rounds,
            "message_count": message_count,
        })
    except Exception as exc:  # 编排未知异常 → error 终帧，不挂死连接
        logger.error("[Roundtable] 编排异常终止", exc_info=True)
        yield _evt("error", {"message": f"圆桌编排异常终止：{type(exc).__name__}: {exc}"})
    finally:
        try:
            trace_collector.finish(
                trace, f"圆桌：{message_count} 条发言",
                int((time.monotonic() - t0) * 1000), model="", provider="",
            )
        except Exception:  # 持久化失败不影响已推送的事件（软失败）
            logger.warning("[Roundtable] trace 收尾失败", exc_info=True)


# ── 请求模型与端点 ────────────────────────────────────────────────────────
class RoundtableRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=TOPIC_MAX_CHARS)
    participants: list[str] = Field(min_length=MIN_PARTICIPANTS, max_length=MAX_PARTICIPANTS)
    max_rounds: int = Field(default=2, ge=1, le=MAX_ROUNDS)


@router.get("/participants")
async def list_participants():
    """圆桌可选角色清单（只读，无 LLM 消耗）：配置面板的数据源。

    事实源在本模块 _PERSONAS/_DOMAIN_TOOLS；前端不硬编码第二份名单。
    """
    return {
        "limits": {
            "min_participants": MIN_PARTICIPANTS,
            "max_participants": MAX_PARTICIPANTS,
            "max_rounds": MAX_ROUNDS,
            "topic_max_chars": TOPIC_MAX_CHARS,
            "max_total_llm_calls": MAX_TOTAL_LLM_CALLS,
        },
        "participants": [
            {
                "route_mode": mode,
                "display_name": persona["display_name"],
                "label": _domain_label(mode),
                "tools": list(_DOMAIN_TOOLS.get(mode, ())),
            }
            for mode, persona in _PERSONAS.items()
        ],
    }


@router.post("/stream")
async def roundtable_stream(r: Request, _rate=Depends(require_rate_limit)):
    """发起圆桌：管理员闸 + 速率限制；SSE 推送，断连即停（演示不续传）。"""
    await _require_admin(r)

    try:
        raw = await r.json()
    except Exception:
        raise HTTPException(status_code=422, detail={"message": "请求体不是合法 JSON。"})
    try:
        req = RoundtableRequest(**(raw or {}))
    except Exception:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    f"参数有误：topic 为 1~{TOPIC_MAX_CHARS} 字；participants 为 "
                    f"{MIN_PARTICIPANTS}~{MAX_PARTICIPANTS} 个有效角色；max_rounds 为 1~{MAX_ROUNDS}。"
                ),
            },
        )
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail={"message": "话题不能为空。"})

    # 参与者去重保序 + 白名单校验
    seen: set[str] = set()
    participants: list[dict] = []
    for mode in req.participants:
        mode = str(mode).strip()
        if mode in seen:
            continue
        seen.add(mode)
        persona = _PERSONAS.get(mode)
        if persona is None:
            raise HTTPException(
                status_code=422,
                detail={"message": f"未知角色：{mode}；可选：{'、'.join(_PERSONAS)}。"},
            )
        participants.append({**persona, "route_mode": mode})
    if len(participants) < MIN_PARTICIPANTS:
        raise HTTPException(
            status_code=422,
            detail={"message": f"去重后参与者不足 {MIN_PARTICIPANTS} 个。"},
        )

    request_id = uuid.uuid4().hex
    meta = _evt("meta", {
        "request_id": request_id,
        "topic": topic,
        "max_rounds": req.max_rounds,
        "participants": [
            {
                "route_mode": p["route_mode"],
                "display_name": p["display_name"],
                "label": _domain_label(p["route_mode"]),
                "tools": list(_DOMAIN_TOOLS.get(p["route_mode"], ())),
            }
            for p in participants
        ],
    })

    aq: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
    stop_event = threading.Event()
    loop = asyncio.get_running_loop()
    sentinel = object()

    def _put(evt) -> None:
        try:
            aq.put_nowait(evt)
        except asyncio.QueueFull:
            logger.warning("[Roundtable] SSE 队列满，丢弃事件（背压）")

    def _put_threadsafe(evt) -> None:
        try:
            loop.call_soon_threadsafe(_put, evt)
        except RuntimeError:
            pass  # 应用关停时放弃投递

    def producer() -> None:
        try:
            for evt in _roundtable_events(
                topic, participants, req.max_rounds, stop_event, request_id,
            ):
                if stop_event.is_set():
                    return
                _put_threadsafe(evt)
        finally:
            _put_threadsafe(sentinel)

    async def event_generator():
        future = loop.run_in_executor(_executor, producer)
        try:
            yield _sse_encode(meta)
            last_yield = time.monotonic()
            while True:
                try:
                    evt = await asyncio.wait_for(aq.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    if (future.done() and aq.empty()) or stop_event.is_set():
                        break
                    now = time.monotonic()
                    if now - last_yield >= SSE_PING_INTERVAL:
                        last_yield = now
                        yield _sse_encode(_evt("ping", {"source": "roundtable"}))
                    continue
                if evt is sentinel:
                    break
                yield _sse_encode(evt)
                last_yield = time.monotonic()
        except (GeneratorExit, asyncio.CancelledError):
            # 演示无续传：断连即停编排，未发生的发言不再消耗 token
            stop_event.set()
            raise
        finally:
            stop_event.set()  # 兜底：任何退出路径都让 producer 结束，避免线程悬挂

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )


async def _require_admin(r: Request) -> None:
    from backend.app.api.routes.observability import require_admin_operator

    await require_admin_operator(r)
