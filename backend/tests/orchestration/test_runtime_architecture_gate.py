"""Runtime Architecture V2 机械聚合器测试。

2026-10-07 随 gate 脚本演进同步：stage A 新增 RUNTIME_ARCH_V2_CONTRACT_PASS，
final 总门要求 A-G 全部阶段键 + 兼容门族 + STATE_UNKNOWN_KEY_TOTAL==0
（旧 fixture 只带 3 键，跟不上脚本即先行漂移，见收口验收报告 §11.1）。
"""

from backend.scripts.verify_runtime_arch_v2 import (
    COMPATIBILITY_GATE_KEYS,
    _STAGE_REQUIRED_KEYS,
    ready_for_final,
    ready_for_stage,
)


def _all_pass() -> dict:
    """全绿基线：A-G 阶段键 + 兼容门族 + 兼容四键 + unknown key 计数。"""
    results = {
        key: True
        for keys in _STAGE_REQUIRED_KEYS.values()
        for key in keys
    }
    results.update({key: True for key in COMPATIBILITY_GATE_KEYS})
    results.update({
        "NODE_ID_COMPAT_PASS": True,
        "SSE_COMPAT_PASS": True,
        "CHECKPOINT_COMPAT_PASS": True,
        "FRONTEND_COMPAT_PASS": True,
        "STATE_UNKNOWN_KEY_TOTAL": 0,
    })
    return results


def test_stage_gate_checks_only_its_own_keys():
    """阶段门只看本阶段键 + 全局回归 + 无行为变更——final ready 标志非阶段门前置。"""
    results = _all_pass()
    results["AGENT_RUNTIME_ARCH_V2_READY"] = False
    assert ready_for_stage(results, "A") is True


def test_stage_gate_fails_on_missing_stage_key():
    results = _all_pass()
    results.pop("RUNTIME_ARCH_V2_CONTRACT_PASS")  # stage A 新增键
    assert ready_for_stage(results, "A") is False


def test_stage_gate_fails_on_behavior_change():
    results = _all_pass()
    results["PRODUCTION_BEHAVIOR_CHANGED"] = True
    assert ready_for_stage(results, "A") is False


def test_final_ready_requires_all_positive_gates_and_no_behavior_change():
    results = _all_pass()
    assert ready_for_final(results) is True

    results["PRODUCTION_BEHAVIOR_CHANGED"] = True
    assert ready_for_final(results) is False

    # 任一兼容门缺失 → final 拒绝
    results = _all_pass()
    results.pop("SSE_COMPAT_PASS")
    assert ready_for_final(results) is False

    # unknown state key 计数非 0 → final 拒绝
    results = _all_pass()
    results["STATE_UNKNOWN_KEY_TOTAL"] = 2
    assert ready_for_final(results) is False
