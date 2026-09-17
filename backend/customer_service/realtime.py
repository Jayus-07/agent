"""customer_service/realtime.py — 坐席侧实时推送 Hub（WebSocket）

2026-09-17 人工介入 v2：坐席工作台下行通道从 2s 轮询升级为 WebSocket 推送，
轮询降级保留（transport fallback，见 frontend-admin 工作台）。

事件流（服务端 → 坐席）:
  hello                连接建立确认
  heartbeat            25s 心跳
  conversation.waiting 新工单进入排队（HandoffExpert 触发）
  conversation.claimed 坐席认领成功（含其他坐席，用于队列摘除）
  conversation.closed  会话关闭
  message.created      新消息（user / assistant / human_agent，含 last_id 游标）

线程模型：
  - CS 图节点是 sync 函数（跑在 graph 执行线程），业务点调用 ``publish()``
    → ``run_coroutine_threadsafe`` 跳到主 uvicorn loop，fire-and-forget 不阻塞。
  - WS 连接全部注册在主 loop 上，send 只发生在主 loop。
  - 广播失败仅记日志，永不影响业务主链路。

鉴权：
  HTTP 端点（X-API-Key 保护）签发一次性 ticket（默认 60s TTL、单次使用），
  浏览器凭 ticket 查询参数完成 WS 握手 —— API Key 不进浏览器。
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time

from fastapi import WebSocket

from backend.shared.logger import logger

TICKET_TTL_SECONDS = 60


class AgentHub:
    """在线坐席连接注册表 + 事件广播器（单例，见 get_agent_hub）。"""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._tickets: dict[str, float] = {}  # ticket -> 过期时刻 (monotonic)
        self._main_loop: asyncio.AbstractEventLoop | None = None

    # ── lifecycle ────────────────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """startup 时绑定主 uvicorn loop（publish 的跳板）。"""
        self._main_loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        logger.info(
            "[AgentHub] agent connected, total=%d", len(self._connections)
        )

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.discard(ws)
            logger.info(
                "[AgentHub] agent disconnected, total=%d",
                len(self._connections),
            )

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ── ticket 鉴权 ──────────────────────────────────────────

    def issue_ticket(self) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        # 顺手清理过期 ticket，防长驻进程缓慢泄漏
        self._tickets = {
            t: exp for t, exp in self._tickets.items() if exp > now
        }
        self._tickets[token] = now + TICKET_TTL_SECONDS
        return token

    def redeem_ticket(self, token: str) -> bool:
        """一次性核销：有效返回 True，过期/不存在/已用一律 False。"""
        exp = self._tickets.pop(token, None)
        return exp is not None and exp > time.monotonic()

    # ── publish ──────────────────────────────────────────────

    def publish(self, event_type: str, **payload) -> None:
        """线程安全 fire-and-forget 广播；无连接 / 未绑 loop 时静默丢弃。"""
        if not self._connections or self._main_loop is None:
            return
        message = {"type": event_type, **payload}
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(message), self._main_loop,
            )
        except Exception:
            logger.debug(
                "[AgentHub] publish %s failed", event_type, exc_info=True
            )

    async def _broadcast(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_hub_instance = AgentHub()


def get_agent_hub() -> AgentHub:
    return _hub_instance
