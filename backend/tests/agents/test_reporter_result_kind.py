"""reporter._result_kind / _append_degraded_disclosure 契约测试。

2026-10-25 回归：Tool Governance 去重缓存（dedupe_cache）是读操作的确定性
缓存复用（同一用户短窗口重复提问直接复用成功结果），不是降级供数——
此前 fallback_used 一律判 degraded，导致「同一问题 30s 内二次提问」的
正确答案被误挂「已使用降级结果，信息可能不完整」警告。
"""
from backend.agents.reporter.reporter import (
    _append_degraded_disclosure,
    _failure_text,
    _result_kind,
    generate_final_answer,
)


def _sr(**extra):
    base = {
        "step_id": "direct_1",
        "capability": "rag.search",
        "status": "success",
        "tool_status": "success",
        "output": "答案正文" + "x" * 100,
    }
    base.update(extra)
    return base


def test_dedupe_cache_hit_is_not_degraded():
    """缓存复用的成功结果不得判降级（核心回归）。"""
    assert _result_kind(_sr(fallback_used="dedupe_cache")) == "success"


def test_real_fallback_provider_still_degraded():
    """真降级供数（熔断/舱壁/预算旁路等失败路径外的新型 fallback）保持降级语义。"""
    assert _result_kind(_sr(fallback_used="circuit_breaker")) == "degraded"
    assert _result_kind(_sr(fallback_used="backup_provider")) == "degraded"


def test_degraded_flag_and_tool_status_unchanged():
    assert _result_kind(_sr(degraded=True)) == "degraded"
    assert _result_kind(_sr(tool_status="degraded")) == "degraded"


def test_rag_service_failure_is_not_reported_as_missing_evidence():
    result = _sr(
        status="failed", tool_status="unavailable", error_type="network",
        output=None,
    )
    assert _failure_text(result) == "知识库检索服务暂时不可用，请稍后重试。"


def test_sql_validation_failure_is_not_reported_as_bad_request():
    result = _sr(
        capability="sql.query",
        status="failed",
        tool_status="invalid_request",
        error_type="validation_error",
        output=None,
    )

    assert _result_kind(result) == "sql_query_rejected"
    assert "请求参数有误" not in _failure_text(result)
    assert "数据库查询未能通过当前规则校验" in _failure_text(result)


def test_disclosure_not_appended_for_dedupe_cache_hit():
    """透传答案不因缓存命中附加降级警告。"""
    answer = "正常答案"
    out = _append_degraded_disclosure(
        answer, {"direct_1": _sr(fallback_used="dedupe_cache")})
    assert out == answer


def test_disclosure_still_appended_for_real_degradation():
    out = _append_degraded_disclosure(
        "正常答案", {"direct_1": _sr(degraded=True)})
    assert "降级" in out and out != "正常答案"


def test_sql_table_keeps_delimited_and_multiline_values_in_one_cell():
    output = {
        "columns": ["商品", "备注", "金额"],
        "rows": [{
            "商品": "A|B",
            "备注": "第一行\n第二行 *重点* <标签>",
            "金额": None,
        }],
        "row_count": 1,
    }
    rendered = generate_final_answer(
        "查询商品结果",
        {"s1": {
            "capability": "sql.query",
            "description": "查询结果",
            "status": "success",
            "output": output,
        }},
        context_filter=False,
        allow_llm=False,
    )

    assert "查询成功，共 1 行。" in rendered
    assert "| A\\|B | 第一行 / 第二行 \\*重点\\* &lt;标签&gt; | — |" in rendered


def test_sql_empty_rows_without_confirmed_count_are_not_called_no_matches():
    result = _sr(
        capability="sql.query",
        description="订单查询",
        output={
            "status": "success",
            "data": {"columns": ["订单号"], "rows": []},
        },
    )

    assert _result_kind(result) == "success"
    answer = generate_final_answer(
        "查一下订单", {"s1": result}, context_filter=False, allow_llm=False,
    )

    assert "无法确认是否有匹配记录" in answer
    assert "没有符合条件的数据（0 行）" not in answer


def test_sql_empty_rows_with_explicit_empty_flag_are_called_no_matches():
    result = _sr(
        capability="sql.query",
        description="订单查询",
        is_empty=True,
        output={"columns": ["订单号"], "rows": []},
    )

    assert _result_kind(result) == "no_data"
    answer = generate_final_answer(
        "查一下订单", {"s1": result}, context_filter=False, allow_llm=False,
    )

    assert "没有符合条件的数据（0 行）" in answer


def test_sql_empty_rows_with_explicit_zero_count_are_called_no_matches():
    result = _sr(
        capability="sql.query",
        description="订单查询",
        output={"columns": ["订单号"], "rows": [], "row_count": 0},
    )

    assert _result_kind(result) == "no_data"
    answer = generate_final_answer(
        "查一下订单", {"s1": result}, context_filter=False, allow_llm=False,
    )

    assert "没有符合条件的数据（0 行）" in answer


def test_sql_empty_rows_with_positive_count_disclose_missing_details():
    result = _sr(
        capability="sql.query",
        description="订单查询",
        output={
            "status": "success",
            "data": {"columns": ["订单号"], "rows": [], "total": 4},
        },
    )

    answer = generate_final_answer(
        "查一下订单", {"s1": result}, context_filter=False, allow_llm=False,
    )

    assert "已确认有 4 行结果，但当前没有可展示的明细" in answer
    assert "没有符合条件的数据（0 行）" not in answer


def test_sql_empty_rows_with_conflicting_count_metadata_remain_unconfirmed():
    result = _sr(
        capability="sql.query",
        description="订单查询",
        output={
            "columns": ["订单号"],
            "rows": [],
            "row_count": 0,
            "total": 4,
        },
    )

    answer = generate_final_answer(
        "查一下订单", {"s1": result}, context_filter=False, allow_llm=False,
    )

    assert "无法确认是否有匹配记录" in answer
    assert "没有符合条件的数据（0 行）" not in answer
