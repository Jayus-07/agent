"""RAG Upload 进度持久化、Redis轮询与所有者鉴权。"""
from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.app.api.identity import require_principal
from backend.app.api.routes.rag_upload_subroutes.base import *

def _write_progress_redis(upload_id: str, stage: str, message: str = "",
                          owner: tuple[str, str] | None = None, **extra) -> None:
    """Phase 5: 将上传进度镜像写入 Redis Hash（跨实例可查）。失败不影响主流程。

    终态一次写（2026-10-01 B 阶段契约）：镜像已落终态（done/error/duplicate）
    后拒绝任何中间态回写——幂等重放/迟到的 uploading 事件不能把终态改回
    uploading（SSE 订阅者据此保证终态不倒退）。
    """
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return
        from backend.config.redis import REDIS_KEY_PREFIX
        key = f"{REDIS_KEY_PREFIX}upload:{upload_id}"
        import json as _json
        if stage not in facade._SSE_TERMINAL_STAGES:
            current = r.hget(key, "stage")
            if isinstance(current, bytes):
                current = current.decode("utf-8", "replace")
            if current in facade._SSE_TERMINAL_STAGES:
                logger.debug(
                    f"[RAG] 进度镜像已终态({current})，拒绝中间态回写: {upload_id}::{stage}")
                return
        mapping = {
            "stage": stage,
            "message": message,
            "updated_at": str(time.time()),
            "detail": _json.dumps(extra, ensure_ascii=False, default=str) if extra else "{}",
        }
        if owner:
            mapping["owner_tenant"] = owner[0]
            mapping["owner_actor"] = owner[1]
        r.hset(key, mapping=mapping)
        # TTL 与 SSE 轮询上限同源（batch8 缺陷 B）：旧 600s 在长阶段静默
        # （49MB 文本解析/500 页 OCR 单阶段可超 10 分钟无帧更新）时键提前
        # 过期，轮询通道 missing 10s 即误报「任务不存在」，而任务实际在跑。
        # 对齐 RAG_SSE_REDIS_POLL_MAX_SECONDS 后，键存活期 ≥ 后端轮询上限，
        # 超时语义由轮询 deadline 统一收口。config 常量是 float（1900.0），
        # Redis EXPIRE 只收整数——float 直传会被 Redis 拒绝并静默丢 TTL
        # （键永不过期），必须 int() 收窄。
        r.expire(key, int(RAG_SSE_REDIS_POLL_MAX_SECONDS))
    except Exception as e:
        # 受理帧/进度帧写失败是用户可见问题的根因（镜像 owner 随帧丢失、
        # 键缺失导致 SSE 误报），warning 级观测——Redis 瞬态恢复后无补救
        # 路径，必须留痕排查
        logger.warning(f"[RAG] 进度镜像写入 Redis 失败: {e}")


def _read_progress_redis(upload_id: str) -> tuple[str, dict | None]:
    """读 Redis 进度镜像并还原为事件 dict（{stage, message, **extra}）。

    返回 (status, evt) 三态（batch3 复审 P2-2：旧实现把「Redis 连接异常」
    与「键不存在」折叠为同一个 None，SSE 轮询无法区分任务真过期和
    Redis 抖动，抖动 >10s 即误报「不存在或已过期」）：
      - ("ok", evt)      镜像存在且解析成功；
      - ("missing", None) Redis 正常但键不存在（任务真不存在 / TTL 已过期）；
      - ("error", None)   Redis 连接/操作异常（不代表任务不存在）。
    """
    import json as _json
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return ("error", None)
        from backend.config.redis import REDIS_KEY_PREFIX
        data = r.hgetall(f"{REDIS_KEY_PREFIX}upload:{upload_id}")
        if not data:
            return ("missing", None)
        evt: dict = {"stage": data.get("stage", ""),
                     "message": data.get("message", "")}
        try:
            detail = _json.loads(data.get("detail") or "{}")
            if isinstance(detail, dict):
                evt.update(detail)
        except Exception:
            pass
        return ("ok", evt)
    except Exception:
        return ("error", None)


def _run_still_active(upload_id: str) -> bool:
    """run_store 权威查询：镜像键缺失时判断任务是否仍在途（batch8 缺陷 B 兜底）。

    镜像键消失（Redis 丢键/极端过期）≠ 任务不存在——run_store 才是权威
    终局：查到非终态运行（claimed/indexing/publishing）则任务活着，不得
    误报「不存在或已过期」。终态集合以 index_run_store_pg.TERMINAL_STATUSES
    为单一事实源。查询异常（PG 抖动）按「仍在途」宽大处理：误宽限的代价
    只是多轮询一轮，误判过期是用户可见失败。
    """
    try:
        from backend.rag.indexing.index_run_store_pg import (
            TERMINAL_STATUSES, get_index_run_store,
        )
        run = get_index_run_store().get_run(upload_id) or {}
        if not run:
            return False  # 运行记录都不存在 = 真过期
        return str(run.get("status") or "") not in TERMINAL_STATUSES
    except Exception:
        return True


async def _redis_poll_events(upload_id: str, last_sig: str | None = None):
    """Celery 模式 SSE 事件源：轮询 Redis 进度镜像直到终态（async 生成器）。

    last_sig: 切换通道前已推送的最后一条事件签名，避免跨通道重复推送。
    事件格式与队列模式完全一致（facade._sse_encode("message", evt)），前端零感知。

    轮询三态（batch3 P2-2）：("ok", evt) 正常推进；("missing", None) 计入
    empty grace（键真不存在，按秒配置判过期——grace 耗尽后先查 run_store 兜底，
    任务仍在途则重置宽限续看，batch8 缺陷 B）；("error", None) 是 Redis 连接
    故障，不计入过期证据，按秒配置宽限自愈，期间低频 keepalive 保活。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + facade._SSE_REDIS_POLL_MAX_SECONDS
    empty_since: float | None = None
    error_since: float | None = None
    last_keepalive_at = loop.time()
    while True:
        if loop.time() >= deadline:
            yield facade._sse_encode("error", {"message": "索引进度轮询超时（任务可能仍在后台执行）"})
            return
        status, evt = await asyncio.to_thread(facade._read_progress_redis, upload_id)
        now = loop.time()
        if status == "error":
            if error_since is None:
                error_since = now
            if now - error_since >= facade._SSE_REDIS_ERROR_GRACE_SECONDS:
                yield facade._sse_encode("error", {"message": "索引进度服务暂时不可用，请稍后刷新确认任务状态"})
                return
            if now - last_keepalive_at >= facade._SSE_REDIS_KEEPALIVE_SECONDS:
                last_keepalive_at = now
                yield ": keepalive\n\n"
            await asyncio.sleep(facade._SSE_REDIS_POLL_SECONDS)
            continue
        error_since = None
        if status == "missing":
            if empty_since is None:
                empty_since = now
            if now - empty_since >= facade._SSE_REDIS_EMPTY_GRACE_SECONDS:
                # batch8 缺陷 B 兜底：键消失（Redis 丢键/极端过期）但任务仍在
                # 途时，run_store 是权威终局——查到非终态运行则不计过期证据，
                # 重置宽限继续轮询（每个 grace 周期只查一次 DB，频次有界）。
                if await asyncio.to_thread(facade._run_still_active, upload_id):
                    empty_since = loop.time()
                    now = empty_since
                    if now - last_keepalive_at >= facade._SSE_REDIS_KEEPALIVE_SECONDS:
                        last_keepalive_at = now
                        yield ": keepalive\n\n"
                    await asyncio.sleep(facade._SSE_REDIS_POLL_SECONDS)
                    continue
                yield facade._sse_encode("error", {"message": "上传任务不存在或已过期"})
                return
            await asyncio.sleep(facade._SSE_REDIS_POLL_SECONDS)
            continue
        empty_since = None
        import json as _json
        sig = _json.dumps(evt, sort_keys=True, ensure_ascii=False, default=str)
        if sig != last_sig:
            last_sig = sig
            last_keepalive_at = now
            yield facade._sse_encode("message", evt)
            if evt.get("stage") in facade._SSE_TERMINAL_STAGES:
                return
        elif now - last_keepalive_at >= facade._SSE_REDIS_KEEPALIVE_SECONDS:
            last_keepalive_at = now
            yield ": keepalive\n\n"
        await asyncio.sleep(facade._SSE_REDIS_POLL_SECONDS)


def _redis_poll_stream_response(upload_id: str, last_sig: str | None = None):
    """Celery 模式 SSE：包装 _redis_poll_events 为 StreamingResponse。"""
    async def event_stream():
        async for chunk in facade._redis_poll_events(upload_id, last_sig=last_sig):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _progress_owner_of(upload_id: str) -> tuple[str, str]:
    """upload_id → 上传者 (tenant_id, actor_id)：进程内注册表优先，
    跨实例（本进程没登记）回退读 Redis 镜像里的 owner 字段。"""
    owner = facade._progress_owners.get(upload_id)
    if owner:
        return owner
    import json as _json
    try:
        from backend.infra.redis.client import get_redis
        r = get_redis()
        if r is None:
            return ("", "")
        from backend.config.redis import REDIS_KEY_PREFIX
        data = r.hgetall(f"{REDIS_KEY_PREFIX}upload:{upload_id}") or {}
        tenant = data.get("owner_tenant") or ""
        actor = data.get("owner_actor") or ""
        if isinstance(tenant, bytes):
            tenant = tenant.decode("utf-8", "replace")
        if isinstance(actor, bytes):
            actor = actor.decode("utf-8", "replace")
        return (tenant, actor)
    except Exception:
        return ("", "")


def _ensure_progress_owner(request: Request, upload_id: str) -> bool:
    """SSE 订阅归属校验（2026-10-01 权限收口）：只允许上传者本人或 admin。

    upload_id 此前可被推断/猜测（确定性 sha256），无归属校验 = 任何已认证
    用户可窃听他人上传进度与终态 doc 元数据。校验失败返回 False，调用方
    以与「任务不存在」完全相同的响应拒绝（不泄露存在性）。
    """
    principal = require_principal(request)
    tenant, actor = facade._progress_owner_of(upload_id)
    if not tenant and not actor:
        return False  # 无归属记录（过期/不可信请求）→ 一律拒绝
    if not tenant or principal.tenant_id != tenant:
        return False
    if tenant and principal.tenant_id == tenant and principal.user_id == actor:
        return True
    try:
        return facade.RagAuthorization.build(principal).is_admin
    except facade.RagAuthorizationError:
        return False


for _fn in [
    _write_progress_redis,
    _read_progress_redis,
    _run_still_active,
    _redis_poll_events,
    _redis_poll_stream_response,
    _progress_owner_of,
    _ensure_progress_owner
]:
    facade.register(_fn.__name__, _fn)
