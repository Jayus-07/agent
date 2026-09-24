"""travel/booking/webhook.py — Webhook Inbox（STOP L6，G27/G28/G29）

纪律（任务书 §二十七~§三十）：
  收到 webhook 先落 Inbox（不直接改单）；签名/时间戳重放窗口/provider
  白名单/payload 大小/content-type 全部校验；同 (provider,event_id) 重复
  投递幂等；找不到订单 → quarantine，绝不 attach 最近订单。
  secret 只来自 env；禁日志/trace/响应。

应用规则（状态机白名单 + 状态版本单调）：
  booking.confirmed → SUBMITTING→BOOKED（W5 webhook 早于 HTTP 收尾）
  booking.failed    → SUBMITTING→FAILED
  其余/迟到事件     → 幂等忽略（终态不可倒退，B17）
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from backend.shared.logger import logger
from backend.travel.booking.state import BookingOrderStatus
from backend.travel.booking.store import (
    BookingStore,
    StaleTransition,
)
from backend.travel.booking.telemetry import record_webhook

# 校验参数（§二十八）
_REPLAY_WINDOW_SECONDS = 300
_MAX_PAYLOAD_BYTES = 64 * 1024
_ALLOWED_CONTENT_TYPES = ("application/json",)


class WebhookRejected(ValueError):
    """校验失败（签名/时间戳/大小/白名单）——拒绝且订单不变（B18）。"""


def verify_signature(*, payload_bytes: bytes, signature: str, secret: str,
                     timestamp: str) -> None:
    """HMAC-SHA256(secret, f"{timestamp}.{payload}") + 重放窗口。"""
    if not secret:
        raise WebhookRejected("webhook secret 未配置")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise WebhookRejected("timestamp 非法") from None
    if abs(time.time() - ts) > _REPLAY_WINDOW_SECONDS:
        raise WebhookRejected("timestamp 超出重放窗口")
    if not signature:
        raise WebhookRejected("signature 缺失")
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload_bytes,
        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip().lower()):
        raise WebhookRejected("signature 不匹配")


def check_content_type(content_type: str) -> None:
    base = (content_type or "").split(";")[0].strip().lower()
    if base not in _ALLOWED_CONTENT_TYPES:
        raise WebhookRejected(f"content-type 不支持: {base or 'empty'}")


def check_payload_size(payload_bytes: bytes) -> None:
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        raise WebhookRejected("payload 超限")


def ingest_webhook(*, provider: str, payload_bytes: bytes, signature: str,
                   timestamp: str, content_type: str,
                   store: BookingStore | None = None,
                   secret: str | None = None) -> dict:
    """HTTP webhook 接收入口：校验 → Inbox 去重落库 → 应用到订单。"""
    store = store or BookingStore()
    provider = (provider or "").strip().lower()
    if provider not in _allowed_providers():
        record_webhook(provider or "unknown", "rejected")
        raise WebhookRejected("provider 不在白名单")
    check_content_type(content_type)
    check_payload_size(payload_bytes)
    if secret is None:
        secret = _provider_secret(provider)
    verify_signature(payload_bytes=payload_bytes, signature=signature,
                     secret=secret, timestamp=timestamp)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise WebhookRejected("payload 非合法 JSON") from None

    event_id = str(payload.get("event_id") or "")
    if not event_id:
        record_webhook(provider, "rejected")
        raise WebhookRejected("event_id 缺失")

    payload_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:40]
    inserted = store.inbox_insert(
        provider=provider, event_id=event_id,
        external_order_id=payload.get("provider_order_id"),
        event_type=str(payload.get("event_type") or "unknown"),
        payload=payload, payload_hash=payload_hash)
    if not inserted:
        # B16：重复投递 → 幂等去重（终态不变，零增量副作用）
        record_webhook(provider, "duplicate")
        return {"accepted": True, "duplicate": True, "applied": False}

    order = store.get_order_by_merchant_ref(
        str(payload.get("merchant_order_id") or ""))
    applied = _apply(store, provider, event_id, order, payload)
    record_webhook(provider, "accepted" if applied is not None
                   else "quarantined")
    return {"accepted": True, "duplicate": False, "applied": applied}


def apply_inbox_event(*, store: BookingStore, provider: str, event_id: str,
                      payload: dict) -> dict | None:
    """对已入箱事件执行应用（与 ingest 共用；测试/补偿通道可独立调用）。"""
    order = store.get_order_by_merchant_ref(
        str(payload.get("merchant_order_id") or ""))
    return _apply(store, provider, event_id, order, payload)


def _apply(store: BookingStore, provider: str, event_id: str,
           order: dict | None, payload: dict) -> dict | None:
    event_type = str(payload.get("event_type") or "")
    if order is None:
        # §三十：找不到订单 → quarantine（绝不 attach 最近订单）
        store.inbox_mark(provider=provider, event_id=event_id,
                         status="quarantined", reason="unknown_order")
        logger.warning("[BookingWebhook] event=%s 无匹配订单，隔离", event_id)
        return None

    incoming = str(payload.get("status") or "")
    target_map = {
        "confirmed": BookingOrderStatus.BOOKED,
        "failed": BookingOrderStatus.FAILED,
    }
    target = target_map.get(incoming)
    if target is None:
        store.inbox_mark(provider=provider, event_id=event_id,
                         status="ignored", reason="unmapped_status")
        return order
    try:
        # W5：webhook 早于 HTTP 收尾——SUBMITTING→BOOKED 合法；
        # B17：迟到事件（终态）→ CAS 不命中 → 幂等忽略（不倒退）
        order = store.transition_order(
            order_id=order["order_id"],
            expected_status=order["status"], target=target.value,
            cause="webhook", actor=f"webhook:{provider}",
            event_type="webhook_applied",
            provider_order_id=str(payload.get("provider_order_id") or "")
            or None)
        store.inbox_mark(provider=provider, event_id=event_id,
                         status="processed")
        return order
    except StaleTransition:
        # 状态已前进：webhook 幂等忽略（终态不倒退，G22）
        store.inbox_mark(provider=provider, event_id=event_id,
                         status="ignored", reason="stale_state")
        return store.get_order(order["order_id"])


def _allowed_providers() -> tuple[str, ...]:
    """provider allowlist（§二十八）：登记过 webhook 能力的 booking provider。"""
    return ("fake_booking_native",)


def _provider_secret(provider: str) -> str:
    import os

    return os.environ.get(
        f"TRAVEL_BOOKING_WEBHOOK_SECRET_{provider.upper()}", "")
