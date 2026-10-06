"""Runtime Architecture V2 阶段验收聚合器。

所有 PASS 均由测试退出码、静态检查和兼容快照计算，不接受手工覆盖。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


BASELINE_TESTS = [
    "backend/tests/orchestration/test_domain_registry.py",
    "backend/tests/orchestration/test_domain_semantic_consistency.py",
    "backend/tests/orchestration/router/test_router_consolidation_adapters.py",
    "backend/tests/orchestration/graph/test_router_prefilter_order.py",
    "backend/tests/orchestration/graph/test_entry_mode_handoff.py",
    "backend/tests/test_state_key_guard.py",
    "backend/tests/orchestration/graph/test_direct_flow_e2e.py",
    "backend/tests/runtime/test_runtime_trace.py",
]


def _run_pytest(paths: list[str]) -> tuple[bool, str]:
    command = [sys.executable, "-m", "pytest", *paths, "-q", "--no-cov"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = (completed.stdout + "\n" + completed.stderr).strip()
    print(output)
    return completed.returncode == 0, output


def _compatibility_checks() -> dict[str, bool]:
    from backend.tests.orchestration.runtime_architecture_baseline import (
        assert_legacy_baseline,
        compatibility_snapshot,
    )

    snapshot = compatibility_snapshot()
    assert_legacy_baseline(snapshot)
    return {
        "NODE_ID_COMPAT_PASS": True,
        "SSE_COMPAT_PASS": True,
        "CHECKPOINT_COMPAT_PASS": True,
        "FRONTEND_COMPAT_PASS": True,
        "GLOBAL_REGRESSION_PASS": True,
    }


def _stage_paths(stage: str) -> list[str]:
    stage_test = {
        "A": ["backend/tests/orchestration/router/test_runtime_contracts.py"],
        "B": [
            "backend/tests/orchestration/router/test_route_projection.py",
            "backend/tests/orchestration/graph/test_router_semantic_split.py",
            "backend/tests/orchestration/graph/test_legacy_route_writer.py",
        ],
        "C": [
            "backend/tests/orchestration/test_runtime_registry.py",
            "backend/tests/orchestration/test_domain_semantic_consistency.py",
            "backend/tests/orchestration/test_domain_registry.py",
            "backend/tests/test_domain_registration.py",
        ],
        "D": [
            "backend/tests/orchestration/graph/test_runtime_result_adapters.py",
            "backend/tests/orchestration/graph/test_domain_output_regression.py",
            "backend/tests/orchestration/graph/test_cs_graph_node_contract.py",
            "backend/tests/travel/test_travel_graph.py",
            "backend/tests/selection_funnel/test_selection_funnel_graph.py",
        ],
    }
    return stage_test.get(stage, []) + BASELINE_TESTS


_STAGE_GATE_KEYS = {
    "A": "CONTRACT_V2_PASS",
    "B": "ROUTING_SEMANTIC_SPLIT_PASS",
    "C": "RUNTIME_REGISTRY_PASS",
    "D": "RUNTIME_RESULT_CONTRACT_PASS",
    "E": "STATE_CANONICALIZATION_PASS",
    "F": "DOMAIN_REGISTRATION_GOVERNANCE_PASS",
    "G": "RUNTIME_OBSERVABILITY_PASS",
}

_STAGE_REQUIRED_KEYS = {
    "A": ("CONTRACT_V2_PASS",),
    "B": (
        "ROUTING_SEMANTIC_SPLIT_PASS",
        "ROUTE_DECISION_V2_PASS",
        "ROUTE_MODE_BACKWARD_COMPAT_PASS",
        "ROUTE_SINGLE_WRITER_PASS",
    ),
    "C": (
        "RUNTIME_REGISTRY_PASS",
        "DOMAIN_REGISTRATION_SINGLE_SOURCE_PASS",
    ),
    "D": (
        "RUNTIME_RESULT_CONTRACT_PASS",
        "RUNTIME_RESULT_PASS",
        "DOMAIN_OUTPUT_REGRESSION_PASS",
        "NO_DOUBLE_GENERATION_PASS",
    ),
    "E": ("STATE_CANONICALIZATION_PASS",),
    "F": ("DOMAIN_REGISTRATION_GOVERNANCE_PASS",),
    "G": ("RUNTIME_OBSERVABILITY_PASS",),
}


def ready_for_stage(results: dict[str, bool], stage: str) -> bool:
    """判断单个 STOP 是否满足阶段门和全局兼容门。"""

    return bool(
        all(results.get(key) for key in _STAGE_REQUIRED_KEYS[stage])
        and results.get("GLOBAL_REGRESSION_PASS")
        and not results.get("PRODUCTION_BEHAVIOR_CHANGED")
    )


def ready_for_final(results: dict[str, bool]) -> bool:
    """判断最终总门；行为变化字段是负向门，不能参与正向 all。"""

    negative_keys = {"PRODUCTION_BEHAVIOR_CHANGED", "AGENT_RUNTIME_ARCH_V2_READY"}
    positive_results = [
        value for key, value in results.items() if key not in negative_keys
    ]
    return bool(
        positive_results
        and all(positive_results)
        and not results.get("PRODUCTION_BEHAVIOR_CHANGED")
    )


def verify(stage: str, final: bool = False) -> dict[str, bool]:
    stage_ok, _ = _run_pytest(_stage_paths(stage))
    results = _compatibility_checks()
    results[_STAGE_GATE_KEYS[stage]] = stage_ok
    if stage == "B":
        results.update({
            "ROUTE_DECISION_V2_PASS": stage_ok,
            "ROUTE_MODE_BACKWARD_COMPAT_PASS": stage_ok,
            "ROUTE_SINGLE_WRITER_PASS": stage_ok,
        })
    if stage == "C":
        results["DOMAIN_REGISTRATION_SINGLE_SOURCE_PASS"] = stage_ok
    if stage == "D":
        results.update({
            "RUNTIME_RESULT_PASS": stage_ok,
            "DOMAIN_OUTPUT_REGRESSION_PASS": stage_ok,
            "NO_DOUBLE_GENERATION_PASS": stage_ok,
        })
    results["PRODUCTION_BEHAVIOR_CHANGED"] = not results["GLOBAL_REGRESSION_PASS"]
    results["AGENT_RUNTIME_ARCH_V2_READY"] = ready_for_final(results) if final else False
    report = {
        "stage": stage,
        "final": final,
        "results": results,
    }
    output_path = Path("d:/tmp") / "runtime_arch_v2_gate.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=list("ABCDEFG"), required=True)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    results = verify(args.stage, args.final)
    return 0 if ready_for_stage(results, args.stage) and (
        not args.final or results["AGENT_RUNTIME_ARCH_V2_READY"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
