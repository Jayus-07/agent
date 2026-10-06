"""Runtime Architecture V2 机械聚合器测试。"""

from backend.scripts.verify_runtime_arch_v2 import (
    ready_for_final,
    ready_for_stage,
)


def test_non_final_stage_does_not_require_final_ready_flag():
    results = {
        "CONTRACT_V2_PASS": True,
        "PRODUCTION_BEHAVIOR_CHANGED": False,
        "GLOBAL_REGRESSION_PASS": True,
        "AGENT_RUNTIME_ARCH_V2_READY": False,
    }

    assert ready_for_stage(results, "A") is True


def test_final_ready_requires_all_positive_gates_and_no_behavior_change():
    results = {
        "CONTRACT_V2_PASS": True,
        "PRODUCTION_BEHAVIOR_CHANGED": False,
        "GLOBAL_REGRESSION_PASS": True,
    }

    assert ready_for_final(results) is True
    results["PRODUCTION_BEHAVIOR_CHANGED"] = True
    assert ready_for_final(results) is False
