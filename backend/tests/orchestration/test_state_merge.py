# -*- coding: utf-8 -*-
"""test_state_merge.py — 并行 Send step_results 合并回归测试（2026-09-15）

背景：BaseSkill.execute 返回 dispatch 时刻的全量 step_results 快照（其他
步骤还是 running 旧视图）。旧 reducer 朴素 dict.update(right) 让后返回的
分支把先返回分支的真实成果覆盖成过期快照——实测 sql success 被 rag 分支
的 running 快照覆盖，reporter 全判失败、用户拿到空回答。

修复：按状态进展优先级逐步骤择优（success > failed/skipped > running >
pending），同优先级取 right（最新写入）。
"""
from backend.orchestration.state import _merge_step_results


def _step(status, output=None, error=None):
    return {"status": status, "output": output, "error": error}


def test_terminal_result_survives_stale_running_snapshot():
    """后返回分支的 running 旧快照不得覆盖先返回分支的 success 成果。"""
    left = {"1": _step("success", output={"rows": [1, 2]})}   # sql 分支真实成果
    right = {"1": _step("running"),                            # rag 分支携带的旧快照
             "2": _step("success", output="有效内容")}
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "success"
    assert merged["1"]["output"] == {"rows": [1, 2]}
    assert merged["2"]["status"] == "success"


def test_real_failure_not_lost_to_stale_running():
    """先返回分支的真实 failed 结果不被后返回分支的 running 快照覆盖。"""
    left = {"1": _step("failed", error="table not found")}
    right = {"1": _step("running"), "2": _step("success", output="ok")}
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "failed"
    assert merged["1"]["error"] == "table not found"
    assert merged["2"]["status"] == "success"


def test_retry_success_beats_earlier_failure():
    """降级重试：重新派发后的 success 胜过更早的 failed。"""
    left = {"1": _step("failed", error="第一次失败")}
    right = {"1": _step("success", output="重试成功")}
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "success"
    assert merged["1"]["output"] == "重试成功"


def test_same_status_right_wins():
    """同优先级（同为终态）取最新写入。"""
    left = {"1": _step("success", output="旧")}
    right = {"1": _step("success", output="新")}
    assert _merge_step_results(left, right)["1"]["output"] == "新"


def test_empty_sides():
    assert _merge_step_results({}, {"1": _step("success")})["1"]["status"] == "success"
    assert _merge_step_results({"1": _step("success")}, {})["1"]["status"] == "success"
    assert _merge_step_results({}, {}) == {}


def test_stale_success_not_downgraded_by_late_running():
    """反向场景：右分支对某步骤只有 running 快照，左分支已是 failed——
    failed(3) > running(1)，真实失败结果保留。"""
    left = {"1": _step("failed", error="x"), "2": _step("success", output="r")}
    right = {"1": _step("running"), "2": _step("running")}
    merged = _merge_step_results(left, right)
    assert merged["1"]["status"] == "failed"
    assert merged["2"]["status"] == "success"
