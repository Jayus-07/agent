"""SSE SQL 结果卡片的边界和脱敏传输契约。"""

from decimal import Decimal
import json

from backend.orchestration.graph.events import extract_sql_result_sets, make_done_event
from backend.skills.sql.models import SQLResult as SkillSQLResult


def test_done_event_includes_bounded_successful_sql_result_without_sql_text():
    rows = [{"商品": f"商品{i}", "销量": i, "备注": "x" * 200}
            for i in range(25)]
    result = make_done_event(
        "查询成功，共 25 行。", {
            "sql-1": {
                "capability": "sql.query",
                "description": "本月商品销量",
                "status": "success",
                "output": {
                    "sql": "SELECT secret_column FROM products",
                    "columns": ["商品", "销量", "备注"],
                    "rows": rows,
                    "row_count": 25,
                },
            },
        },
        start_time=0,
    )

    card = result["data"]["sql_results"][0]
    assert card["title"] == "本月商品销量"
    assert card["columns"] == ["商品", "销量", "备注"]
    assert len(card["rows"]) == 20
    assert card["total_rows"] == 25
    assert card["rows"][0][2] == "x" * 159 + "…"
    assert card["truncated_cells"] is True
    assert "secret_column" not in str(result["data"])


def test_sql_result_extraction_ignores_failed_non_sql_and_offloaded_outputs():
    results = extract_sql_result_sets({
        "failed": {
            "capability": "sql.query", "status": "failed",
            "output": {"columns": ["x"], "rows": [[1]]},
        },
        "rag": {
            "capability": "rag.search", "status": "success",
            "output": {"columns": ["x"], "rows": [[1]]},
        },
        "ref": {
            "capability": "sql.query", "status": "success",
            "output": {"__result_ref__": {"ref_id": "taskref_1", "summary": "摘要"}},
        },
    })

    assert results == []


def test_sql_result_extraction_preserves_empty_result_and_bounds_columns():
    results = extract_sql_result_sets({
        "empty": {
            "capability": "sql.query",
            "description": "当前没有数据",
            "status": "success",
            "output": {
                "columns": [f"列{i}" for i in range(15)],
                "rows": [],
                "row_count": 0,
            },
        },
    })

    assert len(results) == 1
    assert results[0]["rows"] == []
    assert results[0]["total_rows"] == 0
    assert results[0]["total_rows_known"] is True
    assert len(results[0]["columns"]) == 12
    assert results[0]["omitted_columns"] == 3


def test_sql_result_extraction_keeps_nonzero_total_without_preview_rows():
    results = extract_sql_result_sets({
        "partial": {
            "capability": "sql.query",
            "description": "订单查询",
            "status": "success",
            "output": {
                "columns": ["订单号"],
                "rows": [],
                "total": 12,
            },
        },
    })

    assert len(results) == 1
    assert results[0]["rows"] == []
    assert results[0]["total_rows"] == 12
    assert results[0]["total_rows_known"] is True
    assert not results[0].get("payload_truncated")


def test_sql_result_extraction_marks_missing_total_as_unknown():
    results = extract_sql_result_sets({
        "unknown": {
            "capability": "sql.query",
            "status": "success",
            "output": {"columns": ["订单号"], "rows": []},
        },
    })

    assert results[0]["total_rows"] == 0
    assert results[0]["total_rows_known"] is False


def test_sql_result_extraction_marks_inconsistent_total_as_unknown():
    results = extract_sql_result_sets({
        "inconsistent": {
            "capability": "sql.query",
            "status": "success",
            "output": {
                "columns": ["订单号"],
                "rows": [{"订单号": "A"}],
                "row_count": 0,
            },
        },
    })

    assert results[0]["total_rows"] == 1
    assert results[0]["total_rows_known"] is False


def test_sql_card_uses_total_from_structured_tool_envelope():
    results = extract_sql_result_sets({
        "sql": {
            "capability": "sql.query",
            "description": "商品销量",
            "status": "success",
            "output": {
                "status": "success",
                "data": {
                    "columns": ["商品", "销量"],
                    "rows": [{"商品": "A", "销量": 10}, {"商品": "B", "销量": 8}],
                    "total": 25,
                },
            },
        },
    })

    assert len(results) == 1
    assert len(results[0]["rows"]) == 2
    assert results[0]["total_rows"] == 25


def test_sql_card_accepts_the_actual_sql_skill_output_contract_without_leaks():
    output = SkillSQLResult(
        sql="SELECT secret_margin FROM private.sales",
        tables=["private.sales"],
        columns=["月份", "销售额"],
        rows=[{"月份": "2026-11", "销售额": Decimal("1234567890123456.7800")}],
        row_count=1,
        execution_time=0.25,
    ).model_dump()

    done = make_done_event("本月销售额为 1234567890123456.7800。", {
        "sql": {
            "capability": "sql.query",
            "description": "本月销售额",
            "status": "success",
            "output": output,
            "row_count": 1,
        },
    }, start_time=0)
    card = done["data"]["sql_results"][0]

    assert card["columns"] == ["月份", "销售额"]
    assert card["rows"] == [["2026-11", "1234567890123456.7800"]]
    assert card["total_rows"] == 1
    assert card["total_rows_known"] is True
    assert "secret_margin" not in json.dumps(done, ensure_ascii=False)
    assert "private.sales" not in json.dumps(done, ensure_ascii=False)


def test_sql_card_preserves_integer_values_js_cannot_represent_exactly():
    results = extract_sql_result_sets({
        "large_integer": {
            "capability": "sql.query",
            "status": "success",
            "output": {
                "columns": ["订单号"],
                "rows": [{"订单号": 9007199254740993}],
                "row_count": 1,
            },
        },
    })

    assert results[0]["rows"] == [["9007199254740993"]]


def test_sql_card_preserves_high_precision_decimal_as_exact_text():
    amount = Decimal("1234567890123456.7800")
    step_results = {
        "decimal": {
            "capability": "sql.query",
            "status": "success",
            "output": {
                "columns": ["金额"],
                "rows": [{"金额": amount}],
                "row_count": 1,
            },
        },
    }
    results = extract_sql_result_sets(step_results)
    done = make_done_event("金额查询", step_results, start_time=0)

    assert results[0]["rows"] == [["1234567890123456.7800"]]
    assert done["data"]["sql_results"][0]["rows"] == [
        ["1234567890123456.7800"]
    ]
    encoded = json.loads(json.dumps(done))
    assert encoded["data"]["sql_results"][0]["rows"] == [
        ["1234567890123456.7800"]
    ]


def test_sql_card_caps_serialized_result_payload_size():
    columns = [f"列{i}" for i in range(12)]
    rows = [{column: "数据" * 80 for column in columns} for _ in range(20)]
    results = extract_sql_result_sets({
        f"query-{index}": {
            "capability": "sql.query",
            "status": "success",
            "output": {"columns": columns, "rows": rows, "row_count": len(rows)},
        }
        for index in range(3)
    })

    assert len(results) == 3
    assert len(json.dumps(results, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
    assert sum(len(result["rows"]) for result in results) < 60
    assert any(result.get("payload_truncated") for result in results)
