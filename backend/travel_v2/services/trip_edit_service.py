from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import date, time, timedelta
from uuid import UUID, uuid4

from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.commands import (
    AddMealSelectionCommand,
    StructuredTripEditCommand,
    SelectIntercityTrainCommand,
    SelectLodgingCommand,
)
from backend.travel_v2.models.trip import TripDocumentV2
from backend.travel_v2.repositories.edit_operation_repository import EditOperationRepository
from backend.travel_v2.repositories.revision_repository import RevisionRepository
from backend.travel_v2.repositories.trip_repository import TripRepository
from backend.travel_v2.services.trip_service import (
    TripNotFound,
    canonical_request_hash,
)
from backend.travel_v2.services.search_service import stable_selection_id


class VersionConflict(RuntimeError):
    """客户端基准版本不是当前正式版本。"""


class IdempotencyConflict(ValueError):
    """同一幂等键对应不同的编辑内容。"""


class RevisionNotFound(LookupError):
    """当前行程范围内没有该历史版本。"""


class TripEditService:
    def __init__(
        self, *, connect=memory_connection,
        trip_repository: TripRepository | None = None,
        revision_repository: RevisionRepository | None = None,
        edit_operation_repository: EditOperationRepository | None = None,
    ):
        self._connect = connect
        self._trips = trip_repository or TripRepository()
        self._revisions = revision_repository or RevisionRepository()
        self._operations = edit_operation_repository or EditOperationRepository()

    def add_meal(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, idempotency_key: str,
        command: AddMealSelectionCommand | dict,
    ) -> dict:
        payload = command if isinstance(command, AddMealSelectionCommand) else AddMealSelectionCommand.model_validate(command)
        if payload.merchant.source != "amap":
            raise ValueError("美食必须来自已接入的高德商户查询")
        self._assert_selection_id(
            payload.selection_id, "food", payload.merchant.source,
            payload.merchant.merchant_id,
        )
        current = self._get_trip_document(
            trip_id, tenant_id=tenant_id, owner_id=owner_id,
        )
        document = current["document"]
        days = document["days"]
        day = next((item for item in days if item["day_id"] == payload.day_id), None)
        if day is None:
            raise ValueError("所选日期不属于当前行程")
        period_label = "午餐" if payload.meal_period == "lunch" else "晚餐"
        already_added = any(
            item.get("activity_type") == "meal"
            and item.get("note") == period_label
            and (item.get("place") or {}).get("place_id") == payload.merchant.merchant_id
            for item in day["items"]
        )
        if not already_added:
            start_time = self._schedule_meal_time(
                day["items"], preferred="12:00" if payload.meal_period == "lunch" else "18:00",
            )
            item_digest = hashlib.sha256(
                f"{payload.day_id}\0{payload.meal_period}\0{payload.selection_id}".encode("utf-8")
            ).hexdigest()[:32]
            merchant = payload.merchant
            day["items"].append({
                "item_id": f"meal-{item_digest}",
                "kind": "activity", "activity_type": "meal",
                "title": merchant.name, "start_time": start_time,
                "duration_min": 60, "fixed_start": False,
                "place": {
                    "place_id": merchant.merchant_id, "name": merchant.name,
                    "lat": merchant.lat, "lng": merchant.lng,
                    "address": merchant.address,
                    "facts": {
                        "verification": "verified", "source": merchant.source,
                        "observed_at": merchant.observed_at.isoformat(),
                        "rating": merchant.rating, "open_status": merchant.open_status,
                        "open_time_today": merchant.open_time_today,
                        "avg_cost_cny": merchant.avg_cost_cny,
                    },
                },
                "must_visit": False, "locked": False, "note": period_label,
            })
        validated = TripDocumentV2.model_validate(document)
        return self.apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key, command_type="structured_edit",
            change_summary=f"添加{period_label}：{payload.merchant.name}",
            document=validated,
        )

    def select_lodging(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, idempotency_key: str,
        command: SelectLodgingCommand | dict,
    ) -> dict:
        payload = command if isinstance(command, SelectLodgingCommand) else SelectLodgingCommand.model_validate(command)
        lodging = payload.lodging
        if lodging.source != "amap" or lodging.merchant.source != "amap":
            raise ValueError("住宿商户必须来自已接入的高德查询")
        self._assert_selection_id(
            lodging.selection_id, "hotel", lodging.source,
            lodging.merchant.merchant_id,
        )
        current = self._get_trip_document(trip_id, tenant_id=tenant_id, owner_id=owner_id)
        document = current["document"]
        arrangements = document.setdefault("arrangements", {"lodgings": [], "intercity_trains": []})
        lodgings = arrangements.setdefault("lodgings", [])
        record = lodging.model_dump(mode="json")
        existing_index = next((
            index for index, item in enumerate(lodgings)
            if item.get("selection_id") == lodging.selection_id
        ), None)
        if existing_index is None:
            lodgings.append(record)
        else:
            lodgings[existing_index] = record
        validated = TripDocumentV2.model_validate(document)
        return self.apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key, command_type="structured_edit",
            change_summary=f"选择住宿：{lodging.merchant.name}（计划参考，未预订）",
            document=validated,
        )

    def select_intercity_train(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, idempotency_key: str,
        command: SelectIntercityTrainCommand | dict,
    ) -> dict:
        payload = command if isinstance(command, SelectIntercityTrainCommand) else SelectIntercityTrainCommand.model_validate(command)
        train = payload.train
        if train.ticket_source != "12306":
            raise ValueError("动车安排必须来自已接入的 12306 查询")
        identity = "|".join((
            train.travel_date.isoformat(), train.departure_station,
            train.arrival_station, train.train_code,
        ))
        self._assert_selection_id(train.selection_id, "train", "12306", identity)
        current = self._get_trip_document(trip_id, tenant_id=tenant_id, owner_id=owner_id)
        document = current["document"]
        arrangements = document.setdefault("arrangements", {"lodgings": [], "intercity_trains": []})
        trains = arrangements.setdefault("intercity_trains", [])
        record = train.model_dump(mode="json")
        existing_index = next((
            index for index, item in enumerate(trains)
            if item.get("selection_id") == train.selection_id
        ), None)
        if existing_index is None:
            trains.append(record)
        else:
            trains[existing_index] = record
        validated = TripDocumentV2.model_validate(document)
        return self.apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key, command_type="structured_edit",
            change_summary=f"添加城际交通：{train.train_code}（计划参考，未购票）",
            document=validated,
        )

    def _get_trip_document(self, trip_id: str | UUID, *, tenant_id: str, owner_id: str) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                trip = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id, owner_id=owner_id,
                )
        if not trip:
            raise TripNotFound("行程不存在")
        return trip

    @staticmethod
    def _assert_selection_id(selection_id: str, kind: str, source: str, identity: str) -> None:
        expected = stable_selection_id(kind, source, identity)
        if selection_id != expected:
            raise ValueError("查询结果选择编号无效或数据已变化，请重新查询")

    @staticmethod
    def _schedule_meal_time(items: list[dict], *, preferred: str) -> str:
        preferred_time = time.fromisoformat(preferred)
        start_min = preferred_time.hour * 60 + preferred_time.minute
        busy = []
        for item in items:
            if not item.get("start_time"):
                continue
            item_time = time.fromisoformat(item["start_time"])
            item_start = item_time.hour * 60 + item_time.minute
            item_end = item_start + int(item.get("duration_min") or 0)
            busy.append((item_start, item_end))
        for candidate in range(start_min, 22 * 60, 15):
            if all(candidate + 60 <= start or candidate >= end for start, end in busy):
                return f"{candidate // 60:02d}:{candidate % 60:02d}"
        raise ValueError("当天没有合适的餐饮时间，请先调整行程安排")
    def apply_document(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, expected_revision: int, idempotency_key: str,
        command_type: str, change_summary: str,
        document: TripDocumentV2 | dict,
    ) -> dict:
        if expected_revision < 1:
            raise ValueError("expected_revision must be >= 1")
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        if not command_type or len(command_type) > 48:
            raise ValueError("command_type must contain 1 to 48 characters")
        if len(tenant_id) > 128 or len(owner_id) > 128 or len(actor_id) > 128:
            raise ValueError("trip identity fields exceed 128 characters")
        snapshot = (
            document.model_dump(mode="json")
            if isinstance(document, TripDocumentV2)
            else TripDocumentV2.model_validate(document).model_dump(mode="json")
        )
        request_hash = canonical_request_hash({
            "trip_id": str(trip_id), "expected_revision": expected_revision,
            "command_type": command_type, "change_summary": change_summary,
            "document": snapshot,
        })

        with self._connect() as connection:
            with connection.cursor() as cursor:
                replay = self._replay_if_present(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay

                current = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, for_update=True,
                )
                if not current:
                    raise TripNotFound("行程不存在")
                # 并发同 key 请求会在 trip 行锁处等待；锁后重查以返回赢家结果。
                replay = self._replay_if_present(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
                if int(current["revision"]) != expected_revision:
                    raise VersionConflict("行程版本已变化，请刷新后重试")

                response = self._commit_locked(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, actor_id=actor_id,
                    expected_revision=expected_revision,
                    idempotency_key=key, request_hash=request_hash,
                    command_type=command_type,
                    change_summary=change_summary or command_type,
                    current=current, snapshot=snapshot,
                )
        return {**response, "replayed": False}

    def apply_operation(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, idempotency_key: str,
        command: StructuredTripEditCommand | dict,
    ) -> dict:
        """在行锁内应用一次结构化编辑，再以现有 CAS 写入新 Revision。"""
        payload = (
            command if isinstance(command, StructuredTripEditCommand)
            else StructuredTripEditCommand.model_validate(command)
        )
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        if len(tenant_id) > 128 or len(owner_id) > 128 or len(actor_id) > 128:
            raise ValueError("trip identity fields exceed 128 characters")
        operation = payload.operation.model_dump(mode="json", exclude_unset=True)
        command_type = "structured_edit"
        request_hash = canonical_request_hash({
            "trip_id": str(trip_id),
            "expected_revision": payload.expected_revision,
            "command_type": command_type,
            "change_summary": payload.change_summary,
            "operation": operation,
        })

        with self._connect() as connection:
            with connection.cursor() as cursor:
                replay = self._replay_if_present(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
                current = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, for_update=True,
                )
                if not current:
                    raise TripNotFound("行程不存在")
                replay = self._replay_if_present(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
                if int(current["revision"]) != payload.expected_revision:
                    raise VersionConflict("行程版本已变化，请刷新后重试")

                snapshot = self._apply_structured_operation(
                    current["document"], operation,
                )
                self._validate_edit_schedule(snapshot)
                response = self._commit_locked(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, actor_id=actor_id,
                    expected_revision=payload.expected_revision,
                    idempotency_key=key, request_hash=request_hash,
                    command_type=command_type,
                    change_summary=payload.change_summary,
                    current=current, snapshot=snapshot,
                )
        return {**response, "replayed": False}

    def _commit_locked(
        self, cursor, *, trip_id: str | UUID, tenant_id: str,
        owner_id: str, actor_id: str, expected_revision: int,
        idempotency_key: str, request_hash: str, command_type: str,
        change_summary: str, current: dict, snapshot: dict,
    ) -> dict:
        self._validate_protected_items(current["document"], snapshot)
        next_revision = expected_revision + 1
        updated = self._trips.update_document_cas(
            cursor, trip_id=trip_id, tenant_id=tenant_id,
            owner_id=owner_id, expected_revision=expected_revision,
            title=current["title"], document=snapshot,
        )
        if updated != 1:
            raise VersionConflict("行程版本已变化，请刷新后重试")

        self._revisions.insert(
            cursor, trip_id=trip_id, tenant_id=tenant_id,
            owner_id=owner_id, revision=next_revision,
            parent_revision=expected_revision, snapshot=snapshot,
            change_type=command_type,
            change_summary=change_summary or command_type,
            actor_id=actor_id,
        )
        response = {
            "saved": True, "trip_id": str(trip_id),
            "revision": next_revision, "document": snapshot,
            "status": "active",
        }
        self._operations.insert(
            cursor, operation_id=uuid4(), trip_id=trip_id,
            tenant_id=tenant_id, owner_id=owner_id,
            idempotency_key=idempotency_key, request_sha256=request_hash,
            command_type=command_type,
            base_revision=expected_revision,
            result_revision=next_revision, response=response,
        )
        return response

    @staticmethod
    def _apply_structured_operation(current_document: dict, operation: dict) -> dict:
        """纯结构变换；不访问 Provider、LLM 或存储。"""
        document = deepcopy(current_document)
        op = operation.get("op")

        def get_day(day_id: str) -> dict:
            day = next((item for item in document["days"] if item["day_id"] == day_id), None)
            if day is None:
                raise ValueError("所选日期不属于当前行程")
            return day

        def find_item(item_id: str) -> tuple[dict, dict, int]:
            for day in document["days"]:
                for index, item in enumerate(day["items"]):
                    if item["item_id"] == item_id:
                        return day, item, index
            raise ValueError("行程项不存在或已变化")

        def insertion_index(day: dict, position: int | None) -> int:
            index = len(day["items"]) if position is None else position
            if index > len(day["items"]):
                raise ValueError("插入位置超出当天安排范围")
            return index

        if op == "add_activity":
            day = get_day(operation["day_id"])
            day["items"].insert(insertion_index(day, operation.get("position")), {
                "item_id": f"item-{uuid4().hex}",
                "kind": "activity",
                "activity_type": operation["activity_type"],
                "title": operation["title"].strip(),
                "start_time": operation.get("start_time"),
                "duration_min": operation["duration_min"],
                "fixed_start": False,
                "place": None,
                "must_visit": False,
                "locked": False,
                "note": operation.get("note", ""),
            })
        elif op == "add_place":
            day = get_day(operation["day_id"])
            place = operation["place"]
            TripEditService._assert_place_snapshot(place)
            TripEditService._assert_selection_id(
                operation["selection_id"], "place", "tencent:lbs", place["place_id"],
            )
            place_id = place["place_id"]
            day["items"].insert(insertion_index(day, operation.get("position")), {
                "item_id": f"item-{uuid4().hex}", "kind": "place",
                "activity_type": None, "title": place["name"],
                "start_time": operation.get("start_time"),
                "duration_min": operation["duration_min"],
                "fixed_start": False, "place": place,
                "must_visit": False, "locked": False, "note": "",
            })
            TripEditService._upsert_selection(document, place_id, place["name"])
        elif op == "replace_place":
            _day, item, _index = find_item(operation["item_id"])
            if item.get("locked"):
                raise ValueError("locked item cannot be replaced")
            place = operation["place"]
            TripEditService._assert_place_snapshot(place)
            TripEditService._assert_selection_id(
                operation["selection_id"], "place", "tencent:lbs", place["place_id"],
            )
            item.update({
                "kind": "place", "activity_type": None,
                "title": place["name"], "place": place,
                "must_visit": False,
            })
            TripEditService._upsert_selection(document, place["place_id"], place["name"])
        elif op == "update_item":
            _day, item, _index = find_item(operation["item_id"])
            for field in ("start_time", "duration_min", "note"):
                if field in operation:
                    item[field] = operation[field]
        elif op == "remove_item":
            day, item, index = find_item(operation["item_id"])
            if item.get("locked"):
                raise ValueError(f"locked item cannot be removed: {item['item_id']}")
            if item.get("fixed_start"):
                raise ValueError(f"fixed item cannot be removed: {item['item_id']}")
            day["items"].pop(index)
        elif op == "move_item":
            source, item, index = find_item(operation["item_id"])
            target = get_day(operation["target_day_id"])
            if source["day_id"] == target["day_id"]:
                raise ValueError("移动目标与当前日期相同")
            if item.get("locked"):
                raise ValueError(f"locked item cannot be moved: {item['item_id']}")
            if item.get("fixed_start"):
                raise ValueError(f"fixed item cannot be moved: {item['item_id']}")
            source["items"].pop(index)
            target["items"].insert(insertion_index(target, operation.get("position")), item)
        elif op == "reorder_day":
            day = get_day(operation["day_id"])
            before = {item["item_id"]: item for item in day["items"]}
            requested = operation["item_ids"]
            if len(requested) != len(before) or set(requested) != set(before):
                raise ValueError("排序请求必须包含该日期的全部行程项且不能重复")
            day["items"] = [before[item_id] for item_id in requested]
        elif op == "add_day":
            count = len(document["days"])
            if count >= 60:
                raise ValueError("行程最多支持 60 天")
            document["days"].append({
                "day_id": f"day-{uuid4().hex}",
                "date": None,
                "title": operation.get("title") or f"第 {count + 1} 天",
                "items": [], "legs": [],
            })
            TripEditService._refresh_day_dates(document)
        elif op == "remove_day":
            if len(document["days"]) <= 1:
                raise ValueError("行程至少保留一天")
            day = get_day(operation["day_id"])
            if any(item.get("locked") or item.get("fixed_start") for item in day["items"]):
                raise ValueError("包含锁定或固定时间安排的日期不能删除")
            document["days"].remove(day)
            TripEditService._refresh_day_dates(document)
        elif op == "set_leg_mode":
            day = next((
                item for item in document["days"]
                if any(stop["item_id"] == operation["from_item_id"] for stop in item["items"])
                and any(stop["item_id"] == operation["to_item_id"] for stop in item["items"])
            ), None)
            if day is None:
                raise ValueError("交通路段端点不属于同一天")
            item_ids = [item["item_id"] for item in day["items"]]
            try:
                from_index = item_ids.index(operation["from_item_id"])
            except ValueError as exc:
                raise ValueError("交通路段起点不存在") from exc
            if from_index + 1 >= len(item_ids) or item_ids[from_index + 1] != operation["to_item_id"]:
                raise ValueError("交通方式只能设置在相邻地点之间")
            leg = next((
                value for value in day["legs"]
                if value["from_item_id"] == operation["from_item_id"]
                and value["to_item_id"] == operation["to_item_id"]
            ), None)
            if leg is None:
                leg = {
                    "leg_id": f"leg-{uuid4().hex}",
                    "from_item_id": operation["from_item_id"],
                    "to_item_id": operation["to_item_id"],
                    "duration_min": None, "distance_m": None, "cost_cny": None,
                    "reliability": "unavailable", "source": None, "observed_at": None,
                }
                day["legs"].append(leg)
            leg.update({
                "selected_mode": operation["selected_mode"],
                "duration_min": None, "distance_m": None, "cost_cny": None,
                "reliability": "unavailable", "source": None, "observed_at": None,
            })
        else:
            raise ValueError("不支持的结构化编辑操作")

        for day in document["days"]:
            TripEditService._refresh_day_legs(day)
        TripEditService._refresh_derived_totals_and_budget(document, op)
        validated = TripDocumentV2.model_validate(document)
        return validated.model_dump(mode="json")

    @staticmethod
    def _upsert_selection(document: dict, place_id: str, name: str) -> None:
        selections = document.setdefault("selections", [])
        selection = next((item for item in selections if item["place_id"] == place_id), None)
        if selection is None:
            selections.append({"place_id": place_id, "name": name, "preference": "interested"})
        else:
            selection["name"] = name
            if selection["preference"] == "excluded":
                selection["preference"] = "interested"

    @staticmethod
    def _assert_place_snapshot(place: dict) -> None:
        facts = place.get("facts") or {}
        if facts.get("source") != "tencent:lbs":
            raise ValueError("地点候选必须来自已接入的腾讯地点查询")
        if facts.get("verification") != "unknown":
            raise ValueError("该地点查询没有营业时间或价格核实结果")
        if facts.get("opening_hours") is not None or facts.get("ticket_price_cny") is not None:
            raise ValueError("腾讯地点查询未提供营业时间或门票价格")

    @staticmethod
    def _refresh_day_dates(document: dict) -> None:
        document["brief"]["day_count"] = len(document["days"])
        start_date = document["brief"].get("start_date")
        if start_date is None:
            for day in document["days"]:
                day["date"] = None
            return
        base_date = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
        for index, day in enumerate(document["days"]):
            day["date"] = (base_date + timedelta(days=index)).isoformat()

    @staticmethod
    def _refresh_day_legs(day: dict) -> None:
        existing = {
            (leg["from_item_id"], leg["to_item_id"]): leg
            for leg in day.get("legs", [])
        }
        legs: list[dict] = []
        for source, target in zip(day["items"], day["items"][1:]):
            edge = (source["item_id"], target["item_id"])
            leg = existing.get(edge)
            if leg is None:
                leg = {
                    "leg_id": f"leg-{uuid4().hex}",
                    "from_item_id": edge[0], "to_item_id": edge[1],
                    "selected_mode": "transit", "duration_min": None,
                    "distance_m": None, "cost_cny": None,
                    "reliability": "unavailable", "source": None,
                    "observed_at": None,
                }
            legs.append(leg)
        day["legs"] = legs

    @staticmethod
    def _refresh_derived_totals_and_budget(document: dict, operation: str) -> None:
        legs = [leg for day in document["days"] for leg in day.get("legs", [])]
        document["totals"]["transit_min"] = sum(
            int(leg["duration_min"])
            for leg in legs
            if leg.get("reliability") in {"verified", "estimated"}
            and leg.get("duration_min") is not None
        )
        if legs and all(
            leg.get("reliability") in {"verified", "estimated"}
            and leg.get("distance_m") is not None
            for leg in legs
        ):
            document["totals"]["distance_m"] = sum(int(leg["distance_m"]) for leg in legs)
        else:
            document["totals"]["distance_m"] = None

        budget_amount = document["brief"]["budget"].get("amount")
        estimated_cost = document["totals"].get("estimated_cost")
        health = document.setdefault("health", {"status": "ok", "issues": []})
        issues = [
            issue for issue in health.get("issues", [])
            if issue.get("code") not in {
                "BUDGET_EXCEEDED", "BUDGET_ESTIMATE_UNKNOWN", "BUDGET_ESTIMATE_REVIEW",
            }
        ]
        if budget_amount is not None and estimated_cost is not None and estimated_cost > budget_amount:
            issues.append({
                "code": "BUDGET_EXCEEDED", "severity": "warning",
                "message": "当前行程估算费用已超过预算上限，请复核安排。",
            })
        elif budget_amount is not None and operation in {
            "add_activity", "add_place", "replace_place", "remove_item", "add_day", "remove_day",
        }:
            if estimated_cost is None:
                code = "BUDGET_ESTIMATE_UNKNOWN"
                message = "本次编辑可能影响总费用，但当前没有可用的费用估算。"
            else:
                code = "BUDGET_ESTIMATE_REVIEW"
                message = "行程项已调整；缺少按项目费用明细，总费用估算需要复核。"
            issues.append({"code": code, "severity": "warning", "message": message})
        health["issues"] = issues
        if any(issue.get("severity") == "error" for issue in issues):
            health["status"] = "blocked"
        elif any(issue.get("severity") == "warning" for issue in issues):
            health["status"] = "needs_attention"

    @staticmethod
    def _validate_edit_schedule(document: dict) -> None:
        for day in document["days"]:
            previous: tuple[dict, int, int] | None = None
            for item in day["items"]:
                start_value = item.get("start_time")
                if start_value is None:
                    continue
                start = time.fromisoformat(start_value)
                start_minute = start.hour * 60 + start.minute
                end_minute = start_minute + int(item.get("duration_min") or 0)
                if end_minute > 24 * 60:
                    raise ValueError(f"行程项 {item['title']} 超出当天时间范围")
                if previous is not None:
                    previous_item, _previous_start, previous_end = previous
                    travel_min = 0
                    leg = next((
                        value for value in day["legs"]
                        if value["from_item_id"] == previous_item["item_id"]
                        and value["to_item_id"] == item["item_id"]
                    ), None)
                    if leg and leg.get("reliability") in {"verified", "estimated"}:
                        travel_min = int(leg.get("duration_min") or 0)
                    if start_minute < previous_end + travel_min:
                        raise ValueError(
                            f"{previous_item['title']} 与 {item['title']} 的时间或路程安排冲突"
                        )
                TripEditService._validate_opening_hours(day, item, start_minute, end_minute)
                previous = (item, start_minute, end_minute)

    @staticmethod
    def _validate_opening_hours(day: dict, item: dict, start: int, end: int) -> None:
        place = item.get("place") or {}
        facts = place.get("facts") or {}
        if facts.get("verification") != "verified":
            return
        hours = facts.get("opening_hours")
        if not isinstance(hours, dict):
            return
        day_date = day.get("date")
        if day_date:
            weekday = (date.fromisoformat(day_date) if isinstance(day_date, str) else day_date).weekday()
            closed = hours.get("closed_weekdays") or []
            if weekday in closed or str(weekday) in closed:
                raise ValueError(f"{item['title']} 在行程日期闭馆")
        open_value = hours.get("open_time")
        close_value = hours.get("close_time")
        if not open_value or not close_value:
            return
        try:
            open_time = time.fromisoformat(str(open_value))
            close_time = time.fromisoformat(str(close_value))
        except ValueError:
            return
        open_minute = open_time.hour * 60 + open_time.minute
        close_minute = close_time.hour * 60 + close_time.minute
        if start < open_minute or end > close_minute:
            raise ValueError(f"{item['title']} 的安排超出已核实营业时间")

    def restore(
        self, *, trip_id: str | UUID, tenant_id: str, owner_id: str,
        actor_id: str, expected_revision: int, target_revision: int,
        idempotency_key: str,
    ) -> dict:
        if target_revision < 1:
            raise ValueError("target_revision must be >= 1")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                target = self._revisions.get(
                    cursor, trip_id, target_revision,
                    tenant_id=tenant_id, owner_id=owner_id,
                )
        if not target:
            raise RevisionNotFound("历史版本不存在")
        snapshot = TripDocumentV2.model_validate(target["snapshot"])
        return self.apply_document(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            command_type="restore_revision",
            change_summary=f"恢复版本 {target_revision}",
            document=snapshot,
        )

    def _replay_if_present(
        self, cursor, *, trip_id: str | UUID, tenant_id: str,
        owner_id: str, idempotency_key: str, request_hash: str,
    ) -> dict | None:
        operation = self._operations.get_by_key(
            cursor, trip_id, idempotency_key,
            tenant_id=tenant_id, owner_id=owner_id,
        )
        if not operation:
            return None
        if operation["request_sha256"] != request_hash:
            raise IdempotencyConflict("Idempotency-Key 已用于不同的编辑请求")
        return {**operation["response"], "replayed": True}

    @staticmethod
    def _validate_protected_items(current: dict, proposed: dict) -> None:
        old_items = {
            item["item_id"]: (day["day_id"], item)
            for day in current["days"] for item in day["items"]
        }
        new_items = {
            item["item_id"]: (day["day_id"], item)
            for day in proposed["days"] for item in day["items"]
        }
        for item_id, (old_day_id, old_item) in old_items.items():
            new = new_items.get(item_id)
            if old_item.get("locked"):
                if new is None:
                    raise ValueError(f"locked item cannot be removed: {item_id}")
                if new[0] != old_day_id:
                    raise ValueError(f"locked item cannot be moved: {item_id}")
                if not new[1].get("locked"):
                    raise ValueError(f"locked item cannot be unlocked: {item_id}")
            if old_item.get("fixed_start"):
                if new is None or new[0] != old_day_id:
                    raise ValueError(f"fixed item cannot be removed or moved: {item_id}")
                if new[1].get("start_time") != old_item.get("start_time"):
                    raise ValueError(f"fixed item start time cannot change: {item_id}")
                if not new[1].get("fixed_start"):
                    raise ValueError(f"fixed item cannot be unlocked: {item_id}")

        # 已存在的锁定/固定项不能相对同日既有安排换位；新增项仍可插入其中。
        old_positions = {
            day["day_id"]: [item["item_id"] for item in day["items"]]
            for day in current["days"]
        }
        new_positions = {
            day["day_id"]: [item["item_id"] for item in day["items"]]
            for day in proposed["days"]
        }
        protected = {
            item_id
            for item_id, (_day_id, item) in old_items.items()
            if item.get("locked") or item.get("fixed_start")
        }
        for day_id, old_order in old_positions.items():
            new_order = new_positions.get(day_id, [])
            surviving = set(old_order).intersection(new_order)
            surviving_old = [item_id for item_id in old_order if item_id in surviving]
            surviving_new = [item_id for item_id in new_order if item_id in surviving]
            old_rank = {item_id: index for index, item_id in enumerate(surviving_old)}
            new_rank = {item_id: index for index, item_id in enumerate(surviving_new)}
            for protected_id in protected.intersection(surviving_old):
                old_before = [item_id for item_id in surviving_old if item_id != protected_id]
                for other_id in old_before:
                    if (old_rank[other_id] < old_rank[protected_id]) != (
                            new_rank[other_id] < new_rank[protected_id]):
                        raise ValueError(f"locked or fixed item cannot be reordered: {protected_id}")
