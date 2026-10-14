from __future__ import annotations

import hashlib
import secrets
from uuid import UUID, uuid4

from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.share_repository import ShareRepository


class ShareNotFoundError(LookupError):
    """分享不存在、无效、已撤销、已过期或对应行程已归档。"""


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
        expires_in_days: int = 30,
    ) -> dict:
        if not tenant_id or not owner_id:
            raise ValueError("tenant_id and owner_id are required")
        if revision < 1:
            raise ValueError("revision must be a positive integer")
        if not 1 <= expires_in_days <= 365:
            raise ValueError("expires_in_days must be between 1 and 365")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        share_id = uuid4()
        with self._connect() as connection:
            with connection.cursor() as cursor:
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
                created = self._shares.insert(
                    cursor,
                    share_id=share_id,
                    trip_id=trip_id,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    revision=revision,
                    token_sha256=token_hash,
                    expires_in_days=expires_in_days,
                )
        return {**created, "token": token}

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
