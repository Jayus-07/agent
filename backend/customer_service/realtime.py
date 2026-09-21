"""customer_service/realtime.py — 坐席侧实时推送 Hub（WebSocket + Redis pub/sub）

2026-09-17 人工介入 v2：坐席工作台下行通道从 2s 轮询升级为 WebSocket 推送，
轮询降级保留（transport fallback，见 frontend-admin 工作台）。

2026-09-17 P3.2（事件统一格式 + Redis pub/sub + 幂等/补发）：
  - 统一事件封套：``{"type", "event_id", "seq", "ts", **payload}`` —— 原有
    平铺字段原样保留（向后兼容），新增三个信封字段：
      event_id  全局唯一，客户端幂等去重键（重复投递可安全忽略）
      seq       customer_service.events.id（全局自增），断线补发游标
      ts        ISO8601 服务端时间
  - Redis pub/sub：多 worker（uvicorn 多进程/Celery）场景进程内广播失效，
    事件经 ``cs:events`` channel 广播，各进程订阅线程转发给自己持有的 WS。
    Redis 不可用 → 本进程直接广播降级；落库失败 → 进入 Redis Stream
    outbox 补偿，同时 seq=null 仍广播。
  - 断线补发：事件落库 events 表，GET /cs/conversations/{id}/events?after_seq=N
    按 seq 升序回放（见 replay_events）。

2026-09-18 性能与实用性重构：
  - 落库与广播解耦：无坐席在线时事件照常落库（events 表是补发/审计的
    权威源，此前直接丢弃会造成断线补发缺口）；广播环节才看连接数
  - 落库直连主 loop：_persist_event 已运行在 uvicorn 主 loop 上，去掉
    「线程池 → run_sync(_db_loop)」的两次线程跳转
  - 广播并发化：gather 并发发送，单慢客户端不再串行拖住所有坐席；
    序列化收敛到一处（Redis 订阅路径复用收到的原文，不再二次序列化）
  - publish(persist=False) 瞬态事件：只广播不落库（typing 类高频信号）

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
  - Redis 订阅跑在专用 daemon 线程（sync pubsub.listen 阻塞收），
    收到消息再跳回主 loop 广播。
  - 广播/落库失败仅记日志，永不影响业务主链路。

鉴权：
  HTTP 端点使用绑定客服用户的 JWT 身份签发一次性 ticket（默认 60s TTL、
  单次使用）；未建立坐席/租户绑定的 API-Key 通道被拒绝。浏览器凭 ticket
  查询参数完成 WS 握手，API Key/JWT 均不进入浏览器 WS URL。
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import WebSocket

from backend.infra.redis.client import get_redis
from backend.shared.logger import logger

TICKET_TTL_SECONDS = 60
PRESENCE_TTL_SECONDS = 45
REDIS_CHANNEL = "cs:events"
_TICKET_KEY_PREFIX = "cs:agent:ws-ticket:"
_PRESENCE_KEY_PREFIX = "cs:agent:presence:"
_GETDEL_LUA = (
    "local value = redis.call('get', KEYS[1]); "
    "if value then redis.call('del', KEYS[1]); end; "
    "return value"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentHub:
    """在线坐席连接注册表 + 事件广播器（单例，见 get_agent_hub）。"""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._connection_identity: dict[WebSocket, tuple[str, str]] = {}
        # 仅保留旧 bool API 的过期时间提示；ticket 权威始终是 Redis。
        self._tickets: dict[str, float] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._subscriber_thread: threading.Thread | None = None

    # ── lifecycle ────────────────────────────────────────────

    def bind_loop(
        self, loop: asyncio.AbstractEventLoop, start_subscriber: bool = True
    ) -> None:
        """startup 时绑定主 uvicorn loop（publish 的跳板），并拉起订阅线程。"""
        self._main_loop = loop
        if start_subscriber:
            self._ensure_subscriber()

    async def connect(
        self,
        ws: WebSocket,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """注册带租户边界的坐席连接。

        新 WS 路径必须提供已由 ticket 核销得到的身份；无身份的调用仅
        保留给旧的单元测试/内部广播兼容，不会进入在线 presence。
        """
        await ws.accept()
        self.register_connection(
            ws,
            agent_id=agent_id or "",
            tenant_id=tenant_id or "",
        )
        logger.info(
            "[AgentHub] agent connected, total=%d", len(self._connections)
        )
        return True

    def register_connection(
        self, ws: WebSocket, *, agent_id: str, tenant_id: str
    ) -> None:
        """保存连接身份，广播过滤只信任这份服务端核销结果。"""
        self._connections.add(ws)
        self._connection_identity[ws] = (tenant_id, agent_id)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.discard(ws)
            self._connection_identity.pop(ws, None)
            logger.info(
                "[AgentHub] agent disconnected, total=%d",
                len(self._connections),
            )

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ── ticket 鉴权 ──────────────────────────────────────────

    @staticmethod
    def ticket_key(token: str) -> str:
        return f"{_TICKET_KEY_PREFIX}{token}"

    @staticmethod
    def presence_key(tenant_id: str, agent_id: str) -> str:
        # 租户校验允许 ':'；对两个组件分别编码，避免 tenant/agent 拼接碰撞。
        return (
            f"{_PRESENCE_KEY_PREFIX}{quote(tenant_id, safe='')}:"
            f"{quote(agent_id, safe='')}"
        )

    @staticmethod
    def _redis():
        return get_redis()

    def issue_ticket(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str | None:
        """写入 Redis 一次性 ticket；Redis 不可用时 fail-closed。"""
        # 旧单测/内部兼容调用没有身份参数；它仍写 Redis，不能用于新 WS
        # 路径（新路由总是传入真实的数据库绑定身份）。
        legacy_call = agent_id is None and tenant_id is None
        if legacy_call:
            agent_id = "legacy"
            tenant_id = "legacy"
        agent_id = str(agent_id or "").strip()
        tenant_id = str(tenant_id or "").strip()
        if not agent_id or not tenant_id:
            return None

        redis_client = self._redis()
        if redis_client is None:
            return None

        token = secrets.token_urlsafe(24)
        payload = json.dumps(
            {
                "agent_id": agent_id,
                "tenant_id": tenant_id,
                "issued_at": _now_iso(),
                "ttl": TICKET_TTL_SECONDS,
            },
            ensure_ascii=False,
        )
        try:
            stored = redis_client.setex(
                self.ticket_key(token), TICKET_TTL_SECONDS, payload
            )
        except Exception:
            logger.warning("[AgentHub] issue ws ticket failed", exc_info=True)
            return None
        if stored is False:
            logger.warning("[AgentHub] issue ws ticket was not stored")
            return None
        if legacy_call:
            self._tickets[token] = time.monotonic() + TICKET_TTL_SECONDS
        return token

    def redeem_ticket_claims(self, token: str) -> dict[str, str] | None:
        """用 Redis GETDEL/Lua 原子核销并返回服务端绑定身份。"""
        token = str(token or "").strip()
        if not token:
            return None
        redis_client = self._redis()
        if redis_client is None:
            return None
        try:
            if hasattr(redis_client, "getdel"):
                raw = redis_client.getdel(self.ticket_key(token))
            else:
                raw = redis_client.eval(_GETDEL_LUA, 1, self.ticket_key(token))
        except Exception:
            logger.warning("[AgentHub] redeem ws ticket failed", exc_info=True)
            return None
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            claims = json.loads(raw)
        except (TypeError, ValueError):
            return None
        agent_id = str(claims.get("agent_id") or "").strip()
        tenant_id = str(claims.get("tenant_id") or "").strip()
        if not agent_id or not tenant_id:
            return None
        return {"agent_id": agent_id, "tenant_id": tenant_id}

    def redeem_ticket(self, token: str) -> bool:
        """兼容旧调用方的布尔核销接口；新 WS 路径使用 claims 接口。"""
        legacy_expiry = self._tickets.pop(token, None)
        claims = self.redeem_ticket_claims(token)
        if legacy_expiry is not None and legacy_expiry <= time.monotonic():
            return False
        return claims is not None

    def refresh_presence(self, *, agent_id: str, tenant_id: str) -> bool:
        """刷新 Redis presence TTL；不把在线状态写入 PostgreSQL。"""
        agent_id = str(agent_id or "").strip()
        tenant_id = str(tenant_id or "").strip()
        if not agent_id or not tenant_id:
            return False
        redis_client = self._redis()
        if redis_client is None:
            return False
        try:
            stored = redis_client.setex(
                self.presence_key(tenant_id, agent_id),
                PRESENCE_TTL_SECONDS,
                "1",
            )
        except Exception:
            logger.warning("[AgentHub] refresh presence failed", exc_info=True)
            return False
        return stored is not False

    # ── publish ──────────────────────────────────────────────

    def publish(
        self, event_type: str, *, persist: bool = True, **payload
    ) -> None:
        """线程安全 fire-and-forget 广播；未绑 loop 时静默丢弃。

        P3.2 封套：event_id/ts 本地生成，seq 由 events 表分配（落库失败
        为 None）。广播统一经 Redis pub/sub（多 worker 广谱覆盖）；
        Redis 不可用降级为本进程直接广播。

        persist=False 为瞬态事件（typing 等）：只广播不落库，不占 seq。
        无坐席在线时事件仍落库（补发源完整性），仅跳过广播——见
        _persist_and_broadcast。
        """
        if self._main_loop is None:
            return
        envelope = {
            "type": event_type,
            "event_id": uuid.uuid4().hex,
            "seq": None,  # _persist_and_broadcast 内回填
            "ts": _now_iso(),
            **payload,
        }
        try:
            asyncio.run_coroutine_threadsafe(
                self._persist_and_broadcast(envelope, payload, persist),
                self._main_loop,
            )
        except Exception:
            logger.debug(
                "[AgentHub] publish %s failed", event_type, exc_info=True
            )

    async def _persist_and_broadcast(
        self, envelope: dict, payload: dict, persist: bool = True
    ) -> None:
        """落库拿 seq → 经 Redis 广播（不可用则本进程直接广播）。

        落库与广播解耦：无坐席在线时只落库（事件源完整性），不广播。
        """
        if persist:
            try:
                envelope["seq"] = await self._persist_event(payload, envelope)
            except Exception:
                await self._enqueue_event_outbox(envelope, payload)
                # 防御纵深：_persist_event 内部已全捕获，此处兜底任何意外，
                # 保证「落库崩」永不拖垮广播（事件尽力而为原则）
                envelope["seq"] = None
                logger.warning(
                    "[AgentHub] unexpected persist error (%s)",
                    envelope["type"],
                    exc_info=True,
                )
        if not self._connections:
            return
        data = json.dumps(envelope, ensure_ascii=False, default=str)
        if not await self._publish_via_redis(data):
            await self._broadcast(data)

    async def _enqueue_event_outbox(
        self, envelope: dict, payload: dict,
    ) -> None:
        """PG 持久化失败时写入 Redis Stream 补偿队列。"""
        try:
            from backend.customer_service.event_outbox import enqueue_event

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: enqueue_event(envelope=envelope, payload=payload),
            )
        except Exception:
            # 补偿通道自身不可用时仍不能阻断实时广播；日志必须保留故障证据。
            logger.error(
                "[AgentHub] event outbox enqueue failed (%s)",
                envelope.get("type"),
                exc_info=True,
            )

    async def _persist_event(self, payload: dict, envelope: dict) -> int | None:
        """事件落库（seq 分配）。失败抛给 outbox 层，不影响广播。

        本协程运行在主 uvicorn loop，直接 await AsyncSessionLocal——
        此前经 run_in_executor + run_sync(_db_loop) 绕了两道线程切换。
        """
        conversation_id = payload.get("conversation_id")
        if not conversation_id:
            return None
        try:
            from backend.customer_service.repository.event_repo import (
                EventRepository,
            )
            from backend.memory.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                return await EventRepository(db).append(
                    conversation_id=str(conversation_id),
                    event_id=envelope["event_id"],
                    type=envelope["type"],
                    payload=payload,
                )
        except Exception:
            logger.warning(
                "[AgentHub] event persist failed (%s), seq=null",
                envelope["type"],
                exc_info=True,
            )
            # 让 _persist_and_broadcast 进入 outbox 补偿分支；实时广播
            # 仍由外层 catch 保证继续执行。
            raise

    @staticmethod
    async def _publish_via_redis(data: str) -> bool:
        """sync redis publish 丢线程池；成功 True，不可用/失败 False（降级）。"""
        try:
            from backend.infra.redis.client import get_redis

            r = get_redis()
            if r is None:
                return False
            loop = asyncio.get_running_loop()
            receivers = await loop.run_in_executor(
                None, lambda: r.publish(REDIS_CHANNEL, data)
            )
            if receivers <= 0:
                # 无订阅者（单机且无订阅线程）→ 本进程兜底广播，
                # 防止事件因 Redis 存在却无人消费而丢失
                return False
            return True
        except Exception:
            return False

    # ── Redis 订阅（专用线程） ───────────────────────────────

    def _ensure_subscriber(self) -> None:
        """拉起 Redis 订阅 daemon 线程（幂等）；Redis 不可用时线程内探测。"""
        if self._subscriber_thread is not None and self._subscriber_thread.is_alive():
            return

        def _run():
            from redis.exceptions import TimeoutError as RedisTimeoutError

            from backend.infra.redis.client import get_redis

            while True:
                r = get_redis()
                if r is None:
                    time.sleep(5.0)  # cooldown 内不重试，探测周期 5s
                    continue
                try:
                    pubsub = r.pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(REDIS_CHANNEL)
                    logger.info("[AgentHub] redis subscriber ready (%s)", REDIS_CHANNEL)
                    while True:
                        try:
                            for msg in pubsub.listen():
                                data = msg.get("data")
                                if not data or self._main_loop is None:
                                    continue
                                # redis-py 返回 bytes；广播收 str（send_text 契约），
                                # 原文直传避免 dict→str→dict→str 二次序列化
                                if isinstance(data, bytes):
                                    data = data.decode("utf-8", "replace")
                                try:
                                    envelope = json.loads(data)
                                except Exception:
                                    continue
                                if not envelope:
                                    continue
                                asyncio.run_coroutine_threadsafe(
                                    self._broadcast(data), self._main_loop
                                )
                        except RedisTimeoutError:
                            # 共享客户端带 socket_timeout：空闲无消息时 listen
                            # 抛超时——连接仍活着，同 pubsub 继续听即可。
                            # 此前当作连接错误断开重连（P3.5 实测 40min 断连
                            # 200+ 次），重连窗口内发布的事件会丢。
                            continue
                except Exception:
                    logger.warning(
                        "[AgentHub] redis subscriber error, retry in 2s",
                        exc_info=True,
                    )
                    time.sleep(2.0)

        self._subscriber_thread = threading.Thread(
            target=_run, name="cs-redis-subscriber", daemon=True
        )
        self._subscriber_thread.start()

    @staticmethod
    async def publish_envelope(envelope: dict) -> bool:
        """同步把**已构造好的封套**投递到 Redis 频道（P8 outbox relay 专用）。

        与 ``publish()`` 的差别是回执语义：

        - ``publish()`` 是 fire-and-forget，调用方拿不到"到底投出去没有"，
          因此只能用于"尽力而为"的快速路径；
        - ``publish_envelope()`` 等 ``PUBLISH`` 返回**订阅者数**，
          ``receivers == 0``（例如 API 实例全挂、无人订阅）返回 ``False``，
          调用方据此把事件留在 outbox 里下一轮重投 —— 这是方案 §六 P8
          「杀死 dispatcher 后事件不丢」的实现基础。

        刻意不改 ``_persist_and_broadcast`` 的「无本地连接就早退」行为：
        那个早退对 API 进程是合理的优化，但对 relay 是致命的（dispatcher
        容器永远没有本地 WS 连接，早退会让定向事件永远发不出去）。
        """
        return await AgentHub._publish_via_redis(  # noqa: SLF001 - 同类内部复用
            json.dumps(envelope, ensure_ascii=False, default=str)
        )

    async def _broadcast(self, data: str) -> None:
        """并发广播已序列化的帧；死连接统一清理。

        逐个 await 会把慢客户端的 TCP 背压串行放大到所有坐席，gather
        并发后单慢连接只影响自己。入参收 str：调用方（含 Redis 订阅
        路径）序列化一次即可，避免 dict→str→dict→str 往返。
        """
        try:
            envelope = json.loads(data)
        except (TypeError, ValueError):
            return

        target_agent_id = str(envelope.get("target_agent_id") or "").strip()
        target_tenant_id = str(envelope.get("tenant_id") or "").strip()
        conns = list(self._connections)
        if target_agent_id:
            if not target_tenant_id:
                return
            conns = [
                ws for ws in conns
                if self._connection_identity.get(ws)
                == (target_tenant_id, target_agent_id)
            ]
        if not conns:
            return
        results = await asyncio.gather(
            *(ws.send_text(data) for ws in conns), return_exceptions=True
        )
        for ws, result in zip(conns, results):
            if isinstance(result, BaseException):
                self.disconnect(ws)


# ── 断线补发（P3.2） ─────────────────────────────────────

def replay_events(conversation_id: str, after_seq: int, limit: int = 200) -> list[dict]:
    """sync 上下文可调用：按 seq 升序回放事件（补发端点与 WS 重连用）。"""
    from backend.customer_service._db_loop import run_sync
    from backend.customer_service.repository.event_repo import EventRepository
    from backend.memory.database import AsyncSessionLocal

    async def _replay():
        async with AsyncSessionLocal() as db:
            return await EventRepository(db).replay(
                conversation_id, after_seq, limit
            )

    return run_sync(_replay())


_hub_instance = AgentHub()


def get_agent_hub() -> AgentHub:
    return _hub_instance
