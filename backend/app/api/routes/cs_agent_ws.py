"""坐席侧 WebSocket 推送端点 — /ws/cs/agent?ticket=...

下行推送通道（conversation.waiting / claimed / closed、message.created、
heartbeat）。上行不承载业务：坐席操作（认领 / 发消息 / 关闭）一律走
HTTP ticket 只接受绑定客服用户的 JWT 身份，WS 仅作 keepalive。

鉴权：HTTP 端点签发的一次性 ticket（POST /cs/conversations/agent/ws-ticket），
握手时核销 —— 无效 ticket 以 4401 关闭。FastAPI 的 HTTP 中间件
（api_key_middleware 等）不作用于 websocket scope，因此鉴权必须在此显式做。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.shared.logger import logger

router = APIRouter()

_HEARTBEAT_INTERVAL_SECONDS = 15


@router.websocket("/ws/cs/agent")
async def cs_agent_ws(
    websocket: WebSocket,
    ticket: str = Query("", description="一次性连接票据"),
):
    from backend.customer_service.realtime import get_agent_hub

    hub = get_agent_hub()
    claims = await asyncio.to_thread(hub.redeem_ticket_claims, ticket)
    if not claims:
        # accept 后再带自定义关闭码：accept 前 close 会被 Starlette
        # 折叠成 HTTP 403，客户端拿不到 4401 语义
        await websocket.accept()
        await websocket.close(code=4401)
        logger.warning("[AgentWS] reject connection: invalid/expired ticket")
        return

    presence_ok = await asyncio.to_thread(
        hub.refresh_presence,
        agent_id=claims["agent_id"],
        tenant_id=claims["tenant_id"],
    )
    if not presence_ok:
        await websocket.accept()
        await websocket.close(code=1013)
        logger.warning("[AgentWS] reject connection: Redis unavailable")
        return

    await hub.connect(
        websocket,
        agent_id=claims["agent_id"],
        tenant_id=claims["tenant_id"],
    )
    try:
        await websocket.send_json({
            "type": "hello",
            "connections": hub.connection_count,
        })
    except Exception:
        hub.disconnect(websocket)
        return

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(
            websocket,
            hub,
            agent_id=claims["agent_id"],
            tenant_id=claims["tenant_id"],
        ),
        name="agent-ws-heartbeat",
    )
    try:
        while True:
            # 客户端仅发 "ping" 保活；其余消息忽略（上行业务走 HTTP）
            raw = await websocket.receive_text()
            if raw == "ping":
                presence_ok = await asyncio.to_thread(
                    hub.refresh_presence,
                    agent_id=claims["agent_id"],
                    tenant_id=claims["tenant_id"],
                )
                if not presence_ok:
                    await websocket.close(code=1013)
                    break
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("[AgentWS] connection error: %s", e)
    finally:
        heartbeat_task.cancel()
        hub.disconnect(websocket)


async def _heartbeat_loop(
    ws: WebSocket,
    hub,
    *,
    agent_id: str,
    tenant_id: str,
) -> None:
    """服务端心跳：断连（客户端崩溃/网络中断）时抛异常退出并清理。"""
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            presence_ok = await asyncio.to_thread(
                hub.refresh_presence,
                agent_id=agent_id,
                tenant_id=tenant_id,
            )
            if not presence_ok:
                await ws.close(code=1013)
                return
            await ws.send_json({"type": "heartbeat"})
    except Exception:
        pass
