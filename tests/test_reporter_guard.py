"""tests for multi_agent.reporter — BM25 兜底 + 结构化成功判断"""

import pytest
from unittest.mock import MagicMock, patch
from multi_agent.reporter import _is_step_successful, _filter_step_results


def test_is_step_successful_normal():
    """正常成功的步骤"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "这是查询结果，包含详细数据和具体分析内容，输出足够长",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is True


def test_is_step_successful_empty_result():
    """空结果不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "(无结果)",
        "is_empty": True,
        "error_type": None,
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_with_error_type():
    """有 error_type 标记不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "部分数据",
        "error_type": "timeout",
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_failed_status():
    """failed 状态不算成功"""
    result = {
        "step_id": "1",
        "status": "failed",
        "output": "错误输出",
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_short_output():
    """过短的输出不算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "短",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is False


def test_is_step_successful_min_length():
    """刚好 20 字符以上算成功"""
    result = {
        "step_id": "1",
        "status": "success",
        "output": "这是一个刚好超过二十个字符的输出内容用于测试",
        "is_empty": False,
        "error_type": None,
    }
    assert _is_step_successful(result) is True


@patch("multi_agent.reporter._check_reranker_available")
def test_filter_falls_back_to_bm25(mock_check):
    """CrossEncoder 不可用时降级为 BM25"""
    mock_check.return_value = False

    step_results = {
        "1": {
            "step_id": "1",
            "capability": "search_knowledge",
            "status": "success",
            "output": "请假需要提前三天提交申请，部门经理审批后生效。",
            "description": "检索请假流程",
        },
    }
    question = "请假流程是什么"

    # BM25 降级不应崩溃，返回过滤后的结果
    result = _filter_step_results(step_results, question)
    assert "1" in result  # 应保留结果
    # BM25 过滤可能保留或折叠，取决于相关性分数
    sr = result["1"]
    assert sr.get("status") == "success"


def test_context_threshold_configurable():
    """阈值来自 config（默认值合理）"""
    from multi_agent.reporter import _CONTEXT_RELEVANCE_THRESHOLD
    assert 0.0 < _CONTEXT_RELEVANCE_THRESHOLD < 1.0
