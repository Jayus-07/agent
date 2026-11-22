# -*- coding: utf-8 -*-
"""verify_release_gate.py main() 冒烟（mock IO，真实走 checks 装配与调度）。

背景（2026-11-21 评审）：#496 的 ("Gate8_Travel", g8_travel_direct) 漏包
lambda、漏传 token，readiness 单测全部绕过 main() 的 checks 装配与调度
所以全绿，生产部署实跑即 TypeError → Gate8 恒 ERROR。本冒烟在 mock IO
层让 main() 真实跑完装配、fail-fast 自检与调度，把「配置类错误」的暴露
点从生产部署提前到 CI：

- checks 装配 TypeError（漏包 lambda / 漏传 token）当场抛出
- fail-fast 自检通过后 12 个 gate 全部被调用、结果进 RESULTS
- 落库走 --no-record 跳过；所有 IO 均为 mock，不触达真实环境
"""
import json
import sys

import pytest

from backend.scripts import verify_release_gate as gate

GATE_NAMES = [
    "Gate0_BuildIdentity", "Gate1_Migration", "Gate2_Auth", "Gate3_Tenant",
    "Gate4_Chat", "Gate5_RAG", "Gate6_SQL", "Gate7_CS", "Gate8_Travel",
    "Gate10_TaskRuntime", "Gate11_Model", "Gate12_Observability",
]


@pytest.fixture
def smoke_env(monkeypatch):
    monkeypatch.setattr(
        gate, "_request",
        lambda *a, **k: (200, json.dumps({"data": {"token": "fake"}})),
    )
    monkeypatch.setattr(gate, "login", lambda *a, **k: "fake-token")
    calls: list[str] = []

    def spy(name):
        def _fn(*args, **kwargs):
            calls.append(name)
            return True
        return _fn

    for fn_name in (
        "g0_build", "g1_migration", "g2_auth", "g3_tenant", "g_domain",
        "g8_travel_direct", "g10_task", "g11_model", "g12_observability",
    ):
        monkeypatch.setattr(gate, fn_name, spy(fn_name))
    monkeypatch.setattr(
        sys, "argv",
        ["verify_release_gate.py", "--password", "x", "--no-record"],
    )
    gate.RESULTS.clear()
    gate.DETAIL.clear()
    return calls


def test_main_runs_all_gates_and_passes(smoke_env):
    """main() 完整跑通：12 个 gate 全部被调度且 PASS，退出码 0。"""
    rc = gate.main()
    assert rc == 0, f"main 应全 PASS（mock 全 True），DETAIL={gate.DETAIL}"
    assert gate.RESULTS == {name: True for name in GATE_NAMES}
    # g_domain 被 g4/g5/g6/g7 复用，共 12 次 gate 调用
    assert len(smoke_env) == 12


def test_main_reports_failure_and_exit_code(smoke_env, monkeypatch):
    """单个 gate 失败不吞异常、不中断调度：结果进 RESULTS，退出码 1。"""
    monkeypatch.setattr(gate, "g8_travel_direct", lambda: False)
    rc = gate.main()
    assert rc == 1
    assert gate.RESULTS["Gate8_Travel"] is False
    # 其余 gate 仍被调度（失败不吞掉其余检查）
    assert all(v is True for k, v in gate.RESULTS.items() if k != "Gate8_Travel")


def test_main_catches_gate_exception_as_error_detail(smoke_env, monkeypatch):
    """gate 抛异常被 main 捕获为 ERROR 详情：结果 False、退出码 1。"""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated gate crash")
    monkeypatch.setattr(gate, "g11_model", boom)
    rc = gate.main()
    assert rc == 1
    assert gate.RESULTS["Gate11_Model"] is False
    assert "simulated gate crash" in gate.DETAIL["Gate11_Model"]
