"""SQL 观测埋点回归测试：慢查询 WARNING / 执行异常 ERROR / 参数值脱敏 / 幂等。

用 sqlite 内存库验证事件挂接行为（不依赖 PG；两生产引擎的挂接点由
模块 import 即生效，事件逻辑与方言无关）。monkeypatch 把慢查询阈值
调 0 = 全记模式，避免依赖语句真实耗时。"""
import logging

import pytest
from sqlalchemy import create_engine, text

from backend.shared import sql_obs
from backend.shared.sql_obs import install_sql_observation


@pytest.fixture()
def engine(monkeypatch):
    # 阈值 0 = 每条语句都记（测试不依赖真实耗时）
    monkeypatch.setattr(sql_obs, "_SLOW_MS", 0)
    eng = create_engine("sqlite:///:memory:")
    yield eng
    eng.dispose()


def test_slow_query_logged(engine, caplog):
    """阈值 0（全记模式）下执行语句应产出 [SQL] slow WARNING，含引擎名与耗时。"""
    install_sql_observation(engine, engine_name="test")
    with caplog.at_level(logging.WARNING):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    slow_lines = [r for r in caplog.records if "[SQL]" in r.getMessage() and "slow=" in r.getMessage()]
    assert slow_lines, "慢查询（阈值 0 = 全记）应产出 WARNING 日志"
    msg = slow_lines[0].getMessage()
    assert "engine=test" in msg
    assert "slow=" in msg


def test_error_logged_with_reason(engine, caplog):
    """执行错误应产出 [SQL] error ERROR 日志，含底层异常类型与语句文本。"""
    install_sql_observation(engine, engine_name="test")
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception):
            with engine.connect() as conn:
                conn.execute(text("SELECT * FROM __no_such_table__"))
    err_lines = [r for r in caplog.records if "[SQL]" in r.getMessage() and "error=" in r.getMessage()]
    assert err_lines, "执行异常应产出 ERROR 日志"
    msg = err_lines[0].getMessage()
    assert "__no_such_table__" in msg, "语句文本应被记录（占位符形式不含参数值）"


def test_param_values_never_logged(engine, caplog):
    """绑定参数值不得进入日志（敏感红线）：日志只允许语句文本与参数个数。"""
    install_sql_observation(engine, engine_name="test")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t_obs (v TEXT)"))
    with caplog.at_level(logging.WARNING):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO t_obs (v) VALUES (:v)"),
                {"v": "SECRET-VALUE-xyz"},
            )
    for r in caplog.records:
        assert "SECRET-VALUE-xyz" not in r.getMessage(), "参数值泄露进日志"
    slow_lines = [r for r in caplog.records if "[SQL]" in r.getMessage() and "slow=" in r.getMessage()]
    assert slow_lines, "INSERT 语句应在全记模式下产出日志"


def test_install_idempotent(engine, caplog):
    """重复 install 不重复挂监听：同一条语句只记一行 [SQL] slow。"""
    install_sql_observation(engine, engine_name="test")
    install_sql_observation(engine, engine_name="test")
    with caplog.at_level(logging.WARNING):
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    slow_lines = [r for r in caplog.records if "[SQL]" in r.getMessage() and "slow=" in r.getMessage()]
    assert len(slow_lines) == 1, f"幂等 install 后单条语句应只记 1 行，实际 {len(slow_lines)}"


def test_threshold_clamped(monkeypatch):
    """阈值解析防呆：环境变量越界值被夹紧到 0-60000。"""
    import os

    import importlib

    monkeypatch.setenv("SQL_SLOW_LOG_MS", "999999")
    mod = importlib.reload(sql_obs)
    assert mod._SLOW_MS == 60000
    monkeypatch.setenv("SQL_SLOW_LOG_MS", "-5")
    mod = importlib.reload(sql_obs)
    assert mod._SLOW_MS == 0
    monkeypatch.delenv("SQL_SLOW_LOG_MS")
    mod = importlib.reload(sql_obs)
    assert mod._SLOW_MS == 300
    # 恢复默认（reload 后模块级 _INSTALLED 清空无碍——生产进程只 import 一次）
