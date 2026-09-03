"""test_account_service.py — AccountService 单元测试 (mock DB)"""
from unittest.mock import patch

import pytest

from backend.customer_service.errors import AuthenticationError, DatabaseError
from backend.customer_service.service.account_service import (
    AccountService,
)
from backend.sql.sql_result import SQLResult

_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"


def _make_sql_result(rows):
    cols = list(rows[0].keys()) if rows else []
    return SQLResult.success(rows, cols, sql="test") if rows else SQLResult(
        status="no_data", rows=[], columns=cols, sql_text="test",
    )


@pytest.fixture
def service():
    return AccountService()


class TestQueryAccount:

    @patch(_EXEC_PATCH)
    def test_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 42, "name": "张三", "gender": "男",
            "level": "金卡", "register_time": "2025-01-15 10:00:00",
        }])
        result = service.query_account(user_id="42")
        assert result.user_id == "42"
        assert result.name == "张三"
        assert result.level == "金卡"

    @patch(_EXEC_PATCH)
    def test_not_found(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([])
        with pytest.raises(AuthenticationError):
            service.query_account(user_id="999")

    @patch(_EXEC_PATCH)
    def test_db_error(self, mock_exec, service):
        mock_exec.return_value = SQLResult.failed(
            status="failed", error="connection lost",
        )
        with pytest.raises(DatabaseError):
            service.query_account(user_id="42")

    def test_anonymous_user(self, service):
        with pytest.raises(AuthenticationError):
            service.query_account(user_id="anonymous")

    def test_empty_user_id(self, service):
        with pytest.raises(AuthenticationError):
            service.query_account(user_id="")

    @patch(_EXEC_PATCH)
    def test_query_binds_user_id(self, mock_exec, service):
        mock_exec.return_value = _make_sql_result([{
            "id": 42, "name": "张三", "gender": "男",
            "level": "金卡", "register_time": "2025-01-15",
        }])
        service.query_account(user_id="42")
        params = mock_exec.call_args.kwargs.get("params", {})
        assert params["user_id"] == "42"
