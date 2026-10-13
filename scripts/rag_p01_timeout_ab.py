#!/usr/bin/env python3
"""通过现有 RAG Tool 链路对照 15s 回滚口径与 45s 默认策略。

从仓库根目录运行。需要可访问的 RAG 服务、项目运行依赖，以及有权访问
目标知识库的测试身份。脚本只输出状态和耗时，不打印问题、答案或 Trace ID。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid


def _configure_timeout(scene: str) -> int:
    """设置本次进程的对照值，同时保留其它 Tool 的环境覆盖。"""
    raw = os.environ.get("TOOL_POLICY_JSON", "").strip()
    try:
        overrides = json.loads(raw) if raw else {}
    except (ValueError, TypeError) as exc:
        raise SystemExit("TOOL_POLICY_JSON 必须是 JSON 对象") from exc
    if not isinstance(overrides, dict):
        raise SystemExit("TOOL_POLICY_JSON 必须是 JSON 对象")

    rag_override = overrides.get("rag.search", {})
    if not isinstance(rag_override, dict):
        raise SystemExit("TOOL_POLICY_JSON 中 rag.search 必须是 JSON 对象")
    if scene == "A":
        overrides["rag.search"] = {**rag_override, "timeout_ms": 15_000}
        timeout_ms = 15_000
    else:
        rag_override.pop("timeout_ms", None)
        if not rag_override:
            overrides.pop("rag.search", None)
        timeout_ms = 45_000

    if overrides:
        os.environ["TOOL_POLICY_JSON"] = json.dumps(overrides)
    else:
        os.environ.pop("TOOL_POLICY_JSON", None)
    return timeout_ms


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("A", "B"), required=True,
                        help="A=15s 回滚口径，B=45s 默认策略")
    parser.add_argument("--query", action="append", required=True,
                        help="实际验证问题；可重复传入多个")
    parser.add_argument("--user-id", default=os.getenv("RAG_TEST_USER_ID"),
                        help="有权访问目标知识库的测试用户 ID，或设置 RAG_TEST_USER_ID")
    parser.add_argument("--tenant-id", default=os.getenv("RAG_TEST_TENANT_ID"),
                        help="目标租户 ID，或设置 RAG_TEST_TENANT_ID")
    parser.add_argument("--department", default=os.getenv("RAG_TEST_DEPARTMENT"),
                        help="测试用户部门，或设置 RAG_TEST_DEPARTMENT")
    parser.add_argument("--role", action="append", default=None,
                        help="测试用户角色；可重复传入，或设置 RAG_TEST_ROLES（逗号分隔）")
    parser.add_argument("--permission", action="append", default=[],
                        help="测试用户权限；可重复传入")
    args = parser.parse_args()
    missing = [name for name in ("user_id", "tenant_id", "department")
               if not getattr(args, name)]
    if missing:
        parser.error("需提供测试身份参数或环境变量：" + ", ".join(missing))
    if args.role is None:
        args.role = [item.strip() for item in os.getenv("RAG_TEST_ROLES", "").split(",")
                     if item.strip()]
    if not args.role:
        parser.error("需提供至少一个 --role 或设置 RAG_TEST_ROLES")
    return args


def _bind_subject(args: argparse.Namespace, session_id: str) -> None:
    from backend.tools.session import (
        set_session_id,
        set_tool_department,
        set_tool_permissions,
        set_tool_roles,
        set_tool_tenant_id,
        set_tool_user_id,
    )

    set_session_id(session_id)
    set_tool_user_id(args.user_id)
    set_tool_department(args.department)
    set_tool_tenant_id(args.tenant_id)
    set_tool_roles(tuple(args.role))
    set_tool_permissions(tuple(args.permission))


async def _run_one(skill, question: str, step_id: str, session_id: str) -> dict:
    from backend.observability.tracer import trace_collector

    state = {
        "current_step_id": step_id,
        "step_results": {},
        "plan": {"nodes": {step_id: {
            "capability": "rag.search",
            "description": "RAG 超时对照验证",
            "params": {"question": question},
        }}, "edges": {}},
    }
    trace = trace_collector.start(
        question="RAG timeout A/B reproduction", session_id=session_id,
    )
    started = time.monotonic()
    result = await skill.execute(state, step_capability="rag.search")
    wall_ms = int((time.monotonic() - started) * 1000)
    step_result = result["step_results"][step_id]
    answer = step_result.get("output") or ""
    trace_collector.finish(trace, answer, wall_ms, "", "")
    return {
        "wall_ms": wall_ms,
        "status": step_result.get("status"),
        "tool_status": step_result.get("tool_status"),
        "latency_ms": step_result.get("latency_ms"),
        "error_code": step_result.get("error_code"),
        "answer_len": len(answer),
        "trace_status": trace.status,
    }


async def _run_all(skill, questions: list[str], session_id: str) -> list[dict]:
    rows = []
    for index, question in enumerate(questions, start=1):
        rows.append(await _run_one(skill, question, f"step_{index}", session_id))
    return rows


def main() -> int:
    args = _parse_args()
    expected_timeout_ms = _configure_timeout(args.scene)
    os.environ.setdefault("RAG_MODE", "remote")
    if os.getenv("RAG_MODE") != "remote":
        raise SystemExit("此脚本要求 RAG_MODE=remote")
    if not os.getenv("RAG_SERVICE_URL"):
        raise SystemExit("请设置可访问的 RAG_SERVICE_URL")

    # 配置环境后再导入 backend，确保 Tool Policy 从本次对照值初始化。
    from backend.core.tool_runtime.policy import get_policy
    from backend.skills.rag.skill import RAGSkill

    actual_timeout_ms = get_policy("rag.search").timeout_ms
    if actual_timeout_ms != expected_timeout_ms:
        raise SystemExit(
            f"有效 rag.search 超时应为 {expected_timeout_ms}ms，实际为 {actual_timeout_ms}ms"
        )

    session_id = f"rag-ab-{uuid.uuid4().hex[:12]}"
    _bind_subject(args, session_id)
    skill = RAGSkill()
    rows = asyncio.run(_run_all(skill, args.query, session_id))
    print(json.dumps({
        "scene": args.scene,
        "policy_timeout_ms": actual_timeout_ms,
        "rows": rows,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
