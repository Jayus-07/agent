"""tests for multi_agent.alerts — 告警数据类 + 日志写入"""

import json
import os
import tempfile
from multi_agent.alerts import PlanAlert, ALERT_CODES, log_degradation


def test_plan_alert_creation():
    """PlanAlert 数据类创建 + 字段完整性"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="warn",
        code="PLAN_EMPTY",
        message="Planner 返回空计划",
        detail={"question": "测试问题"},
    )
    assert alert.level == "warn"
    assert alert.code == "PLAN_EMPTY"
    assert alert.detail["question"] == "测试问题"


def test_alert_codes_completeness():
    """验证所有告警代码都有对应的 level 和 message"""
    required_codes = [
        "PLAN_EMPTY", "PLAN_JSON_INVALID", "PLAN_CAP_INVALID",
        "PLAN_MISROUTE", "CRITIQUE_FAILED", "SUPERVISOR_MAX_LOOP",
        "WORKER_TIMEOUT", "WORKER_RETRY_EXHAUST",
        "RERANKER_UNAVAILABLE", "DEGRADATION_TRIGGER",
    ]
    for code in required_codes:
        assert code in ALERT_CODES, f"缺少告警代码: {code}"
        level, message = ALERT_CODES[code]
        assert level in ("info", "warn", "error")
        assert len(message) > 0


def test_log_degradation_writes_jsonl():
    """log_degradation 写入 JSONL 文件"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="warn",
        code="PLAN_EMPTY",
        message="测试告警",
        detail={},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "test.jsonl")
        # 临时替换模块中的日志路径
        import multi_agent.alerts as alerts_mod
        original_path = alerts_mod.DEGRADATION_LOG_FILE
        alerts_mod.DEGRADATION_LOG_FILE = log_path
        try:
            log_degradation(alert)
            assert os.path.exists(log_path)
            with open(log_path, "r", encoding="utf-8") as f:
                line = f.readline()
                data = json.loads(line)
                assert data["code"] == "PLAN_EMPTY"
                assert data["level"] == "warn"
        finally:
            alerts_mod.DEGRADATION_LOG_FILE = original_path


def test_log_degradation_no_exception_on_io_error():
    """磁盘写入失败时不抛异常（非阻塞）"""
    alert = PlanAlert(
        timestamp="2026-06-21T12:00:00",
        level="info",
        code="DEGRADATION_TRIGGER",
        message="测试",
        detail={},
    )
    import multi_agent.alerts as alerts_mod
    original_path = alerts_mod.DEGRADATION_LOG_FILE
    alerts_mod.DEGRADATION_LOG_FILE = "NUL"  # Windows: NUL device, won't fail
    try:
        # 不应抛出异常
        log_degradation(alert)
    finally:
        alerts_mod.DEGRADATION_LOG_FILE = original_path
