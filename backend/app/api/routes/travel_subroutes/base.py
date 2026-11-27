"""Travel API 共享基础设施、异常类、租户与决策辅助、动态门面代理。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Request

from backend.app.api.identity import require_identity
from backend.shared.logger import logger
from backend.travel.models.brief import TravelBrief


class FacadeProxy:
    """透明门面代理：
    优先从门面模块 (backend.app.api.routes.travel) 解析被 monkeypatch 替换的属性，
    如果未被 patch 或门面未就绪，则回退到本地默认实现。
    """
    def __init__(self, facade_module: str) -> None:
        self._facade_module = facade_module
        self._defaults: dict[str, Any] = {}

    def register(self, name: str, default_val: Any) -> None:
        self._defaults[name] = default_val

    def __getattr__(self, name: str) -> Any:
        mod = sys.modules.get(self._facade_module)
        default_val = self._defaults.get(name)
        if mod is not None and hasattr(mod, name):
            val = getattr(mod, name)
            if default_val is None or val is not default_val:
                return val
        if default_val is not None:
            return default_val
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
        raise AttributeError(f"'{self._facade_module}' has no attribute '{name}'")


facade = FacadeProxy("backend.app.api.routes.travel")
facade.register("require_identity", require_identity)
facade.register("logger", logger)

def _travel_tenant(request: Request, identity) -> str:
    """租户 fail-closed（audit P3 全域收口）。

    header/strict 源只认网关注入的 ``identity.tenant_id``；legacy body 源读
    ``X-Tenant-Id`` 信任头；两者皆空即 403，绝不静默回落 ``'default'``
    （对齐 cs_admin._resolve_tenant_scope / plan_revisions._tenant 口径）。
    """
    if getattr(identity, "source", "") == "body":
        tenant = request.headers.get("X-Tenant-Id", "")
    else:
        tenant = getattr(identity, "tenant_id", "")
    if not tenant:
        raise HTTPException(status_code=403, detail="缺少可信租户身份")
    return tenant


def _masked_trace_question(message: str) -> str:
    """trace 落库前掩码（验收 #124）：trace_summary.question 是明文账
    （输入手机号即可回读），复用客服 C11 的规则掩码（shared/pii_mask）。
    只脱观测账，不改执行输入——规划链路拿到的仍是原文。掩码失败原样
    落库（观测旁路不阻塞主链）。"""
    try:
        from backend.shared.pii_mask import mask_pii

        return mask_pii(message or "")[0]
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        return message or ""


def _annotate_trace_source(trace, client_run_id: str, source: str) -> None:
    """把前端轮次与来源写进 trace.tags（仅非空时写，避免空 tag 噪音）。

    在 trace 创建后立即调用（而非收口时）——即便执行中途崩溃，
    trace 里也已带上 client_run_id/source，审计口径「当时哪轮、从哪来」
    对失败轮次同样成立。
    """
    try:
        # 来源分类在建 trace 时就落定：中途崩溃/超时的轮次同样要能归到
        # 旅游域，不能只依赖收口路径（收口失败即永久误判为 AI 助手）。
        trace.tags["runtime_domain"] = "travel"
        if client_run_id:
            trace.tags["client_run_id"] = str(client_run_id)[:64]
        if source:
            trace.tags["travel_source"] = str(source)[:32]
    except Exception:  # noqa: BLE001 — 观测旁路软失败
        logger.debug("[TravelAPI] trace source 标注失败", exc_info=True)


def _plan_error(message: str, status: str = "failed") -> dict:
    return {"status": status, "final_answer": message, "itinerary": None}


class _TravelResumeInProgressError(RuntimeError):
    """同一 checkpoint 的人工恢复已经由另一个请求处理。"""


class _TravelResumeLockUnavailableError(RuntimeError):
    """无法确认共享 checkpoint 的恢复互斥状态。"""


class _TravelCheckpointUnavailableError(RuntimeError):
    """持久化状态读取失败时 fail-closed，避免把新输入写进暂停图。"""



def _decision_payload_from_interrupts(interrupts: Any) -> dict | None:
    for item in interrupts or ():
        value = getattr(item, "value", None)
        if isinstance(item, dict):
            value = item.get("value", value)
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            if value["items"]:
                return value
    return None


def _pending_travel_decision(graph, config: dict) -> tuple[dict | None, Any]:
    """读取当前身份对应的 checkpoint；数据库故障时不冒险启动新一轮。"""
    get_state = getattr(graph, "get_state", None)
    if not callable(get_state):
        return None, None
    try:
        snapshot = get_state(config)
    except Exception:
        from backend.travel.graph_builder import (
            PERSISTENCE_DISABLED,
            get_persistence_status,
        )

        try:
            if get_persistence_status() == PERSISTENCE_DISABLED:
                return None, None
        except Exception:
            pass
        raise _TravelCheckpointUnavailableError(
            "无法读取行程暂停状态；本轮没有继续执行。"
        ) from None

    payload = facade._decision_payload_from_interrupts(
        getattr(snapshot, "interrupts", None))
    if payload:
        return payload, snapshot
    for task in getattr(snapshot, "tasks", ()) or ():
        payload = facade._decision_payload_from_interrupts(
            getattr(task, "interrupts", None))
        if payload:
            return payload, snapshot
    values = getattr(snapshot, "values", None)
    if isinstance(values, dict):
        payload = facade._decision_payload_from_interrupts(values.get("__interrupt__"))
    return payload, snapshot


def _bind_travel_decision_token(
    payload: dict, snapshot: Any, thread_id: str,
) -> dict:
    """把用户确认绑定到这个具体 checkpoint 与待决 payload。"""
    snapshot_config = getattr(snapshot, "config", None)
    configurable = (
        snapshot_config.get("configurable")
        if isinstance(snapshot_config, dict) else None
    )
    checkpoint_id = (
        configurable.get("checkpoint_id")
        if isinstance(configurable, dict) else None
    )
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise _TravelCheckpointUnavailableError(
            "行程确认版本暂不可用；本轮没有继续执行。"
        )
    canonical_payload = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    material = f"{thread_id}\x00{checkpoint_id}\x00{canonical_payload}"
    token = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return {**payload, "decision_token": token}


_KEEP_TRAVEL_DECISIONS = frozenset({
    "保留", "保留此安排", "保留当前安排", "接受风险",
})
_REMOVE_TRAVEL_DECISION = re.compile(r"^(?:移除|删除)\s*[:：]\s*(.+)$")


def _has_explicit_travel_decision(req: TravelPlanRequest) -> bool:
    if isinstance(req.action_payload, dict) and any(
        key in req.action_payload for key in ("decision", "user_decision")
    ):
        return True
    message = (req.message or "").strip().rstrip("。.!！").strip()
    return (message in _KEEP_TRAVEL_DECISIONS
            or _REMOVE_TRAVEL_DECISION.fullmatch(message) is not None)


def _parse_travel_decision(
    req: TravelPlanRequest, payload: dict,
) -> dict | None:
    action_payload = req.action_payload if isinstance(req.action_payload, dict) else {}
    if action_payload.get("decision_token") != payload.get("decision_token"):
        return None
    allowed = {
        str(item.get("poi_name") or "").strip()
        for item in payload.get("items", [])
        if isinstance(item, dict)
    }
    allowed.discard("")
    decision = action_payload.get("decision", action_payload.get("user_decision"))
    if isinstance(decision, dict):
        if decision.get("action") == "keep":
            return {"action": "keep"}
        if decision.get("action") == "drop":
            return {"action": "drop"}
        drops = decision.get("drop")
        if isinstance(drops, (list, tuple)) and drops:
            names = [str(name).strip() for name in drops]
            if all(name in allowed for name in names):
                return {"drop": list(dict.fromkeys(names))}
        return None

    message = (req.message or "").strip().rstrip("。.!！").strip()
    if message in _KEEP_TRAVEL_DECISIONS:
        return {"action": "keep"}
    match = _REMOVE_TRAVEL_DECISION.fullmatch(message)
    if match is None:
        return None
    names = [name.strip() for name in re.split(r"[、，,]", match.group(1))]
    if names and all(name in allowed for name in names):
        return {"drop": list(dict.fromkeys(names))}
    return None


def _travel_decision_prompt(
    payload: dict, conversation_id: str, *, unrecognized_input: bool = False,
    stale_decision: bool = False,
) -> dict:
    lines = ["行程已暂停，以下事项需要你明确决定："]
    for item in payload.get("items", []):
        if isinstance(item, dict) and item.get("message"):
            lines.append(f"- {item['message']}")
    lines.append("请回复“保留”或“移除：地点名”。")
    if unrecognized_input:
        lines.append("本轮内容没有被当作确认，也没有修改正式行程。")
    if stale_decision:
        lines.append("这条确认对应的事项已更新；请核对当前事项后重新确认。")
    message = "\n".join(lines)
    return {
        "status": "needs_clarification",
        "result_kind": "clarification",
        "final_answer": message,
        "clarification": message,
        "itinerary": None,
        "travel_context": {
            "conversation_id": conversation_id,
            "pending_decision": payload,
        },
    }


def _stale_travel_decision(conversation_id: str) -> dict:
    return {
        "status": "answered",
        "result_kind": "answer",
        "final_answer": "当前没有待处理的行程确认；本条确认没有被重复应用。",
        "itinerary": None,
        "travel_context": {"conversation_id": conversation_id},
    }


@contextmanager
def _travel_resume_lock(thread_id: str) -> Iterator[None]:
    """用共享 Postgres advisory lock 防止多 worker 同时恢复同一暂停点。"""
    from backend.travel.graph_builder import get_persistence_status

    if get_persistence_status() != "healthy":
        # MemorySaver 只在本进程可见；入口 RequestExecutor 已按会话串行化。
        yield
        return

    from backend.config.database import MEMORY_DB_CONFIG

    try:
        import psycopg

        conn = psycopg.connect(**MEMORY_DB_CONFIG, autocommit=True)
    except Exception:
        raise _TravelResumeLockUnavailableError(
            "无法锁定行程恢复状态；本轮没有继续执行。"
        ) from None

    lock_key = f"travel-interrupt-resume:{thread_id}"
    acquired = False
    try:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (lock_key,),
                )
                acquired = bool(cur.fetchone()[0])
        except Exception:
            raise _TravelResumeLockUnavailableError(
                "无法锁定行程恢复状态；本轮没有继续执行。"
            ) from None
        if not acquired:
            raise _TravelResumeInProgressError(
                "该行程的确认正在处理中，请勿重复提交。"
            )
        yield
    finally:
        if acquired:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (lock_key,),
                    )
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def _travel_destination(state: dict | None, result: dict | None = None) -> str:
    """从旅游状态提取可审计的目的地标签，不依赖用户自报字段。"""
    state = state or {}
    brief = state.get("brief") or {}
    if isinstance(brief, dict) and brief.get("destination"):
        return str(brief["destination"])
    itinerary = (result or {}).get("itinerary") or {}
    if isinstance(itinerary, dict):
        return str(itinerary.get("destination") or "")
    return str(getattr(itinerary, "destination", "") or "")


# 注册基础函数和异常
facade.register("_travel_tenant", _travel_tenant)
facade.register("_masked_trace_question", _masked_trace_question)
facade.register("_annotate_trace_source", _annotate_trace_source)
facade.register("_plan_error", _plan_error)
facade.register("_TravelResumeInProgressError", _TravelResumeInProgressError)
facade.register("_TravelResumeLockUnavailableError", _TravelResumeLockUnavailableError)
facade.register("_TravelCheckpointUnavailableError", _TravelCheckpointUnavailableError)
facade.register("_decision_payload_from_interrupts", _decision_payload_from_interrupts)
facade.register("_pending_travel_decision", _pending_travel_decision)
facade.register("_bind_travel_decision_token", _bind_travel_decision_token)
facade.register("_KEEP_TRAVEL_DECISIONS", _KEEP_TRAVEL_DECISIONS)
facade.register("_REMOVE_TRAVEL_DECISION", _REMOVE_TRAVEL_DECISION)
facade.register("_has_explicit_travel_decision", _has_explicit_travel_decision)
facade.register("_parse_travel_decision", _parse_travel_decision)
facade.register("_travel_decision_prompt", _travel_decision_prompt)
facade.register("_stale_travel_decision", _stale_travel_decision)
facade.register("_travel_resume_lock", _travel_resume_lock)
facade.register("_travel_destination", _travel_destination)
