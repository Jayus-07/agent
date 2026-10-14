from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from uuid import UUID, uuid4

from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.share_repository import ShareRepository
from backend.travel_v2.services.trip_service import IdempotencyConflict


class ShareNotFoundError(LookupError):
    """分享不存在、无效、已撤销、已过期或对应行程已归档。"""


class ShareTokenRecoveryError(RuntimeError):
    """分享密钥缺失或轮换后无法恢复同一幂等请求的原始令牌。"""


def _request_sha256(*, revision: int, expires_in_days: int) -> str:
    canonical = json.dumps(
        {"expires_in_days": expires_in_days, "revision": revision},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _derive_share_token(
    *, secret: bytes, tenant_id: str, owner_id: str, trip_id: str | UUID,
    idempotency_key_sha256: str, request_sha256: str,
) -> str:
    intent = json.dumps(
        {
            "tenant_id": tenant_id,
            "owner_id": owner_id,
            "trip_id": str(trip_id),
            "idempotency_key_sha256": idempotency_key_sha256,
            "request_sha256": request_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(secret, b"travel-v2-share-v1\0" + intent, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class ShareService:
    def __init__(
        self,
        *,
        connect=memory_connection,
        repository: ShareRepository | None = None,
    ):
        self._connect = connect
        self._shares = repository or ShareRepository()

    def create_share(
        self,
        *,
        trip_id: str | UUID,
        tenant_id: str,
        owner_id: str,
        revision: int,
        idempotency_key: str,
        expires_in_days: int = 30,
    ) -> dict:
        if not tenant_id or not owner_id:
            raise ValueError("tenant_id and owner_id are required")
        if revision < 1:
            raise ValueError("revision must be a positive integer")
        if not 1 <= expires_in_days <= 365:
            raise ValueError("expires_in_days must be between 1 and 365")
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        configured_secret = os.getenv("TRAVEL_V2_SHARE_TOKEN_SECRET", "")
        secret = configured_secret.encode("utf-8")
        if len(secret) < 32:
            raise ShareTokenRecoveryError("分享服务密钥未配置")
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        request_hash = _request_sha256(
            revision=revision,
            expires_in_days=expires_in_days,
        )
        token = _derive_share_token(
            secret=secret,
            tenant_id=tenant_id,
            owner_id=owner_id,
            trip_id=trip_id,
            idempotency_key_sha256=key_hash,
            request_sha256=request_hash,
        )
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                # 沿用现有事务级 advisory lock 模式；锁键不含请求内容，以便
                # 同一幂等键的并发不同 payload 能串行化并稳定返回 409。
                lock_material = json.dumps(
                    [tenant_id, owner_id, str(trip_id), key_hash],
                    separators=(",", ":"),
                ).encode("utf-8")
                lock_scope = "travel-v2-share:" + hashlib.sha256(
                    lock_material,
                ).hexdigest()
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (lock_scope,),
                )
                selected = self._shares.get_revision_for_owner(
                    cursor,
                    trip_id=trip_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    revision=revision,
                    for_update=True,
                )
                if not selected:
                    raise ShareNotFoundError("行程或指定 Revision 不存在")
                # 校验正式快照仍符合 V2 Schema；仅将 Revision 的不可变快照作为分享源。
                TripDocumentV2.model_validate(selected["snapshot"])
                existing = self._shares.get_by_idempotency_key(
                    cursor,
                    trip_id=trip_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    idempotency_key_sha256=key_hash,
                )
                if existing:
                    if existing["request_sha256"] != request_hash:
                        raise IdempotencyConflict(
                            "Idempotency-Key 已用于不同的分享请求",
                        )
                    if not hmac.compare_digest(existing["token_sha256"], token_hash):
                        # 密钥轮换后不返回会失效的新令牌；原链接仍按数据库哈希工作。
                        raise ShareTokenRecoveryError("分享令牌无法恢复")
                    return {**existing["share"], "token": token, "replayed": True}

                created = self._shares.insert(
                    cursor,
                    share_id=uuid4(),
                    trip_id=trip_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    revision=revision,
                    token_sha256=token_hash,
                    idempotency_key_sha256=key_hash,
                    request_sha256=request_hash,
                    expires_in_days=expires_in_days,
                )
        return {**created, "token": token, "replayed": False}

    def get_public_share(self, *, share_id: str | UUID, token: str) -> dict:
        if not token or len(token) > 128:
            raise ShareNotFoundError("分享不存在")
        try:
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError as exc:
            raise ShareNotFoundError("分享不存在") from exc
        with self._connect() as connection:
            with connection.cursor() as cursor:
                share = self._shares.get_public(
                    cursor,
                    share_id=share_id,
                    token_sha256=token_hash,
                )
        if not share:
            raise ShareNotFoundError("分享不存在")
        return share

    def revoke_share(
        self,
        *,
        share_id: str | UUID,
        trip_id: str | UUID,
        tenant_id: str,
        owner_id: str,
    ) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                revoked = self._shares.revoke_for_owner(
                    cursor,
                    share_id=share_id,
                    trip_id=trip_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                )
        if not revoked:
            raise ShareNotFoundError("分享不存在")


def project_public_share(share: dict) -> dict:
    """按显式白名单投影公开字段，屏蔽备注、ID、事实来源与内部状态。"""
    document = TripDocumentV2.model_validate(share["snapshot"]).model_dump(mode="json")
    brief = document["brief"]
    days = []
    for day in document["days"]:
        item_titles = {item["item_id"]: item["title"] for item in day["items"]}
        items = []
        for item in day["items"]:
            place = item.get("place")
            public_item = {
                "kind": item["kind"],
                "activity_type": item.get("activity_type"),
                "title": item["title"],
                "start_time": item.get("start_time"),
                "duration_min": item["duration_min"],
            }
            if place:
                public_item["place"] = {
                    "name": place["name"],
                    "address": place.get("address"),
                    "lat": place.get("lat"),
                    "lng": place.get("lng"),
                }
            items.append(public_item)
        legs = []
        for leg in day["legs"]:
            legs.append(
                {
                    "from_title": item_titles[leg["from_item_id"]],
                    "to_title": item_titles[leg["to_item_id"]],
                    "selected_mode": leg["selected_mode"],
                    "duration_min": leg.get("duration_min"),
                    "distance_m": leg.get("distance_m"),
                }
            )
        days.append(
            {
                "date": day.get("date"),
                "title": day["title"],
                "items": items,
                "legs": legs,
            }
        )

    arrangements = document.get("arrangements") or {}
    lodgings = []
    for lodging in arrangements.get("lodgings", []):
        merchant = lodging["merchant"]
        lodgings.append(
            {
                "name": merchant["name"],
                "address": merchant.get("address"),
                "check_in": lodging["check_in"],
                "check_out": lodging["check_out"],
                "booking_status": lodging["booking_status"],
            }
        )
    trains = []
    for train in arrangements.get("intercity_trains", []):
        trains.append(
            {
                "train_code": train["train_code"],
                "travel_date": train["travel_date"],
                "departure_station": train["departure_station"],
                "arrival_station": train["arrival_station"],
                "departure_time": train["departure_time"],
                "arrival_time": train["arrival_time"],
                "duration_min": train["duration_min"],
                "booking_status": train["booking_status"],
            }
        )
    return {
        "share_id": share["share_id"],
        "revision": share["revision"],
        "expires_at": share["expires_at"],
        "title": brief["destination"],
        "brief": {
            "destination": brief["destination"],
            "timezone": brief["timezone"],
            "start_date": brief.get("start_date"),
            "day_count": brief["day_count"],
        },
        "days": days,
        "arrangements": {"lodgings": lodgings, "intercity_trains": trains},
    }
