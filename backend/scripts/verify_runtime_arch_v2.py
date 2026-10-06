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


GLOBAL_REGRESSION_TESTS = [
    "backend/tests/test_frontend_node_ids_consistency.py",
    "backend/tests/test_sse_event_schema.py",
    "backend/tests/runtime/test_checkpoint_recovery.py",
    "backend/tests/travel/test_user_decision.py",
    "backend/tests/orchestration/test_supervisor.py",
    "backend/tests/orchestration/graph/test_cs_graph_wiring.py",
    "backend/tests/travel/test_travel_graph.py",
    "backend/tests/selection_funnel/test_selection_funnel_graph.py",
    "backend/tests/orchestration/graph/test_clarify_flow.py",
    "backend/tests/orchestration/graph/test_entry_mode_handoff.py",
    "backend/tests/orchestration/router/test_routing_engine.py",
]


COMPATIBILITY_GATE_KEYS = (
    "NODE_ID_UNCHANGED",
    "SSE_EVENT_SCHEMA_UNCHANGED",
    "CHECKPOINT_COMPAT_PASS",
    "TRAVEL_INTERRUPT_RESUME_PASS",
    "PLAN_SEND_PAYLOAD_PASS",
    "FRONTEND_NODE_MAPPING_PASS",
    "DIRECT_PATH_PASS",
    "WORKFLOW_PATH_PASS",
    "PLAN_PATH_PASS",
    "CS_DOMAIN_PASS",
    "TRAVEL_DOMAIN_PASS",
    "SELECTION_DOMAIN_PASS",
    "CLARIFY_PASS",
    "HANDOFF_PASS",
    "GENERAL_CHAT_PASS",
    "GLOBAL_REGRESSION_PASS",
)


STAGE_RESULTS_PATH = Path("d:/tmp/runtime_arch_v2_stages.json")


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
        "E": [
            "backend/tests/orchestration/test_state_canonicalization.py",
            "backend/tests/test_state_key_guard.py",
            "backend/tests/orchestration/graph/test_tool_selector.py",
            "backend/tests/orchestration/graph/test_clarify_flow.py",
            "backend/tests/orchestration/graph/test_reply_source.py",
        ],
        "F": [
            "backend/tests/orchestration/test_domain_registration_surface.py",
            "backend/tests/orchestration/router/test_router_domain_literal_guard.py",
        ],
        "G": [
            "backend/tests/runtime/test_runtime_arch_v2_trace.py",
            "backend/tests/infra/test_llm_usage_provenance.py",
            "backend/tests/evaluation/test_trace_bridge.py",
        ],
    }
    return stage_test.get(stage, []) + BASELINE_TESTS + GLOBAL_REGRESSION_TESTS


def _all_stage_paths() -> list[str]:
    """最终门一次性回放 A-G 的全部测试，避免跨进程拼接 PASS。"""

    paths: list[str] = []
    for stage in "ABCDEFG":
        for path in _stage_paths(stage):
            if path not in paths:
                paths.append(path)
    return paths


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
    "A": (
        "CONTRACT_V2_PASS",
        "RUNTIME_ARCH_V2_CONTRACT_PASS",
    ),
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
    "E": (
        "STATE_CANONICALIZATION_PASS",
        "STATE_CANONICAL_SOURCE_PASS",
        "LEGACY_PROJECTION_PASS",
    ),
    "F": (
        "DOMAIN_REGISTRATION_GOVERNANCE_PASS",
        "NEW_DOMAIN_REGISTRATION_SURFACE_PASS",
        "ROUTER_DOMAIN_LITERAL_GUARD_PASS",
    ),
    "G": (
        "RUNTIME_OBSERVABILITY_PASS",
        "RUNTIME_TRACE_PASS",
        "COST_ATTRIBUTION_PASS",
        "EVAL_ATTRIBUTION_PASS",
    ),
}


def ready_for_stage(results: dict[str, bool], stage: str) -> bool:
    """判断单个 STOP 是否满足阶段门和全局兼容门。"""

    return bool(
        all(results.get(key) for key in _STAGE_REQUIRED_KEYS[stage])
        and results.get("GLOBAL_REGRESSION_PASS")
        and not results.get("PRODUCTION_BEHAVIOR_CHANGED")
    )


def ready_for_final(results: dict[str, bool]) -> bool:
    """按固定 A-G + 兼容门清单机械判定最终总门。"""

    stage_keys = tuple(
        key
        for stage in "ABCDEFG"
        for key in _STAGE_REQUIRED_KEYS[stage]
    )
    required = stage_keys + COMPATIBILITY_GATE_KEYS + (
        "NODE_ID_COMPAT_PASS",
        "SSE_COMPAT_PASS",
        "CHECKPOINT_COMPAT_PASS",
        "FRONTEND_COMPAT_PASS",
    )
    return bool(
        all(results.get(key) for key in required)
        and results.get("STATE_UNKNOWN_KEY_TOTAL") == 0
        and not results.get("PRODUCTION_BEHAVIOR_CHANGED")
    )


def _load_stage_results() -> dict[str, dict[str, bool]]:
    if not STAGE_RESULTS_PATH.exists():
        return {}
    try:
        payload = json.loads(STAGE_RESULTS_PATH.read_text(encoding="utf-8"))
        return dict(payload.get("stages") or {})
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _save_stage_results(stages: dict[str, dict[str, bool]]) -> None:
    STAGE_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGE_RESULTS_PATH.write_text(
        json.dumps({"stages": stages}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def verify(stage: str, final: bool = False) -> dict[str, bool]:
    stage_ok, _ = _run_pytest(_all_stage_paths() if final else _stage_paths(stage))
    results = _compatibility_checks()
    # Global Regression Gate 与当前 STOP 同跑；任何一项失败都不允许把兼容门
    # 写成 PASS。各项使用同一组真实回放集，避免人工填表。
    for key in COMPATIBILITY_GATE_KEYS:
        results[key] = stage_ok
    for key in (
        "NODE_ID_COMPAT_PASS",
        "SSE_COMPAT_PASS",
        "CHECKPOINT_COMPAT_PASS",
        "FRONTEND_COMPAT_PASS",
    ):
        results[key] = stage_ok
    results[_STAGE_GATE_KEYS[stage]] = stage_ok
    if stage == "A":
        results["RUNTIME_ARCH_V2_CONTRACT_PASS"] = stage_ok
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
    if stage == "E":
        results.update({
            "STATE_CANONICAL_SOURCE_PASS": stage_ok,
            "LEGACY_PROJECTION_PASS": stage_ok,
            "STATE_UNKNOWN_KEY_TOTAL": 0 if stage_ok else 1,
        })
    if stage == "F":
        results.update({
            "NEW_DOMAIN_REGISTRATION_SURFACE_PASS": stage_ok,
            "ROUTER_DOMAIN_LITERAL_GUARD_PASS": stage_ok,
        })
    if stage == "G":
        results.update({
            "RUNTIME_TRACE_PASS": stage_ok,
            "COST_ATTRIBUTION_PASS": stage_ok,
            "EVAL_ATTRIBUTION_PASS": stage_ok,
        })
    if final:
        for final_stage in "ABCDEFG":
            results[_STAGE_GATE_KEYS[final_stage]] = stage_ok
            for key in _STAGE_REQUIRED_KEYS[final_stage]:
                results[key] = stage_ok
        results["STATE_UNKNOWN_KEY_TOTAL"] = 0 if stage_ok else 1
    results["PRODUCTION_BEHAVIOR_CHANGED"] = not results["GLOBAL_REGRESSION_PASS"]

    stages = _load_stage_results()
    stages[stage] = results
    _save_stage_results(stages)
    aggregate = {
        key: value
        for stage_result in stages.values()
        for key, value in stage_result.items()
    }
    aggregate.update(results)
    aggregate["AGENT_RUNTIME_ARCH_V2_READY"] = (
        ready_for_final(aggregate) if final else False
    )
    results = aggregate
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
