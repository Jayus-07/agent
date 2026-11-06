"""Planner 完整 Plan 契约、打分与 canonical 样本有效性。"""

import json
from pathlib import Path

import pytest

from backend.agents.planner.plan_utils import validate_plan_contract
from backend.evaluation.dataset import load_dataset, validate_dataset
from backend.evaluation.dataset.loader import file_content_hash
from backend.evaluation.models import MODULE_KINDS, TestCase as EvalCase
from backend.evaluation.runner import evaluate_planner_offline


def _valid_plan() -> dict:
    return {
        "nodes": [
            {
                "step_id": "1",
                "capability": "sql.query",
                "description": "查询近30天销售额",
                "params": {"question": "查询近30天销售额"},
            },
            {
                "step_id": "2",
                "capability": "report.generate",
                "description": "生成销售简报",
                "params": {"report_type": "sales_summary"},
            },
        ],
        "edges": {"2": ["1"]},
    }


def _expected() -> dict:
    return {
        "capabilities": ["sql.query", "report.generate"],
        "should_not_contain": ["rag.search"],
        "min_steps": 2,
        "max_steps": 2,
        "edges": [{"from": "sql.query", "to": "report.generate"}],
    }


def test_plan_contract_accepts_complete_authorized_dag():
    assert validate_plan_contract(
        _valid_plan(), allowed={"sql.query", "report.generate"}
    ) == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.update({"unexpected": True}), "unsupported fields"),
        (lambda plan: plan["nodes"][0].pop("description"), "field set"),
        (lambda plan: plan["nodes"][0].update({"extra": True}), "field set"),
        (lambda plan: plan["nodes"][1].update({"step_id": "1"}), "duplicated"),
        (lambda plan: plan["edges"].update({"2": ["missing"]}), "unknown dependency"),
        (lambda plan: plan["edges"].update({"2": ["1", "1"]}), "duplicate dependency"),
        (lambda plan: plan["edges"].update({"1": ["2"]}), "cycle or exceeds"),
    ],
)
def test_plan_contract_rejects_incomplete_or_invalid_plan(mutate, message):
    plan = _valid_plan()
    mutate(plan)

    assert any(message in error for error in validate_plan_contract(plan))


def test_plan_contract_rejects_unauthorized_capability_before_normalization():
    plan = _valid_plan()

    errors = validate_plan_contract(plan, allowed={"sql.query"})

    assert any("not authorized" in error for error in errors)


def test_plan_contract_rejects_dependency_depth_over_scheduler_limit():
    nodes = [
        {
            "step_id": str(index),
            "capability": "sql.query",
            "description": f"步骤 {index}",
            "params": {},
        }
        for index in range(1, 10)
    ]
    plan = {
        "nodes": nodes,
        "edges": {str(index): [str(index - 1)] for index in range(2, 10)},
    }

    assert any(
        "maximum depth" in error
        for error in validate_plan_contract(plan)
    )


def test_exact_full_plan_passes_capability_edge_and_step_checks():
    result = evaluate_planner_offline(
        "P-REPORT", _expected(), actual_plan=_valid_plan()
    )

    assert result.status == "pass"
    assert result.metrics["capability_match"] == 1.0
    assert result.metrics["edge_match"] == 1.0
    assert result.metrics["step_count_ok"] == 1.0
    assert result.metrics["node_count"] == 2


def test_extra_capability_cannot_pass_by_jaccard_similarity():
    plan = _valid_plan()
    plan["nodes"].append({
        "step_id": "3",
        "capability": "rag.search",
        "description": "额外检索",
        "params": {},
    })

    result = evaluate_planner_offline("P-EXTRA", _expected(), actual_plan=plan)

    assert result.status == "fail"
    assert result.metrics["jaccard"] > 0.5
    assert result.metrics["capability_match"] == 0.0
    assert result.metrics["step_count_ok"] == 0.0


def test_missing_or_extra_dependency_edge_fails_exact_plan_check():
    missing = {**_valid_plan(), "edges": {}}
    extra = {**_valid_plan(), "edges": {"2": ["1"], "1": ["2"]}}

    missing_result = evaluate_planner_offline(
        "P-MISSING-EDGE", _expected(), actual_plan=missing
    )
    extra_result = evaluate_planner_offline(
        "P-EXTRA-EDGE", _expected(), actual_plan=extra
    )

    assert missing_result.status == "fail"
    assert missing_result.metrics["edge_match"] == 0.0
    assert extra_result.status == "fail"
    assert extra_result.metrics["structure_ok"] == 0.0


def test_repeated_capability_steps_can_have_a_dependency():
    expected = {
        "capabilities": ["sql.query", "sql.query"],
        "should_not_contain": ["rag.search"],
        "min_steps": 2,
        "max_steps": 2,
        "edges": [{"from": "sql.query", "to": "sql.query"}],
    }
    plan = {
        "nodes": [
            {
                "step_id": "first",
                "capability": "sql.query",
                "description": "查询订单明细",
                "params": {},
            },
            {
                "step_id": "second",
                "capability": "sql.query",
                "description": "汇总订单金额",
                "params": {},
            },
        ],
        "edges": {"second": ["first"]},
    }
    case = EvalCase(
        id="P-REPEATED-CAPABILITY",
        question="分步查询订单数据",
        module="planner",
        expected=expected,
    )

    assert validate_dataset([case]) == []
    assert evaluate_planner_offline(
        case.id, expected, actual_plan=plan
    ).status == "pass"


def test_malformed_actual_plan_fails_even_when_capability_names_match():
    plan = _valid_plan()
    plan["nodes"][0].pop("params")

    result = evaluate_planner_offline("P-MALFORMED", _expected(), actual_plan=plan)

    assert result.status == "fail"
    assert result.metrics["structure_ok"] == 0.0
    assert result.actual["plan_contract_errors"]


def test_planner_canonical_dataset_has_current_complete_contract_and_hash():
    cases = load_dataset("planner")
    errors = validate_dataset(cases)
    split_dir = Path(__file__).parents[2] / "evaluation" / "datasets" / "planner"
    manifest = json.loads((split_dir / "manifest.json").read_text(encoding="utf-8"))

    assert errors == []
    assert manifest["version"] == "2.0"
    assert manifest["case_count"] == len(cases) == 20
    assert manifest["content_hash"] == file_content_hash(split_dir / "cases.jsonl")
    assert all(isinstance(case.expected["edges"], list) for case in cases)
    assert all(case.expected["min_steps"] <= case.expected["max_steps"] for case in cases)
    assert all(case.expected["capabilities"] for case in cases)


def test_planner_dataset_rejects_missing_edges_and_step_bounds():
    case = EvalCase(
        id="P-INCOMPLETE",
        question="查询商品销售额",
        module="planner",
        expected={"capabilities": ["sql.query"], "should_not_contain": []},
    )

    errors = validate_dataset([case])

    assert any("planner min_steps" in error for error in errors)
    assert any("planner max_steps" in error for error in errors)
    assert any("planner edges" in error for error in errors)


@pytest.mark.parametrize("module", MODULE_KINDS)
def test_dataset_validator_accepts_registered_module_kinds(module):
    case = EvalCase(
        id=f"MODULE-{module}",
        question="模块注册契约样本",
        module=module,
        expected=(
            {
                "capabilities": ["sql.query"],
                "should_not_contain": [],
                "min_steps": 1,
                "max_steps": 1,
                "edges": [],
            }
            if module == "planner" else {}
        ),
    )

    assert validate_dataset([case]) == []
