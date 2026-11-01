import json
from pathlib import Path

import pytest

from backend.evaluation_v2.domain_contracts import (
    validate_dataset_domain,
    validate_non_rag_case,
)
from backend.evaluation_v2.imports import validate_non_rag_upload


def _manifest(module: str) -> dict:
    return {
        "schema_version": "evaluation-v2/1.0",
        "dataset_key": f"{module.replace('-', '_')}_golden",
        "name": f"{module} Golden",
        "version": "1.0.0",
        "module": module,
    }


def test_dataset_domain_comes_from_registered_module_kind():
    # 2026-11-01 收编重构：travel-booking 探针随域图壳退役，不再是合法数据集域
    with pytest.raises(ValueError, match="评测模块登记值"):
        validate_dataset_domain("travel-booking")
    with pytest.raises(ValueError, match="评测模块登记值"):
        validate_dataset_domain("customer_service")
    assert validate_dataset_domain("travel") == "travel"


def test_sql_golden_reuses_existing_offline_validator_without_query_execution():
    sample_line = Path("backend/evaluation/datasets/sql/cases.jsonl").read_text(encoding="utf-8").splitlines()[0]
    case = json.loads(sample_line)
    validate_non_rag_case(
        "sql",
        {"query": case["question"]},
        case["expected"],
        [],
    )

    validate_non_rag_case(
        "sql",
        {"query": "越权查询"},
        {"should_reject": True, "expected_status": ["no_table"]},
        [],
    )


def test_cs_golden_reuses_existing_offline_route_contract():
    validate_non_rag_case(
        "cs",
        {"query": "我的订单怎么申请退款"},
        {"target": "cs_business_action", "must_contain": ["退款"]},
        [],
    )
    with pytest.raises(ValueError, match="expected.target 非法"):
        validate_non_rag_case("cs", {"query": "问题"}, {"target": "unknown"}, [])
    with pytest.raises(ValueError, match="1–40 条消息"):
        validate_non_rag_case(
            "cs", {"query": "问题", "turns": []},
            {"target": "cs_business_action"}, [],
        )


def test_travel_and_probe_goldens_validate_typed_assertions():
    validate_non_rag_case(
        "travel",
        {"query": "福州1天行程"},
        {"status": "ready", "destination": "福州", "days": 1},
        [],
    )
    validate_non_rag_case(
        "travel-provider", {"query": "probe: place success"}, {"status": "success"}, [],
    )
    with pytest.raises(ValueError, match="confidence_max"):
        validate_non_rag_case(
            "travel", {"query": "行程"}, {"confidence_max": 2}, [],
        )


def test_e2e_golden_reuses_registered_capability_and_workflow_contracts():
    validate_non_rag_case(
        "e2e",
        {"query": "每天早上自动跑经营日报"},
        {"route": "workflow", "workflow_name": "daily_report", "final_state": "SUCCESS"},
        [],
    )
    with pytest.raises(ValueError, match="未注册能力"):
        validate_non_rag_case(
            "e2e",
            {"query": "运行任务"},
            {"route": "direct", "capabilities": ["missing.skill"], "final_state": "SUCCESS"},
            [],
        )


def test_domain_contracts_turn_malformed_enum_values_into_validation_errors():
    invalid_cases = [
        ("e2e", {"route": {"direct": True}, "final_state": "SUCCESS"}),
        ("e2e", {"route": "direct", "capabilities": ["agent.chat"], "final_state": []}),
        ("e2e", {"fault": {"name": "timeout"}}),
        ("travel-provider", {"status": ["success"]}),
        ("travel-provider", {"status": "success", "freshness": {"value": "fresh"}}),
    ]
    for module, expected in invalid_cases:
        with pytest.raises(ValueError):
            validate_non_rag_case(module, {"query": "离线校验"}, expected, [])

    with pytest.raises(ValueError, match="无效角色"):
        validate_non_rag_case(
            "cs",
            {"turns": [{"role": ["user"], "text": "问题"}]},
            {"target": "cs_business_action"},
            [],
        )


def test_cs_golden_bounds_turn_count_and_message_size():
    expected = {"target": "cs_business_action"}
    too_many_turns = {
        "turns": [{"role": "user", "text": "问题"} for _ in range(41)],
    }
    oversized_turn = {
        "turns": [{"role": "user", "text": "x" * 12001}],
    }
    oversized_query = {"query": "x" * 12001}

    for case_input, message in (
        (too_many_turns, "1–40 条消息"),
        (oversized_turn, "12000 个字符"),
        (oversized_query, "12000 个字符"),
    ):
        with pytest.raises(ValueError, match=message):
            validate_non_rag_case("cs", case_input, expected, [])


def test_non_rag_jsonl_preflight_preserves_module_expected_fields():
    manifest = _manifest("cs")
    case = {
        "id": "CS-001",
        "question": "我的订单怎么申请退款",
        "expected": {"target": "cs_business_action", "must_contain": ["退款"]},
        "metadata": {"tier": "smoke"},
    }
    result = validate_non_rag_upload(
        "cases.jsonl",
        (json.dumps(case, ensure_ascii=False) + "\n").encode("utf-8"),
        manifest,
    )

    assert result["valid"] is True
    assert result["case_count"] == 1
    assert result["cases"][0]["expected"] == case["expected"]
    assert result["cases"][0]["input"] == {"query": case["question"]}


def test_non_rag_preflight_preserves_cs_turns_and_checks_declared_module():
    manifest = _manifest("cs")
    case = {
        "id": "CS-MULTI-001",
        "module": "cs",
        "input": {"turns": [
            {"role": "user", "text": "我想申请退款"},
            {"role": "assistant", "text": "请问是什么原因？"},
            {"role": "user", "text": "商品有质量问题"},
        ]},
        "expected": {"target": "cs_business_action", "must_contain": ["退款"]},
    }
    result = validate_non_rag_upload(
        "cases.jsonl",
        (json.dumps(case, ensure_ascii=False) + "\n").encode("utf-8"),
        manifest,
    )
    assert result["valid"] is True
    assert result["cases"][0]["input"]["query"] == "商品有质量问题"
    assert result["cases"][0]["input"]["turns"] == case["input"]["turns"]

    mismatched = {**case, "module": "travel"}
    rejected = validate_non_rag_upload(
        "cases.jsonl",
        (json.dumps(mismatched, ensure_ascii=False) + "\n").encode("utf-8"),
        manifest,
    )
    assert rejected["valid"] is False
    assert "case.module 必须与 manifest.module 一致" in rejected["errors"][0]["message"]


def test_registered_non_rag_fixture_expected_contracts_remain_compatible():
    fixtures = {
        "sql": "sql/cases.jsonl",
        "cs": "cs/cases.jsonl",
        "planner": "planner/cases.jsonl",
        "e2e": "e2e/cases.jsonl",
        "travel": "travel/cases.jsonl",
        "travel-provider": "travel-provider/cases.jsonl",
    }
    for module, relative_path in fixtures.items():
        rows = [
            json.loads(line)
            for line in Path(f"backend/evaluation/datasets/{relative_path}")
            .read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for row in rows:
            turns = row.get("turns")
            case_input = {"turns": turns} if turns else {"query": row.get("question", "")}
            validate_non_rag_case(
                module,
                case_input,
                row["expected"],
                row["expected"].get("evidence", []),
            )


def test_non_rag_preflight_rejects_dataset_evidence_and_unknown_contract():
    manifest = _manifest("travel")
    case = {
        "id": "T-001",
        "question": "福州1天行程",
        "expected": {"status": "ready", "evidence": [{"quote": "x"}]},
    }
    result = validate_non_rag_upload(
        "cases.jsonl",
        json.dumps(case, ensure_ascii=False).encode("utf-8"),
        manifest,
    )
    assert result["valid"] is False
    assert "不能绑定知识库证据锚点" in result["errors"][0]["message"]

    invalid_manifest = validate_non_rag_upload(
        "cases.jsonl", b"{}", {**manifest, "module": "memory"},
    )
    assert invalid_manifest["valid"] is False
    assert "已登记的非 RAG 模块" in invalid_manifest["errors"][0]["message"]

    rag_schema_manifest = validate_non_rag_upload(
        "cases.jsonl", b"{}", {**manifest, "schema_version": "rag-eval/1.0"},
    )
    assert rag_schema_manifest["valid"] is False
    assert "必须为 evaluation-v2/1.0" in rag_schema_manifest["errors"][0]["message"]
