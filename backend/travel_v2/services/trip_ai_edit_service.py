from __future__ import annotations

import contextvars
import json
import re
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from uuid import UUID, uuid4

from backend.travel.core.intent import QUERY_INTENTS, TravelIntent, classify_intent
from backend.travel.services.llm_intent_service import classify_intent_llm
from backend.travel_v2.db import memory_connection
from backend.travel_v2.models.ai_edit import AITripEditCandidate, AITripEditRequest
from backend.travel_v2.models.commands import StructuredTripEditCommand
from backend.travel_v2.repositories.ai_edit_request_repository import (
    AIEditRequestRepository,
)
from backend.travel_v2.repositories.edit_operation_repository import (
    EditOperationRepository,
)
from backend.travel_v2.repositories.trip_repository import TripRepository
from backend.travel_v2.services.search_service import stable_selection_id
from backend.travel_v2.services.trip_edit_service import (
    IdempotencyConflict,
    TripEditService,
    VersionConflict,
)
from backend.travel_v2.services.trip_service import (
    TripNotFound,
    canonical_request_hash,
)
from backend.travel_v2.services.trip_write_validator import (
    TripWriteConflictError,
    trip_write_advisories,
    validate_trip_write,
)

_ALLOWED_OPS = frozenset({
    "update_item", "move_item", "add_activity", "remove_item",
    "replace_place", "reorder_day",
})
_EDIT_VERBS = re.compile(r"修改|调整|改一下|改成|改到|改为|挪|移动|移到|加一个|加个|增加|添加|安排|插入|删除|移除|去掉|删掉|换成|替换|重排|调换")
_MULTI_CHANGE_HINTS = re.compile(r"但|而且|并且|同时|顺便|另外")
_NO_EXECUTION_REQUEST = re.compile(
    r"先不要(?:修改|调整|改动|保存)|暂时不要(?:修改|调整|改动|保存)|"
    r"不要修改|别修改|先别改|暂时别改|不要动行程|先别保存|不要保存|"
    r"(?:请)?不要执行|别执行|请勿执行|禁止执行|"
    r"(?:请)?(?:不要|别|请勿|禁止)(?:再|立即|实际|直接)?"
    r"(?:执行|修改|调整|改动|保存|应用|提交|写入)"
)
_DEFERRED_WRITE_REQUEST = re.compile(
    r"(?:先|暂时|暂|现在先|目前先)(?:不|不用|不要|别|不必|暂缓)(?:再)?"
    r"(?:执行|修改|调整|改动|保存|应用|提交|写入)|"
    r"(?:不用|无需|不需要|不必)(?:再)?(?:实际)?(?:保存|应用|提交|执行|写入)|"
    r"(?:不|不用|不必|无需|不需要|别)(?:再|立即|实际|直接)?"
    r"(?:保存|应用|提交|执行|写入)|"
    r"(?:不想|不打算|不希望|不准备)(?:再)?(?:保存|应用|提交|执行|写入)|"
    r"(?:暂缓|推迟|稍后再)(?:保存|应用|提交|执行|写入)|"
    r"等.{0,12}(?:确认|同意|回复).{0,8}(?:再|以后|之后)"
    r"(?:保存|应用|提交|执行|写入)"
)
_CONSULTATIVE_REQUEST = re.compile(
    r"你觉得|你认为|会不会|会怎样|会怎么样|会如何|会有什么影响|"
    r"(?:哪个|比较|对比|相比).{0,20}(?:合适|适合|更好|更轻松)|"
    r"(?:合适|适合|太赶|太满|可行|妥当)(?:吗|不)|怎么样|如何|好不好|值不值得|"
    r"是否|(?:可以吗|能不能|可不可以|行不行|对吗|妥吗)[？?]?$|(?:吗|么)[？?]?$|[？?]$"
)
_EXECUTION_WORDING = re.compile(
    r"(?:请(?:您|你)?(?:直接)?|麻烦(?:您|你)?(?:直接)?|"
    r"直接帮我|帮我(?:直接)?|替我|为我)\s*"
    r"(?:把|将|改|修改|调整|删除|移除|去掉|增加|添加|安排|移动|替换|重排)"
)
_HYPOTHETICAL_REQUEST = re.compile(r"如果|假如|要是|倘若|假设|比如(?:说)?")
_REFERENCE_OR_DEMO_REQUEST = re.compile(
    r"(?:以下|上文|以上|这(?:个|句|条|段))?.{0,8}(?:只是|仅是|仅供|作为)?"
    r"(?:演示|示例|例子|引用|引文)"
)
_QUOTED_EDIT_COMMAND = re.compile(
    r"[“\"'「『【][^”\"'」』】]{0,180}"
    r"(?:把|将|改|修改|调整|挪|移动|换|替换|删除|移除|去掉|增加|添加|安排|插入|重排)"
    r"[^”\"'」』】]{0,180}[”\"'」』】]"
)
_PERMISSION_CONDITION_PREFIX = re.compile(
    r"^\s*如果(?:可以|方便)(?:的话)?[，,]?\s*"
)
_POLITE_EXECUTION_PREFIX = re.compile(
    r"^\s*(?:(?:请(?:您|你)?|麻烦(?:您|你)?|"
    r"(?:直接)?帮我(?:直接)?|直接(?:帮我)?|替我|为我)\s*)*"
)
_DIRECT_EDIT_COMMAND = re.compile(
    r"^\s*(?:把|将|改|修改|调整|挪|移动|换|替换|删除|移除|去掉|删掉|"
    r"取消|加|增加|添加|安排|插入|重排|调换).{0,180}"
)


class AIEditModelOutputError(ValueError):
    """模型返回内容不能通过 JSON 与候选 Schema 校验。"""

    def __init__(self, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class AIEditModelUnavailableError(RuntimeError):
    """模型角色或 Prompt 不可用，尚未开始 Provider 调用。"""

    def __init__(self, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class AIEditCallRejectedError(RuntimeError):
    """受治理的预算或限流门禁在 Provider 调用前拒绝本次请求。"""

    def __init__(self, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class AIEditCallUnknownError(RuntimeError):
    """Provider 调用结果不明，按 UNKNOWN 处理且禁止重新生成。"""

    def __init__(self, message: str, metadata: dict | None = None):
        super().__init__(message)
        self.metadata = metadata or {}


class TripAIEditService:
    """把 AI 候选经过范围检查后交给既有 V2 编辑事务提交。"""

    def __init__(
        self, *, connect=memory_connection,
        trip_repository: TripRepository | None = None,
        request_repository: AIEditRequestRepository | None = None,
        edit_operation_repository: EditOperationRepository | None = None,
        edit_service: TripEditService | None = None,
    ):
        self._connect = connect
        self._trips = trip_repository or TripRepository()
        self._requests = request_repository or AIEditRequestRepository()
        self._operations = edit_operation_repository or EditOperationRepository()
        self._edits = edit_service or TripEditService(connect=connect)

    def edit(
        self, *, trip_id: str, tenant_id: str, owner_id: str, actor_id: str,
        idempotency_key: str, request: AITripEditRequest | dict,
    ) -> dict:
        payload = request if isinstance(request, AITripEditRequest) else AITripEditRequest.model_validate(request)
        key = str(idempotency_key or "").strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        if any(len(value) > 128 for value in (tenant_id, owner_id, actor_id)):
            raise ValueError("trip identity fields exceed 128 characters")

        request_hash = canonical_request_hash({
            "trip_id": str(trip_id),
            "expected_revision": payload.expected_revision,
            "user_message": payload.user_message,
            "conversation_id": payload.conversation_id,
            "command_type": "ai_edit",
        })
        existing, current, request_id = self._reserve(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            idempotency_key=key, request_hash=request_hash,
            expected_revision=payload.expected_revision,
        )
        if existing is not None:
            return self._replay(
                existing, request_hash, trip_id, tenant_id, owner_id, key,
            )
        assert current is not None
        assert request_id is not None

        if int(current["revision"]) != payload.expected_revision:
            failure = self._failure(
                trip_id, payload.expected_revision, key,
                "VERSION_CONFLICT", "行程版本已变化，请刷新后重试",
            )
            failure["conflicts"] = [{
                "conflict_code": "VERSION_CONFLICT",
                "severity": "BLOCKED",
                "message": "行程版本已变化，请刷新后重试。",
                "affected_item_ids": [],
                "allowed_resolutions": [],
            }]
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=failure,
            )

        authorization = self._execution_authorization(payload.user_message)
        if authorization in {"read_only", "clarify"}:
            return self._finish_no_write(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, status=authorization,
            )

        intent = self._classify(payload.user_message, current["document"])
        if intent == "modify" and authorization != "authorized":
            intent = "clarify"
        if intent == "read_only":
            return self._finish_no_write(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, status="read_only",
            )
        if intent == "unsupported":
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="unsupported", request=payload,
                result={
                    "status": "unsupported", "saved": False,
                    "trip_id": str(trip_id),
                    "base_revision": payload.expected_revision,
                    "operation_key": key,
                    "message": "当前入口只支持已有行程的单项局部修改。",
                    "replayed": False,
                },
            )
        if intent == "clarify":
            return self._finish_no_write(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, status="clarify",
            )

        trip_context = self._trip_context(current)
        try:
            candidate, metadata = self._generate_candidate(
                payload.user_message, trip_context,
            )
        except TimeoutError:
            result = self._unknown(trip_id, payload.expected_revision, key)
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="unknown", request=payload,
                result=result, allow_unknown=True,
            )
        except AIEditModelOutputError as exc:
            metadata = {
                **exc.metadata,
                "role": "main", "operation_type": "invalid",
                "result": "invalid_model_output",
            }
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=self._failure(
                    trip_id, payload.expected_revision, key,
                    "AI_EDIT_INVALID_OUTPUT", "模型没有返回可用的结构化修改候选。",
                ), metadata=metadata,
            )
        except AIEditCallRejectedError as exc:
            metadata = {**exc.metadata, "result": "pre_call_rejected"}
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=self._failure(
                    trip_id, payload.expected_revision, key,
                    "AI_EDIT_PRECALL_REJECTED",
                    "模型调用在 Provider 请求前被预算或限流门禁拒绝，行程没有修改。",
                ), metadata=metadata,
            )
        except AIEditModelUnavailableError as exc:
            metadata = {**exc.metadata, "result": "model_unavailable"}
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=self._failure(
                    trip_id, payload.expected_revision, key,
                    "AI_EDIT_MODEL_UNAVAILABLE", "行程修改模型当前不可用。",
                ), metadata=metadata,
            )
        except AIEditCallUnknownError as exc:
            result = self._unknown(trip_id, payload.expected_revision, key)
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="unknown", request=payload,
                result=result,
                metadata={**exc.metadata, "result": "unknown"},
                allow_unknown=True,
            )
        except Exception as exc:  # noqa: BLE001 — Provider 终态不明时禁止重生成
            result = self._unknown(trip_id, payload.expected_revision, key)
            result["code"] = "AI_EDIT_CALL_UNKNOWN"
            metadata = {
                "role": "main", "operation_type": "unknown",
                "result": "unknown", "failure_type": type(exc).__name__,
            }
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="unknown", request=payload,
                result=result, metadata=metadata, allow_unknown=True,
            )

        # 写入前重复核对执行授权，避免任何候选绕过确定性门禁。
        authorization = self._execution_authorization(payload.user_message)
        if authorization != "authorized":
            status = "read_only" if authorization == "read_only" else "clarify"
            return self._finish_no_write(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, status=status,
                metadata={**metadata, "operation_type": "none",
                          "result": "execution_not_authorized"},
            )

        if candidate.outcome == "clarify":
            metadata.update({"operation_type": "none", "result": "clarify"})
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="clarify", request=payload,
                result={
                    "status": "clarify", "saved": False,
                    "trip_id": str(trip_id),
                    "base_revision": payload.expected_revision,
                    "operation_key": key,
                    "clarification": candidate.clarification,
                    "replayed": False,
                }, metadata=metadata,
            )
        if candidate.outcome == "unsupported":
            metadata.update({"operation_type": "none", "result": "unsupported"})
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="unsupported", request=payload,
                result={
                    "status": "unsupported", "saved": False,
                    "trip_id": str(trip_id),
                    "base_revision": payload.expected_revision,
                    "operation_key": key,
                    "message": "这项修改暂时无法安全映射为单个行程操作。",
                    "replayed": False,
                }, metadata=metadata,
            )

        operation: dict = {}
        try:
            operation = self._normalized_operation(candidate)
            self._validate_scope(
                payload.user_message, current["document"], operation,
            )
            self._place_add_activity_in_order(
                current["document"], operation,
            )
            # 纯结构预演在无锁状态下尽早拒绝伪造 ID/受保护项；提交时仍会
            # 在行锁内重新应用并运行共享 TripWriteValidator 与 CAS。
            proposed = TripEditService._apply_structured_operation(
                current["document"], operation,
            )
            validate_trip_write(
                proposed, current_document=current["document"],
            )
            command = StructuredTripEditCommand.model_validate({
                "expected_revision": payload.expected_revision,
                "change_summary": candidate.change_summary,
                "operation": operation,
            })
        except TripWriteConflictError as exc:
            return self._blocked_or_pending(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, operation=operation,
                request_id=request_id, request_sha256=request_hash,
                change_summary=candidate.change_summary,
                metadata=metadata, current_document=current["document"],
                conflict=exc.conflict,
            )
        except Exception as exc:  # noqa: BLE001 — 非法候选一律不进入正式写路径
            metadata.update({
                "operation_type": str(operation.get("op") or "invalid"),
                "result": "candidate_rejected",
                "rejection_type": type(exc).__name__,
            })
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=self._failure(
                    trip_id, payload.expected_revision, key,
                    "AI_EDIT_CANDIDATE_REJECTED",
                    "无法确认这项修改符合当前行程范围，请补充信息后重试。",
                ), metadata=metadata,
            )

        metadata.update({
            "intent": "modify",
            "operation_type": operation["op"],
            "result": "candidate_validated",
        })
        response_extra = {
            "status": "saved",
            "base_revision": payload.expected_revision,
            "new_revision": payload.expected_revision + 1,
            "operation_key": key,
            "change_summary": candidate.change_summary,
            "diff": self._diff(operation),
            "conflicts": trip_write_advisories(proposed),
        }
        try:
            saved = self._edits.apply_operation(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                actor_id=actor_id, idempotency_key=key, command=command,
                _request_hash=request_hash, _command_type="ai_edit",
                _response_extra=response_extra, _complete_ai_request=True,
                _ai_metadata=metadata,
            )
        except IdempotencyConflict:
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=self._failure(
                    trip_id, payload.expected_revision, key,
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key 已用于其他编辑请求。",
                ), metadata={**metadata, "result": "idempotency_conflict"},
            )
        except VersionConflict:
            failure = self._failure(
                trip_id, payload.expected_revision, key,
                "VERSION_CONFLICT", "行程在修改生成期间已变化，请刷新后重试",
            )
            failure["conflicts"] = [{
                "conflict_code": "VERSION_CONFLICT",
                "severity": "BLOCKED",
                "message": "行程在修改生成期间已变化，请刷新后重试。",
                "affected_item_ids": [],
                "allowed_resolutions": [],
            }]
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=failure, metadata={**metadata, "result": "version_conflict"},
            )
        except TripWriteConflictError as exc:
            return self._blocked_or_pending(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, request=payload, operation=operation,
                request_id=request_id, request_sha256=request_hash,
                change_summary=candidate.change_summary,
                metadata=metadata, current_document=current["document"],
                conflict=exc.conflict,
            )
        except ValueError as exc:  # noqa: BLE001 — 业务校验拒绝，写事务已回滚
            failure = self._failure(
                trip_id, payload.expected_revision, key,
                "AI_EDIT_VALIDATION_REJECTED",
                "服务端校验未通过，行程没有保存。",
            )
            return self._finish_result(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                key=key, status="failed", request=payload,
                result=failure,
                metadata={
                    **metadata, "result": "transaction_rejected",
                    "rejection_type": type(exc).__name__,
                },
            )
        except Exception:  # noqa: BLE001 — 提交结果可能不明，绝不伪称失败
            try:
                with self._connect() as connection:
                    with connection.cursor() as cursor:
                        persisted = self._requests.get(
                            cursor, trip_id=trip_id, tenant_id=tenant_id,
                            owner_id=owner_id, idempotency_key=key,
                        )
                if persisted and persisted["status"] == "succeeded":
                    return self._replay(
                        persisted, request_hash, trip_id,
                        tenant_id, owner_id, key,
                    )
            except Exception:  # noqa: BLE001 — 存储不可用时只返回未知状态
                pass
            return self._unknown(trip_id, payload.expected_revision, key)
        return {**saved, "replayed": False}

    def _blocked_or_pending(
        self, *, trip_id: str, tenant_id: str, owner_id: str, key: str,
        request: AITripEditRequest, operation: dict,
        request_id: UUID, request_sha256: str, change_summary: str,
        metadata: dict, current_document: dict, conflict: dict,
    ) -> dict:
        conflict = dict(conflict)
        resolution = self._next_available_time_resolution(
            current_document, operation, conflict,
        )
        if resolution:
            conflict["allowed_resolutions"] = [{
                "resolution_id": resolution["resolution_id"],
                "label": resolution["label"],
                "proposed_start_time": resolution["proposed_start_time"],
            }]
            pending_id = str(uuid4())
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=15)
            ).isoformat()
            pending = {
                "pending_id": pending_id,
                "trip_id": str(trip_id),
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "base_revision": request.expected_revision,
                "operation_key": key,
                "conflict_codes": [conflict["conflict_code"]],
                "affected_item_ids": conflict["affected_item_ids"],
                "allowed_resolutions": conflict["allowed_resolutions"],
                "expires_at": expires_at,
                "status": "pending",
            }
            result = {
                "status": "pending_resolution", "saved": False,
                "trip_id": str(trip_id),
                "base_revision": request.expected_revision,
                "operation_key": key, "pending_id": pending_id,
                "pending_resolution": pending,
                "conflicts": [conflict],
                "proposed_change": {
                    "change_summary": change_summary,
                    "diff": self._diff(operation),
                },
                "replayed": False,
            }
            plan = {
                "operation": operation,
                "mutable_fields": ["start_time"],
                "authorized_scope": {
                    "operation_type": operation.get("op"),
                    "item_id": operation.get("item_id"),
                    "mutable_fields": ["start_time"],
                },
                "change_summary": change_summary,
                "resolution_operations": {
                    resolution["resolution_id"]: {
                        "operation": resolution["operation"],
                        "diff": self._diff(resolution["operation"]),
                        "label": resolution["label"],
                    },
                },
            }
            return self._edits.persist_ai_pending_resolution(
                trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
                expected_revision=request.expected_revision,
                idempotency_key=key, request_id=request_id,
                request_sha256=request_sha256, response={
                    name: value for name, value in result.items()
                    if name != "replayed"
                },
                metadata={
                    **metadata, "result": "pending_resolution",
                    "pending_resolution": plan,
                },
            )

        result = self._failure(
            trip_id, request.expected_revision, key,
            "AI_EDIT_CONFLICT_BLOCKED", "服务端校验发现硬冲突，行程没有保存。",
        )
        result["conflicts"] = [conflict]
        return self._finish_result(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            key=key, status="failed", request=request, result=result,
            metadata={**metadata, "result": "blocked_conflict"},
        )

    @staticmethod
    def _next_available_time_resolution(
        document: dict, operation: dict, conflict: dict,
    ) -> dict | None:
        if (
            conflict.get("conflict_code") not in {
                "SCHEDULE_OVERLAP", "VERIFIED_OPENING_HOURS",
            }
            or operation.get("op") != "update_item"
            or not isinstance(operation.get("start_time"), str)
            or operation.get("item_id") not in conflict.get("affected_item_ids", [])
        ):
            return None
        day = next((
            day for day in document.get("days", [])
            if any(item.get("item_id") == operation["item_id"]
                   for item in day.get("items", []))
        ), None)
        if not day:
            return None
        item = next(item for item in day["items"]
                    if item.get("item_id") == operation["item_id"])
        if item.get("locked") or item.get("fixed_start"):
            return None
        try:
            start = dt_time.fromisoformat(operation["start_time"])
        except ValueError:
            return None
        start_minute = start.hour * 60 + start.minute
        for minute in range(start_minute + 1, 24 * 60):
            candidate_time = f"{minute // 60:02d}:{minute % 60:02d}"
            candidate = {**operation, "start_time": candidate_time}
            try:
                proposed = TripEditService._apply_structured_operation(
                    document, candidate,
                )
                validate_trip_write(proposed, current_document=document)
            except TripWriteConflictError as exc:
                if exc.conflict["conflict_code"] in {
                    "SCHEDULE_OVERLAP", "VERIFIED_OPENING_HOURS",
                }:
                    continue
                return None
            except ValueError:
                return None
            return {
                "resolution_id": "use_next_available_time",
                "label": f"将开始时间调整到 {candidate_time}",
                "proposed_start_time": candidate_time,
                "operation": candidate,
            }
        return None

    def operation_status(
        self, *, trip_id: str, tenant_id: str, owner_id: str,
        operation_key: str,
    ) -> dict | None:
        """只读查账；不会触发模型，也不把版本变化推断成该操作成功。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                request = self._requests.get(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=operation_key,
                )
                if request:
                    return self._status_for_request(
                        cursor, request, trip_id, tenant_id, owner_id,
                        operation_key,
                    )
                operation = self._operations.get_by_key(
                    cursor, trip_id, operation_key,
                    tenant_id=tenant_id, owner_id=owner_id,
                )
                if not operation:
                    return None
                return {
                    "status": "succeeded", "saved": True,
                    "trip_id": str(trip_id), "operation_key": operation_key,
                    "base_revision": operation["base_revision"],
                    "new_revision": operation["result_revision"],
                    "edit_operation": self._operation_identity(operation),
                    "result": operation["response"],
                }

    def _status_for_request(
        self, cursor, request: dict, trip_id: str, tenant_id: str,
        owner_id: str, operation_key: str,
    ) -> dict:
        status = request["status"]
        response = request["response"]
        if status in {"succeeded", "unknown"}:
            operation = self._authoritative_operation(
                cursor, request, trip_id, tenant_id, owner_id, operation_key,
            )
            if operation:
                result = operation["response"]
                return {
                    "status": "succeeded", "saved": True,
                    "trip_id": str(trip_id), "operation_key": operation_key,
                    "base_revision": operation["base_revision"],
                    "new_revision": operation["result_revision"],
                    "edit_operation": self._operation_identity(operation),
                    "result": result,
                }
            if status == "succeeded":
                return {
                    "status": "unknown", "saved": False,
                    "trip_id": str(trip_id), "operation_key": operation_key,
                    "base_revision": request["expected_revision"],
                    "code": "AI_EDIT_LEDGER_INCONSISTENT",
                    "message": "成功请求暂时无法关联到正式操作账本。",
                }
        if status == "processing":
            updated_at = request["updated_at"]
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated_at > timedelta(seconds=90):
                return {
                    "status": "unknown", "saved": False,
                    "trip_id": str(trip_id),
                    "base_revision": request["expected_revision"],
                    "operation_key": operation_key,
                    "code": "AI_EDIT_OUTCOME_UNKNOWN",
                    "possibly_still_running": True,
                }
            return {
                "status": "processing", "saved": False,
                "trip_id": str(trip_id),
                "base_revision": request["expected_revision"],
                "operation_key": operation_key,
            }
        if status == "pending_resolution":
            pending = response.get("pending_resolution") or {}
            expiry = datetime.fromisoformat(
                str(pending.get("expires_at", "")).replace("Z", "+00:00"),
            )
            if expiry <= datetime.now(timezone.utc):
                return {
                    **response, "status": "expired", "saved": False,
                    "pending_resolution": {**pending, "status": "expired"},
                }
        return {**response, "status": status, "operation_key": operation_key}

    def _authoritative_operation(
        self, cursor, request: dict, trip_id: str, tenant_id: str,
        owner_id: str, operation_key: str,
    ) -> dict | None:
        response = request["response"]
        linked_key = response.get("resolution_operation_key") or operation_key
        operation = self._operations.get_by_key(
            cursor, trip_id, linked_key, tenant_id=tenant_id, owner_id=owner_id,
        )
        if not operation:
            return None
        if linked_key == operation_key:
            return operation if operation["request_sha256"] == request["request_sha256"] else None
        confirmation = self._requests.get(
            cursor, trip_id=trip_id, tenant_id=tenant_id,
            owner_id=owner_id, idempotency_key=linked_key,
        )
        if (
            not confirmation
            or confirmation["status"] != "succeeded"
            or confirmation["request_sha256"] != operation["request_sha256"]
            or confirmation["metadata"].get("pending_id") != response.get("pending_id")
            or confirmation["metadata"].get("original_operation_key") != operation_key
        ):
            return None
        return operation

    @staticmethod
    def _operation_identity(operation: dict) -> dict:
        return {
            "operation_id": operation["operation_id"],
            "operation_key": operation["idempotency_key"],
            "command_type": operation["command_type"],
            "base_revision": operation["base_revision"],
            "result_revision": operation["result_revision"],
        }

    def get_pending_resolution(
        self, *, trip_id: str, tenant_id: str, owner_id: str,
        pending_id: str,
    ) -> dict | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                pending = self._requests.get_pending(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, pending_id=pending_id,
                )
        if not pending:
            return None
        result = pending["response"]
        if pending["status"] == "pending_resolution":
            contract = result.get("pending_resolution") or {}
            expiry = datetime.fromisoformat(
                str(contract.get("expires_at", "")).replace("Z", "+00:00"),
            )
            if expiry <= datetime.now(timezone.utc):
                return {
                    **result, "status": "expired", "saved": False,
                    "pending_resolution": {**contract, "status": "expired"},
                }
        return {**result, "status": pending["status"]}

    def resolve_pending_resolution(
        self, *, trip_id: str, tenant_id: str, owner_id: str,
        actor_id: str, pending_id: str, resolution_id: str,
        idempotency_key: str,
    ) -> dict:
        return self._edits.resolve_ai_pending(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            actor_id=actor_id, pending_id=pending_id,
            resolution_id=resolution_id, idempotency_key=idempotency_key,
        )

    def cancel_pending_resolution(
        self, *, trip_id: str, tenant_id: str, owner_id: str,
        pending_id: str,
    ) -> dict | None:
        return self._edits.cancel_ai_pending_resolution(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            pending_id=pending_id,
        )

    def _reserve(
        self, *, trip_id: str, tenant_id: str, owner_id: str,
        idempotency_key: str, request_hash: str, expected_revision: int,
    ) -> tuple[dict | None, dict | None, UUID | None]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                existing = self._requests.get(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=idempotency_key,
                )
                if existing:
                    return existing, None, None
                prior_operation = self._operations.get_by_key(
                    cursor, trip_id, idempotency_key,
                    tenant_id=tenant_id, owner_id=owner_id,
                )
                if prior_operation:
                    raise IdempotencyConflict(
                        "Idempotency-Key 已用于其他行程编辑操作",
                    )
                current = self._trips.get(
                    cursor, trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, for_update=True,
                )
                if not current:
                    raise TripNotFound("行程不存在")
                # Trip 行锁统一串行化 AI 与结构化入口；锁后重新读取两本账，
                # 避免前置检查与预留之间被同 key 的另一入口抢先提交。
                existing = self._requests.get(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=idempotency_key,
                )
                if existing:
                    return existing, None, None
                prior_operation = self._operations.get_by_key(
                    cursor, trip_id, idempotency_key,
                    tenant_id=tenant_id, owner_id=owner_id,
                )
                if prior_operation:
                    raise IdempotencyConflict(
                        "Idempotency-Key 已用于其他行程编辑操作",
                    )
                request_id = uuid4()
                inserted = self._requests.reserve(
                    cursor, request_id=request_id, trip_id=trip_id,
                    tenant_id=tenant_id, owner_id=owner_id,
                    idempotency_key=idempotency_key,
                    request_sha256=request_hash,
                    expected_revision=expected_revision,
                )
                if inserted:
                    return None, current, request_id
                existing = self._requests.get(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=idempotency_key,
                )
                if not existing:
                    raise RuntimeError("AI 编辑请求预留状态不可读取")
                return existing, None, None

    def _replay(self, existing: dict, request_hash: str,
                trip_id: str, tenant_id: str, owner_id: str,
                key: str) -> dict:
        if existing["request_sha256"] != request_hash:
            raise IdempotencyConflict("Idempotency-Key 已用于不同的 AI 修改请求")
        if existing["status"] == "processing":
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    self._requests.mark_stale_unknown(
                        cursor, trip_id=trip_id,
                        tenant_id=tenant_id,
                        owner_id=owner_id,
                        idempotency_key=key,
                    )
                    refreshed = self._requests.get(
                        cursor, trip_id=trip_id,
                        tenant_id=tenant_id,
                        owner_id=owner_id,
                        idempotency_key=key,
                    )
            if refreshed:
                existing = refreshed
        if existing["status"] == "processing":
            return {
                "status": "processing", "saved": False,
                "trip_id": str(trip_id),
                "base_revision": existing["expected_revision"],
                "operation_key": key, "replayed": True,
            }
        if existing["status"] == "unknown":
            if existing["response"]:
                return {**existing["response"], "replayed": True}
            return self._unknown(trip_id, existing["expected_revision"], key)
        if existing["status"] == "succeeded":
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    operation = self._authoritative_operation(
                        cursor, existing, trip_id, tenant_id, owner_id, key,
                    )
            if not operation:
                raise RuntimeError("AI 编辑成功状态与操作账本不一致")
            response = {
                **operation["response"], "operation_key": key,
                "replayed": True,
            }
            if existing["response"].get("resolution_operation_key"):
                response["resolution_operation_key"] = existing["response"][
                    "resolution_operation_key"
                ]
            return response
        if existing["response"]:
            return {**existing["response"], "replayed": True}
        raise RuntimeError("AI 编辑请求已结束但缺少持久结果")

    def _finish_result(
        self, *, trip_id: str, tenant_id: str, owner_id: str, key: str,
        status: str, request: AITripEditRequest, result: dict,
        metadata: dict | None = None, allow_unknown: bool = False,
    ) -> dict:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                self._requests.finish(
                    cursor, trip_id=trip_id, tenant_id=tenant_id,
                    owner_id=owner_id, idempotency_key=key, status=status,
                    response={key: value for key, value in result.items()
                              if key != "replayed"},
                    metadata=metadata, allow_unknown=allow_unknown,
                )
        return result

    def _finish_no_write(
        self, *, trip_id: str, tenant_id: str, owner_id: str, key: str,
        request: AITripEditRequest, status: str, metadata: dict | None = None,
    ) -> dict:
        result = {
            "status": status, "saved": False,
            "trip_id": str(trip_id),
            "base_revision": request.expected_revision,
            "operation_key": key,
            "replayed": False,
        }
        if status == "read_only":
            result["message"] = "这是咨询请求，本接口未修改行程。"
        else:
            result["clarification"] = "请确认是否要立即修改行程，并说明具体改动。"
        return self._finish_result(
            trip_id=trip_id, tenant_id=tenant_id, owner_id=owner_id,
            key=key, status=status, request=request, result=result,
            metadata=metadata,
        )

    @staticmethod
    def _execution_authorization(message: str) -> str:
        """仅在 V2 AI 写入口判断是否有立即执行授权。"""
        if _NO_EXECUTION_REQUEST.search(message):
            return "read_only"
        if _DEFERRED_WRITE_REQUEST.search(message):
            return "clarify" if _EDIT_VERBS.search(message) else "read_only"
        if _REFERENCE_OR_DEMO_REQUEST.search(message):
            return "read_only"
        if _QUOTED_EDIT_COMMAND.search(message):
            return "clarify"
        if _CONSULTATIVE_REQUEST.search(message):
            return (
                "clarify" if _EXECUTION_WORDING.search(message) else "read_only"
            )

        command = message
        permission_prefix = _PERMISSION_CONDITION_PREFIX.match(command)
        if permission_prefix:
            command = command[permission_prefix.end():]
        elif _HYPOTHETICAL_REQUEST.search(command) and _EDIT_VERBS.search(command):
            return "clarify"

        command = _POLITE_EXECUTION_PREFIX.sub("", command, count=1)
        if _DIRECT_EDIT_COMMAND.search(command):
            return "authorized"
        if _HYPOTHETICAL_REQUEST.search(command) and _EDIT_VERBS.search(command):
            return "clarify"
        return "unspecified"

    @staticmethod
    def _classify(message: str, document: dict) -> str:
        intent = classify_intent(
            message, has_itinerary=True,
            has_destination=bool((document.get("brief") or {}).get("destination")),
        )
        if intent == TravelIntent.MODIFY:
            return "modify"
        if intent in QUERY_INTENTS or intent in {
            TravelIntent.SOCIAL, TravelIntent.META,
        }:
            return "read_only"
        if intent in {TravelIntent.OUT_OF_SCOPE, TravelIntent.PLAN}:
            return "unsupported"
        if (intent is None and _EDIT_VERBS.search(message)
                and TripAIEditService._mentioned_items(message, document)):
            return "modify"
        family = classify_intent_llm(
            message, has_itinerary=True,
            has_destination=bool((document.get("brief") or {}).get("destination")),
        )
        if family == "modify":
            return "modify"
        if family == "query_dynamic":
            return "read_only"
        if family == "out_of_scope":
            return "unsupported"
        return "clarify"

    @staticmethod
    def _trip_context(trip: dict) -> dict:
        document = trip["document"]
        return {
            "revision": trip["revision"],
            "title": trip["title"],
            "brief": {
                "destination": document["brief"]["destination"],
                "start_date": document["brief"].get("start_date"),
                "day_count": document["brief"]["day_count"],
            },
            "days": [
                {
                    "day_id": day["day_id"], "date": day.get("date"),
                    "title": day["title"],
                    "items": [
                        {
                            "item_id": item["item_id"],
                            "kind": item["kind"],
                            "activity_type": item.get("activity_type"),
                            "title": item["title"],
                            "start_time": item.get("start_time"),
                            "duration_min": item["duration_min"],
                            "fixed_start": item["fixed_start"],
                            "must_visit": item["must_visit"],
                            "locked": item["locked"],
                            "place": item.get("place"),
                        }
                        for item in day["items"]
                    ],
                }
                for day in document["days"]
            ],
        }

    @staticmethod
    def _generate_candidate(message: str, trip_context: dict) -> tuple[AITripEditCandidate, dict]:
        from langchain_core.messages import HumanMessage, SystemMessage

        from backend.config import model_roles
        from backend.config.travel import TRAVEL_TURN_DECISION_TIMEOUT_MS
        from backend.context_budget.errors import ContextBudgetExceededError
        from backend.infra.async_utils import sync_call_with_timeout
        from backend.infra.llm.budget import RequestBudgetExceeded
        from backend.infra.llm.proxy import RateLimitError, bind_llm_for_role
        from backend.infra.llm.quota import QuotaConfigurationError, QuotaExceeded
        from backend.observability.llm_context import llm_attribution_scope
        from backend.prompts.service import prompt_service
        from backend.shared.json_extractor import extract_json

        started = time.monotonic()
        try:
            rendered = prompt_service.render_sync("travel.v2.trip_ai_edit")
        except Exception as exc:  # noqa: BLE001 — Prompt 失败时不调用 Provider
            raise AIEditModelUnavailableError(
                "managed prompt is unavailable",
                {"role": "main", "operation_type": "none"},
            ) from exc
        prompt_version = (
            f"travel.v2.trip_ai_edit@{rendered.version}"
            if rendered.version is not None else "travel.v2.trip_ai_edit@default"
        )
        model_config = model_roles.resolve_effective("main") or {}
        model_name = str(model_config.get("value") or "").strip()
        if not model_name:
            raise AIEditModelUnavailableError(
                "main model role is unavailable",
                {"role": "main", "prompt_version": prompt_version,
                 "operation_type": "none"},
            )
        try:
            llm = bind_llm_for_role("main", temperature=0, max_tokens=1800)
        except Exception as exc:  # noqa: BLE001 — 绑定失败前尚未发出模型请求
            raise AIEditModelUnavailableError(
                "main model could not be bound",
                {"role": "main", "model": model_name,
                 "prompt_version": prompt_version, "operation_type": "none"},
            ) from exc
        human_payload = json.dumps({
            "user_message": message,
            "trip_context": trip_context,
        }, ensure_ascii=False, separators=(",", ":"))
        timeout = max(0.5, TRAVEL_TURN_DECISION_TIMEOUT_MS / 1000)
        try:
            with llm_attribution_scope(agent_domain="travel"):
                call_context = contextvars.copy_context()
                response = sync_call_with_timeout(
                    lambda: call_context.run(
                        llm.invoke,
                        [SystemMessage(content=rendered.text),
                         HumanMessage(content=human_payload)],
                    ),
                    timeout,
                )
        except (
            RequestBudgetExceeded, ContextBudgetExceededError, QuotaExceeded,
            QuotaConfigurationError, RateLimitError,
        ) as exc:
            metadata = {
                "role": "main", "model": model_name,
                "prompt_version": prompt_version,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "operation_type": "none",
                "rejection_type": type(exc).__name__,
            }
            if getattr(exc, "provider_attempted", False):
                metadata["operation_type"] = "unknown"
                metadata["result"] = "provider_attempted_before_budget_rejection"
                raise AIEditCallUnknownError(
                    "provider attempt preceded governed budget rejection",
                    metadata,
                ) from exc
            raise AIEditCallRejectedError(
                "governed model pre-call rejection", metadata,
            ) from exc
        except Exception as exc:  # noqa: BLE001 — 请求可能已到达 Provider
            metadata = {
                "role": "main", "model": model_name,
                "prompt_version": prompt_version,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "input_tokens": 0, "output_tokens": 0,
            }
            raise AIEditCallUnknownError(
                "provider outcome is unknown", metadata,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage_metadata", None) or {}
        response_meta = getattr(response, "response_metadata", None) or {}
        if not isinstance(usage, dict):
            usage = {}
        if not usage and isinstance(response_meta, dict):
            usage = response_meta.get("token_usage") or {}
        metadata = {
            "role": "main", "model": model_name,
            "prompt_version": prompt_version,
            "latency_ms": latency_ms,
            "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            "output_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        }
        try:
            from backend.observability.llm_usage_store import current_usage_attribution

            attribution = current_usage_attribution()
            metadata["trace_id"] = attribution.get("trace_id", "")
            metadata["request_id"] = attribution.get("request_id", "")
        except Exception:  # noqa: BLE001 — 追踪关联缺失不影响安全执行
            pass
        raw = str(getattr(response, "content", "") or "")
        try:
            payload = extract_json(raw, source="travel.v2.trip_ai_edit")
            if not isinstance(payload, dict):
                raise ValueError("model output is not a JSON object")
            candidate = AITripEditCandidate.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — 不将非法模型输出暴露给写入层
            raise AIEditModelOutputError(
                "invalid structured AI edit output", metadata,
            ) from exc
        return candidate, metadata

    @staticmethod
    def _normalized_operation(candidate: AITripEditCandidate) -> dict:
        operation = candidate.operation
        if operation is None or operation.op not in _ALLOWED_OPS:
            raise ValueError("AI operation is not allowlisted")
        if operation.op == "update_item":
            normalized = operation.model_dump(mode="json", exclude_unset=True)
            if set(normalized) - {"op", "item_id", "start_time", "duration_min"}:
                raise ValueError("AI may update only an item's time or duration")
            return normalized
        normalized = operation.model_dump(mode="json")
        if operation.op == "replace_place":
            normalized["selection_id"] = stable_selection_id(
                "place", "tencent:lbs", normalized["place"]["place_id"],
            )
        if operation.op == "add_activity":
            normalized["duration_min"] = operation.duration_min
        return normalized

    @staticmethod
    def _place_add_activity_in_order(document: dict, operation: dict) -> None:
        if operation.get("op") != "add_activity" or not operation.get("start_time"):
            return
        if operation.get("position") is not None:
            return
        day = next(
            (item for item in document["days"]
             if item["day_id"] == operation["day_id"]),
            None,
        )
        if day is None:
            return
        new_start = dt_time.fromisoformat(operation["start_time"])
        for index, item in enumerate(day["items"]):
            old_start = item.get("start_time")
            if old_start and dt_time.fromisoformat(old_start) > new_start:
                operation["position"] = index
                return

    @staticmethod
    def _validate_scope(message: str, document: dict, operation: dict) -> None:
        action_count = len(re.findall(
            r"调整|修改|改成|改到|改为|设置|移动|挪到|移到|删除|去掉|移除|删掉"
            r"|增加|添加|加一个|加个|安排|插入|替换|换成|重排|调换",
            message,
        ))
        if action_count > 1 or (
            _MULTI_CHANGE_HINTS.search(message)
            and re.search(r"保留|必须|删除|移除|增加|添加|替换|换成", message)
        ):
            raise ValueError("一个请求包含多个原子修改")

        days = document["days"]
        day_by_id = {day["day_id"]: day for day in days}
        item_by_id = {
            item["item_id"]: (day, item)
            for day in days for item in day["items"]
        }
        op = operation["op"]

        if op == "add_activity":
            if not _EDIT_VERBS.search(message):
                raise ValueError("用户没有明确要求新增活动")
            day_ids = TripAIEditService._mentioned_day_ids(message, days)
            if not day_ids and len(days) == 1:
                day_ids = [days[0]["day_id"]]
            if len(day_ids) != 1 or operation["day_id"] != day_ids[0]:
                raise ValueError("新增活动的日期不唯一或与用户请求不符")
            activity_terms = {
                "meal": r"午餐|晚餐|吃饭|用餐",
                "rest": r"休息|歇一歇|放松",
                "free_time": r"自由时间|空闲|留白",
                "shopping": r"购物|逛街",
                "custom": re.escape(operation["title"]),
            }
            if not re.search(activity_terms[operation["activity_type"]], message):
                raise ValueError("新增活动类型没有在用户请求中明确表达")
            if operation.get("note") and operation["note"] not in message:
                raise ValueError("新增活动备注不是用户明确提供的内容")
            if (operation.get("duration_min", 60) != 60
                    and not TripAIEditService._mentions_duration(
                        message, operation["duration_min"],
                    )):
                raise ValueError("新增活动时长没有在用户请求中明确表达")
            if operation.get("start_time") and not TripAIEditService._mentions_clock(
                message, operation["start_time"],
            ):
                raise ValueError("新增时间没有在用户请求中明确表达")
            return

        if op in {"update_item", "remove_item", "move_item", "replace_place"}:
            item_id = operation["item_id"]
            if item_id not in item_by_id:
                raise TripWriteConflictError(
                    "TARGET_NOT_FOUND", "候选目标不属于当前 Trip", (item_id,),
                )
            matches = TripAIEditService._mentioned_items(message, document)
            if op == "replace_place":
                replacement_name = operation["place"]["name"]
                destination_ids = {
                    item["item_id"]
                    for day in days for item in day["items"]
                    if item["title"] == replacement_name
                    or (item.get("place") or {}).get("name") == replacement_name
                }
                matches = [item_id for item_id in matches if item_id not in destination_ids]
            if len(matches) != 1 or matches[0] != item_id:
                raise ValueError("用户修改目标不唯一或与候选不符")
            _day, item = item_by_id[item_id]
            if item.get("locked"):
                raise TripWriteConflictError(
                    "LOCKED_ITEM_CHANGED", "locked 行程项不能通过 AI 修改",
                    (item_id,),
                )
            if item.get("fixed_start"):
                raise TripWriteConflictError(
                    "FIXED_START_CHANGED", "fixed_start 行程项不能通过 AI 修改",
                    (item_id,),
                )
            must_visit_ids = {
                selection["place_id"]
                for selection in document.get("selections", [])
                if selection.get("preference") == "must_visit"
            }
            protected_by_request = any(
                name in message and re.search(
                    rf"{re.escape(name)}.{{0,8}}(?:必须保留|一定保留|不要删|不许动|别动)",
                    message,
                )
                for name in {
                    item["title"],
                    (item.get("place") or {}).get("name", ""),
                }
                if name
            )
            if op in {"remove_item", "replace_place"} and (
                item.get("must_visit")
                or (item.get("place") or {}).get("place_id") in must_visit_ids
                or protected_by_request
            ):
                raise TripWriteConflictError(
                    "MUST_VISIT_PROTECTED",
                    "用户指定的必去或明确保留项目不能删除或替换",
                    (item_id,),
                )
            if op == "update_item":
                if ("start_time" in operation and operation["start_time"] is None
                        and not re.search(r"取消时间|不设时间|移除时间", message)):
                    raise ValueError("请求没有明确要求清除开始时间")
                if "start_time" in operation and not TripAIEditService._mentions_clock(
                    message, operation["start_time"],
                ):
                    raise ValueError("修改时间没有在用户请求中明确表达")
                if "duration_min" in operation and not TripAIEditService._mentions_duration(
                    message, operation["duration_min"],
                ):
                    raise ValueError("修改时长没有在用户请求中明确表达")
            elif op == "remove_item" and not re.search(r"删除|移除|去掉|删掉|不要", message):
                raise ValueError("用户没有明确要求移除目标")
            elif op == "move_item":
                day_ids = TripAIEditService._mentioned_day_ids(message, days)
                target = operation["target_day_id"]
                if (target not in day_by_id or target == _day["day_id"]
                        or target not in day_ids):
                    raise ValueError("移动目标日期不明确或与用户请求不符")
            elif op == "replace_place":
                replacement = operation["place"]
                if replacement == item.get("place"):
                    raise ValueError("替换地点与当前地点相同")
                known_places = [
                    current.get("place") for _, current in item_by_id.values()
                    if current.get("place")
                ]
                if replacement not in known_places:
                    raise ValueError("不能使用当前 Trip 快照之外的地点")
                if replacement["name"] not in message:
                    raise ValueError("替换地点没有在用户请求中明确表达")
            return

        if op == "reorder_day":
            day_id = operation["day_id"]
            if day_id not in day_by_id:
                raise ValueError("重排日期不属于当前 Trip")
            expected_ids = [item["item_id"] for item in day_by_id[day_id]["items"]]
            if set(operation["item_ids"]) != set(expected_ids):
                raise ValueError("重排不能增删当前行程项")
            if len(TripAIEditService._mentioned_day_ids(message, days)) != 1:
                raise ValueError("重排日期不明确")
            mentioned = TripAIEditService._mentioned_items(message, document)
            if set(mentioned) != set(expected_ids):
                raise ValueError("重排范围未在用户请求中完整列出")
            positions = {}
            for item_id in expected_ids:
                item = item_by_id[item_id][1]
                aliases = [item["title"]]
                if item.get("place"):
                    aliases.append(item["place"]["name"])
                found = [message.find(alias) for alias in aliases if alias in message]
                if not found:
                    raise ValueError("重排目标没有明确顺序")
                positions[item_id] = min(found)
            if len(set(positions.values())) != len(expected_ids):
                raise ValueError("重排行程项名称不唯一")
            mentioned_order = sorted(expected_ids, key=positions.__getitem__)
            if operation["item_ids"] != mentioned_order:
                raise ValueError("候选顺序与用户描述顺序不一致")
            return

        raise ValueError("当前 AI 编辑入口不支持该操作类型")

    @staticmethod
    def _mentioned_day_ids(message: str, days: list[dict]) -> list[str]:
        result = []
        for index, day in enumerate(days, 1):
            aliases = [day["day_id"], f"第{index}天", f"第{index}日"]
            if index <= 10:
                chinese = "零一二三四五六七八九十"[index]
                aliases.extend((f"第{chinese}天", f"第{chinese}日"))
            if day.get("date"):
                aliases.append(str(day["date"]))
            if any(alias and alias in message for alias in aliases):
                result.append(day["day_id"])
        return result

    @staticmethod
    def _mentioned_items(message: str, document: dict) -> list[str]:
        matches = []
        for day in document["days"]:
            for item in day["items"]:
                names = [item["title"]]
                if item.get("place"):
                    names.append(item["place"]["name"])
                if item["item_id"] in message or any(
                    len(name.strip()) >= 2 and name.strip() in message for name in names
                ):
                    matches.append(item["item_id"])
        return list(dict.fromkeys(matches))

    @staticmethod
    def _mentions_clock(message: str, value: str | None) -> bool:
        if value is None:
            return True
        target = dt_time.fromisoformat(value)
        target_time = (target.hour, target.minute, target.second)
        mentioned: list[tuple[int, tuple[int, int, int]]] = []
        for match in re.finditer(
            r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", message,
        ):
            clock = (
                int(match.group(1)), int(match.group(2)),
                int(match.group(3) or 0),
            )
            mentioned.append((match.start(), clock))
        for match in re.finditer(
            r"(?<!\d)(\d{1,2})\s*(?:点|时)(?:(\d{1,2})\s*分?|半|整)?",
            message,
        ):
            hour = int(match.group(1))
            minute_text = match.group(2)
            minute = int(minute_text) if minute_text else (
                30 if "半" in match.group(0) else 0
            )
            mentioned.append((match.start(), (hour, minute, 0)))
        return bool(mentioned) and max(mentioned, key=lambda entry: entry[0])[1] == target_time

    @staticmethod
    def _mentions_duration(message: str, value: int) -> bool:
        number = r"(?:\d{1,3}|[零一两二三四五六七八九十]+|半)"
        pattern = re.compile(
            rf"(?<![\d.])(?:(?P<hours>{number})(?:个)?(?P<half_before>半)?\s*"
            rf"(?:小时|时)(?P<half_after>半)?\s*)?"
            rf"(?:(?P<minutes>{number})\s*(?:分钟|分))?"
        )

        def parse_number(text: str, *, half_allowed: bool) -> int | None:
            if text.isdigit():
                return int(text)
            if text == "半":
                return 30 if half_allowed else None
            digits = {
                "零": 0, "一": 1, "两": 2, "二": 2, "三": 3,
                "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
                "九": 9,
            }
            if text == "十":
                return 10
            if "十" in text:
                tens, ones = text.split("十", 1)
                if (tens and tens not in digits) or (ones and ones not in digits):
                    return None
                return (digits[tens] if tens else 1) * 10 + digits.get(ones, 0)
            if len(text) == 1:
                return digits.get(text)
            return None

        parsed: list[int] = []
        for match in pattern.finditer(message):
            hours_text = match.group("hours")
            minutes_text = match.group("minutes")
            if not hours_text and not minutes_text:
                continue
            total = 0
            if hours_text:
                hours = parse_number(hours_text, half_allowed=True)
                if hours is None:
                    continue
                total += hours if hours_text == "半" else hours * 60
                if match.group("half_before") or match.group("half_after"):
                    if hours_text == "半":
                        continue
                    total += 30
            if minutes_text:
                minutes = parse_number(minutes_text, half_allowed=False)
                if minutes is None:
                    continue
                total += minutes
            parsed.append(total)
        return bool(parsed) and parsed[-1] == value

    @staticmethod
    def _diff(operation: dict) -> dict:
        item_ids = [
            operation.get("item_id"), operation.get("from_item_id"),
            operation.get("to_item_id"),
        ]
        day_ids = [operation.get("day_id"), operation.get("target_day_id")]
        if operation.get("op") == "reorder_day":
            item_ids.extend(operation.get("item_ids") or [])
        return {
            "operation_type": operation["op"],
            "item_ids": list(dict.fromkeys(item_id for item_id in item_ids if item_id)),
            "day_ids": list(dict.fromkeys(day_id for day_id in day_ids if day_id)),
        }

    @staticmethod
    def _failure(trip_id: str, revision: int, key: str,
                 code: str, message: str) -> dict:
        return {
            "status": "failed", "saved": False,
            "trip_id": str(trip_id), "base_revision": revision,
            "operation_key": key, "code": code,
            "message": message, "replayed": False,
        }

    @staticmethod
    def _unknown(trip_id: str, revision: int, key: str) -> dict:
        return {
            "status": "unknown", "saved": False,
            "trip_id": str(trip_id), "base_revision": revision,
            "operation_key": key, "code": "AI_EDIT_OUTCOME_UNKNOWN",
            "message": "请求结果尚未确认；请使用相同 Idempotency-Key 重试查询处理状态，不要更换 key 重发。",
            "replayed": False,
        }


__all__ = ["TripAIEditService"]
