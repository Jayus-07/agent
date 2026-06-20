"""tests for multi_agent.workers.base — Worker 超时 + 重试 + 错误分类"""

import asyncio
import pytest
from unittest.mock import MagicMock

from multi_agent.workers.base import (
    execute_with_retry,
    _is_retryable,
    UNRETRYABLE_PATTERNS,
    DEFAULT_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    RETRY_BACKOFF_BASE,
)


def test_retryable_errors():
    """网络/超时类错误可重试"""
    assert _is_retryable("connection timeout") is True
    assert _is_retryable("timeout error") is True
    assert _is_retryable("unexpected error") is True


def test_unretryable_errors():
    """参数/语法类错误不可重试"""
    for pattern in UNRETRYABLE_PATTERNS:
        assert _is_retryable(pattern) is False, f"'{pattern}' should be unretryable"
    assert _is_retryable("Error: no such table: users") is False
    assert _is_retryable("column not found: name") is False


def test_retryable_case_insensitive():
    """错误分类大小写不敏感"""
    assert _is_retryable("No Such Table: employees") is False
    assert _is_retryable("SYNTAX ERROR near SELECT") is False


@pytest.mark.asyncio
async def test_worker_success_first_try():
    """Worker 首次成功执行"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {"question": "test"},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.return_value = "查询结果"

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "success"
    assert sr["output"] == "查询结果"
    assert sr["retries"] == 0


@pytest.mark.asyncio
async def test_worker_retry_then_success():
    """Worker 第一次失败、第二次成功"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = [Exception("timeout"), "最终结果"]

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "success"
    assert sr["output"] == "最终结果"
    assert sr["retries"] == 1  # 第二次尝试成功，retries=1


@pytest.mark.asyncio
async def test_worker_retry_exhausted():
    """Worker 重试耗尽，最终失败"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = Exception("connection timeout")

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    assert "connection timeout" in sr.get("error", "")


@pytest.mark.asyncio
async def test_worker_no_retry_on_unretryable():
    """不可重试错误不重试，直接失败"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "query_database",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    mock_tool.invoke.side_effect = Exception("no such table: nonexistent")

    result = await execute_with_retry(state, mock_tool)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    # 不可重试，invoke 应该只被调用 1 次
    assert mock_tool.invoke.call_count == 1


@pytest.mark.asyncio
async def test_worker_timeout():
    """Worker 超时触发 TimeoutError"""
    state = {
        "current_step_id": "1",
        "plan": {
            "nodes": {
                "1": {
                    "step_id": "1",
                    "capability": "search_knowledge",
                    "description": "测试",
                    "params": {},
                }
            },
            "edges": {},
        },
        "step_results": {},
    }

    mock_tool = MagicMock()
    # 模拟超时：阻塞的同步函数（asyncio.to_thread 执行在 thread pool）
    # sleep 时长必须超过 timeout=0.1s 以触发 TimeoutError
    import time
    def slow_invoke(*args, **kwargs):
        time.sleep(2)  # 超过 timeout=0.1，但足够短让线程快速退出
        return "too late"

    mock_tool.invoke = slow_invoke

    # 用一个很短的超时来测试
    result = await execute_with_retry(state, mock_tool, max_retries=0, timeout=0.1)
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    assert "超时" in sr.get("error", "").lower() or "timeout" in sr.get("error", "").lower()


@pytest.mark.asyncio
async def test_worker_missing_step_id():
    """current_step_id 为空时返回空 dict"""
    state = {
        "current_step_id": None,
        "plan": {"nodes": {}, "edges": {}},
        "step_results": {},
    }
    mock_tool = MagicMock()
    result = await execute_with_retry(state, mock_tool)
    assert result == {}


def test_backoff_constants():
    """退避常量存在且合理"""
    assert RETRY_BACKOFF_BASE >= 1.0
    assert DEFAULT_TIMEOUT >= 30
    assert DEFAULT_MAX_RETRIES >= 1
