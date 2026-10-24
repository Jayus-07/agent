"""非 RAG 评测 Golden 的离线领域契约校验。"""

from __future__ import annotations

from typing import Any

from backend.evaluation.models import MODULE_KINDS, TestCase

_CS_EXPECTED_FIELDS = {
    "target", "must_contain", "must_not_contain", "intent", "cs_route",
    "next_action", "risk_level", "should_handoff", "route_mode",
    "decision_layer", "entities", "missing_slots", "allowed_facts",
    "forbidden_facts",
}
_PLANNER_EXPECTED_FIELDS = {
    "capabilities", "max_steps", "min_steps", "should_not_contain",
    "edges", "params",
}
_E2E_EXPECTED_FIELDS = {
    "route", "capabilities", "workflow_name", "final_state", "should_block",
    "fault", "error_type",
}
_E2E_FAULT_NAMES = {
    "unretryable_error", "retryable_error", "timeout", "output_dict_normalized",
    "structured_str_parsed", "param_missing_required", "param_enum_violation",
    "approval_pending", "executor_no_candidates", "executor_skill_not_found",
    "sqlresult_rendered", "executor_step_failed",
}
_TRAVEL_COMMERCE_EXPECTED_FIELDS = {
    "status", "availability", "currency", "taxes", "deeplink",
    "freshness", "answer", "stops", "violation",
}
_TRAVEL_BOOKING_EXPECTED_FIELDS = {
    "status", "result", "final", "create", "order", "create_delta",
    "logical", "logical_orders", "blind_retry", "resend", "duplicate_dedup",
    "no_regression", "quarantined", "rejected",
}


def validate_dataset_domain(domain: str) -> str:
    """只允许统一评测框架登记的模块名作为数据集业务域。"""
    normalized = str(domain).strip()
    if normalized not in MODULE_KINDS:
        raise ValueError("数据集业务域必须使用评测模块登记值")
    return normalized


def validate_non_rag_case(
    domain: str,
    case_input: Any,
    expected: Any,
    evidence: Any,
) -> None:
    """校验离线资产结构，不调用模型、业务数据库或外部 Provider。"""
    normalized = validate_dataset_domain(domain)
    if normalized == "rag":
        raise ValueError("RAG 样本必须使用知识库证据审核契约")
    query = _case_query(case_input, allow_turns=normalized == "cs")
    if not isinstance(expected, dict) or not expected:
        raise ValueError("非 RAG 样本必须填写模块标准结果")
    if evidence:
        raise ValueError("非 RAG 样本不能绑定知识库证据锚点")

    if normalized == "sql":
        _validate_sql_expected(expected)
    elif normalized == "cs":
        _validate_cs_expected(query, expected)
    elif normalized == "travel":
        _validate_travel_expected(expected)
    elif normalized == "planner":
        _validate_planner_expected(expected)
    elif normalized == "e2e":
        _validate_e2e_expected(expected)
    elif normalized == "travel-provider":
        _validate_travel_provider_expected(expected)
    elif normalized == "travel-commerce":
        _validate_travel_commerce_expected(expected)
    elif normalized == "travel-booking":
        _validate_travel_booking_expected(expected)
    else:  # ModuleKind 扩展时必须显式定义 Golden 校验契约
        raise ValueError(f"评测模块 {normalized} 尚未适配 V2 Golden 契约")


def _validate_sql_expected(expected: dict[str, Any]) -> None:
    allowed = {"gold_sql", "expected_tables", "should_reject", "expected_status"}
    if set(expected) - allowed:
        raise ValueError("SQL Golden 包含未登记的 expected 字段")
    should_reject = expected.get("should_reject", False)
    if not isinstance(should_reject, bool):
        raise ValueError("expected.should_reject 必须是布尔值")
    if should_reject:
        if expected.get("gold_sql"):
            raise ValueError("SQL 拒绝样本不能附带可执行 gold_sql")
        statuses = expected.get("expected_status")
        if statuses is not None and (
            not isinstance(statuses, list)
            or not statuses
            or any(not isinstance(item, str) or not item.strip() for item in statuses)
        ):
            raise ValueError("拒绝样本的 expected_status 必须是非空字符串数组")
        return

    sql = expected.get("gold_sql")
    tables = expected.get("expected_tables")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("SQL 正向样本必须填写 gold_sql")
    if (
        not isinstance(tables, list)
        or not tables
        or any(not isinstance(table, str) or not table.strip() for table in tables)
    ):
        raise ValueError("SQL 正向样本必须填写 expected_tables 字符串数组")

    # 复用旧 SQL Runner 的离线数据校验：只检查白名单与 SQL 安全规则，绝不执行查询。
    from backend.evaluation.runners.sql import _run_offline_sanity

    result = _run_offline_sanity([
        TestCase(
            id="v2-golden-contract",
            question="离线 Golden 校验",
            module="sql",
            expected=expected,
        ),
    ])[0]
    if result.status != "pass":
        raise ValueError(result.error_msg or "SQL Golden 未通过离线校验")


def _validate_cs_expected(query: str, expected: dict[str, Any]) -> None:
    if set(expected) - _CS_EXPECTED_FIELDS:
        raise ValueError("客服 Golden 包含未登记的 expected 字段")
    for field in (
        "target", "intent", "cs_route", "next_action", "risk_level",
        "route_mode", "decision_layer",
    ):
        if field in expected and not isinstance(expected[field], str):
            raise ValueError(f"expected.{field} 必须是字符串")
    for field in (
        "must_contain", "must_not_contain", "missing_slots",
        "allowed_facts", "forbidden_facts",
    ):
        _optional_string_list(expected, field)
    if "should_handoff" in expected and not isinstance(expected["should_handoff"], bool):
        raise ValueError("expected.should_handoff 必须是布尔值")
    if "entities" in expected and not isinstance(expected["entities"], dict):
        raise ValueError("expected.entities 必须是对象")

    # 复用旧 Runner 的离线枚举校验，只校验字段，不执行客服图或模型。
    from backend.evaluation.runners.cs import _run_offline_sanity

    result = _run_offline_sanity([
        TestCase(
            id="v2-golden-contract",
            question=query,
            module="cs",
            expected=expected,
        ),
    ])[0]
    if result.status != "pass":
        raise ValueError(result.error_msg or "客服 Golden 未通过离线契约校验")


def _validate_planner_expected(expected: dict[str, Any]) -> None:
    if set(expected) - _PLANNER_EXPECTED_FIELDS:
        raise ValueError("Planner Golden 包含未登记的 expected 字段")
    _optional_string_list(expected, "capabilities")
    _optional_string_list(expected, "should_not_contain")
    for field in ("max_steps", "min_steps"):
        if field in expected and (
            isinstance(expected[field], bool)
            or not isinstance(expected[field], int)
            or expected[field] < 0
        ):
            raise ValueError(f"expected.{field} 必须是非负整数")
    if (
        "min_steps" in expected
        and "max_steps" in expected
        and expected["min_steps"] > expected["max_steps"]
    ):
        raise ValueError("expected.min_steps 不能大于 max_steps")
    edges = expected.get("edges", [])
    if not isinstance(edges, list) or any(
        not isinstance(edge, dict)
        or set(edge) != {"from", "to"}
        or any(not isinstance(edge[key], str) or not edge[key].strip() for key in ("from", "to"))
        for edge in edges
    ):
        raise ValueError("expected.edges 必须是 from/to 字符串对象数组")
    params = expected.get("params", {})
    if not isinstance(params, dict) or any(not isinstance(spec, dict) for spec in params.values()):
        raise ValueError("expected.params 必须是对象映射")


def _validate_e2e_expected(expected: dict[str, Any]) -> None:
    if set(expected) - _E2E_EXPECTED_FIELDS:
        raise ValueError("E2E Golden 包含未登记的 expected 字段")
    final_state = expected.get("final_state")
    if final_state is not None and (
        not isinstance(final_state, str)
        or final_state not in {"SUCCESS", "FAILED", "BLOCKED"}
    ):
        raise ValueError("expected.final_state 非法")
    if "capabilities" in expected:
        _optional_string_list(expected, "capabilities")
    if "should_block" in expected:
        if expected["should_block"] is not True or final_state != "BLOCKED":
            raise ValueError("E2E 拦截样本必须声明 should_block=true 和 final_state=BLOCKED")
        return
    if "fault" in expected:
        if not isinstance(expected["fault"], str) or expected["fault"] not in _E2E_FAULT_NAMES:
            raise ValueError("expected.fault 未登记")
        if "error_type" in expected:
            _require_nonempty_string(expected, "error_type")
            if expected["error_type"] not in {"not_found", "unknown", "timeout"}:
                raise ValueError("expected.error_type 未登记")
        return
    route = expected.get("route")
    from backend.evaluation.runners.e2e import (
        _VALID_ROUTES,
        _known_capabilities,
        _known_workflows,
    )

    if not isinstance(route, str) or route not in _VALID_ROUTES:
        raise ValueError("expected.route 未登记")
    known_capabilities = _known_capabilities()
    bad_capabilities = [
        capability for capability in expected.get("capabilities", [])
        if capability not in known_capabilities
    ]
    if bad_capabilities:
        raise ValueError("expected.capabilities 包含未注册能力")
    if route == "workflow":
        _require_nonempty_string(expected, "workflow_name")
        if expected["workflow_name"] not in _known_workflows():
            raise ValueError("expected.workflow_name 未注册")
    elif route in {"direct", "plan"} and not expected.get("capabilities"):
        raise ValueError(f"{route} E2E Golden 必须声明 capabilities")
    if final_state is None:
        raise ValueError("普通 E2E Golden 必须声明 final_state")


def _validate_travel_expected(expected: dict[str, Any]) -> None:
    allowed = {
        "status", "destination", "days", "party_size", "pace", "budget_cny",
        "min_plan_version", "parent_linked", "repair_triggered",
        "decision_required", "no_error_violations", "violation_codes",
        "confidence_max", "must_contain", "must_not_contain", "notes_contain",
        "notes_not_contain", "clarification",
    }
    if set(expected) - allowed:
        raise ValueError("旅游 Golden 包含未登记的 expected 字段")
    for key in ("status", "destination", "pace"):
        if key in expected and not isinstance(expected[key], str):
            raise ValueError(f"expected.{key} 必须是字符串")
    for key in ("days", "party_size", "min_plan_version"):
        if key in expected and (
            isinstance(expected[key], bool)
            or not isinstance(expected[key], int)
            or expected[key] < 0
        ):
            raise ValueError(f"expected.{key} 必须是非负整数")
    if "budget_cny" in expected and (
        isinstance(expected["budget_cny"], bool)
        or not isinstance(expected["budget_cny"], (int, float))
        or expected["budget_cny"] < 0
    ):
        raise ValueError("expected.budget_cny 必须是非负数字")
    for key in (
        "parent_linked", "repair_triggered", "decision_required",
        "no_error_violations", "clarification",
    ):
        if key in expected and not isinstance(expected[key], bool):
            raise ValueError(f"expected.{key} 必须是布尔值")
    _optional_string_list(expected, "violation_codes")
    for key in ("must_contain", "must_not_contain", "notes_contain", "notes_not_contain"):
        _optional_string_list(expected, key)
    if "confidence_max" in expected and (
        isinstance(expected["confidence_max"], bool)
        or not isinstance(expected["confidence_max"], (int, float))
        or not 0 <= expected["confidence_max"] <= 1
    ):
        raise ValueError("expected.confidence_max 必须在 0 到 1 之间")


def _validate_travel_provider_expected(expected: dict[str, Any]) -> None:
    if set(expected) - {"status", "freshness"}:
        raise ValueError("旅游 Provider Golden 包含未登记的 expected 字段")
    statuses = {
        "success", "not_found", "unavailable", "rate_limited",
        "invalid_response", "timeout", "unauthorized", "disabled",
    }
    status = expected.get("status")
    if not isinstance(status, str) or status not in statuses:
        raise ValueError("expected.status 不是已登记的 Provider 状态")
    if "freshness" in expected:
        freshness = expected["freshness"]
        if not isinstance(freshness, str) or freshness not in {"fresh", "stale", "cached", "unknown"}:
            raise ValueError("expected.freshness 未登记")


def _validate_travel_commerce_expected(expected: dict[str, Any]) -> None:
    if set(expected) - _TRAVEL_COMMERCE_EXPECTED_FIELDS or not expected:
        raise ValueError("旅游商城 Golden 字段为空或包含未登记字段")
    string_fields = {"status", "availability", "currency", "deeplink", "freshness", "answer"}
    for field in string_fields & expected.keys():
        if not isinstance(expected[field], str) or not expected[field].strip():
            raise ValueError(f"expected.{field} 必须是非空字符串")
    if "taxes" in expected and expected["taxes"] is not None and not isinstance(expected["taxes"], (int, float, str)):
        raise ValueError("expected.taxes 必须是数字、字符串或 null")
    for field in ("stops", "violation"):
        if field in expected and (
            isinstance(expected[field], bool)
            or not isinstance(expected[field], int)
            or expected[field] < 0
        ):
            raise ValueError(f"expected.{field} 必须是非负整数")


def _validate_travel_booking_expected(expected: dict[str, Any]) -> None:
    if set(expected) - _TRAVEL_BOOKING_EXPECTED_FIELDS or not expected:
        raise ValueError("旅游预订 Golden 字段为空或包含未登记字段")
    for field in {"status", "result", "final"} & expected.keys():
        if not isinstance(expected[field], str) or not expected[field].strip():
            raise ValueError(f"expected.{field} 必须是非空字符串")
    if "status" in expected and expected["status"] not in {"booked", "expired", "in_doubt"}:
        raise ValueError("expected.status 未登记")
    for field in {"create", "order", "create_delta", "logical", "logical_orders", "blind_retry", "resend"} & expected.keys():
        if isinstance(expected[field], bool) or not isinstance(expected[field], int) or expected[field] < 0:
            raise ValueError(f"expected.{field} 必须是非负整数")
    for field in {"duplicate_dedup", "no_regression", "quarantined", "rejected"} & expected.keys():
        if not isinstance(expected[field], bool):
            raise ValueError(f"expected.{field} 必须是布尔值")


def _case_query(case_input: Any, *, allow_turns: bool) -> str:
    if not isinstance(case_input, dict):
        raise ValueError("评测样本 input 必须是 JSON 对象")
    query = case_input.get("query")
    if allow_turns and "turns" in case_input:
        turns = case_input["turns"]
        if not isinstance(turns, list) or not 1 <= len(turns) <= 40:
            raise ValueError("input.turns 必须是 1–40 条消息")
        for item in turns:
            role = item.get("role") if isinstance(item, dict) else None
            if not isinstance(role, str) or role not in {"user", "assistant", "system"}:
                raise ValueError("input.turns 含无效角色或消息")
            if not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError("input.turns.text 必须为非空字符串")
            if len(item["text"]) > 12000:
                raise ValueError("input.turns.text 不得超过 12000 个字符")
        user_turns = [
            item for item in turns
            if isinstance(item, dict) and item.get("role") == "user"
            and isinstance(item.get("text"), str) and item["text"].strip()
        ]
        if user_turns:
            last_user_query = user_turns[-1]["text"].strip()
            if query is not None and query != last_user_query:
                raise ValueError("input.query 必须与最后一条客服用户消息一致")
            return last_user_query
        raise ValueError("客服 input.turns 至少需要一条 user 消息")
    if isinstance(query, str) and query.strip():
        if len(query) > 12000:
            raise ValueError("input.query 不得超过 12000 个字符")
        return query.strip()
    if allow_turns:
        raise ValueError("客服评测样本必须包含非空 query 或用户 turns")
    raise ValueError("评测样本缺少非空 query")


def _require_nonempty_string(expected: dict[str, Any], field: str) -> None:
    value = expected.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"expected.{field} 必须是非空字符串")


def _optional_string_list(expected: dict[str, Any], field: str) -> None:
    if field not in expected:
        return
    value = expected[field]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"expected.{field} 必须是字符串数组")
