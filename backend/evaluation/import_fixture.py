"""评估 fixture 的明确离线导入入口。"""

from __future__ import annotations

import os
import sys


def _bootstrap_llm_registry() -> None:
    """离线导入前加载数据库中的 embedding/LLM 凭据覆盖层。"""
    try:
        import asyncio

        from backend.infra.llm.registry_store import refresh_registry

        asyncio.run(refresh_registry())
    except Exception as exc:  # noqa: BLE001
        print(f"[bootstrap] refresh_registry 失败: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """按 fixture_set 调用既有离线索引导入器。"""
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1 or not args[0].strip():
        print("用法: python -m backend.evaluation.import_fixture <fixture_set>")
        return 2

    _bootstrap_llm_registry()

    from backend.scripts import ingest_eval_fixtures

    original_argv = sys.argv[:]
    sys.argv = [original_argv[0] if original_argv else "import_fixture", "--fixture-set", args[0]]
    try:
        run_id = os.getenv("EVAL_RUN_ID", "").strip()
        if not run_id:
            return int(ingest_eval_fixtures.main())

        # CI 的 fixture 索引也会真实调用 embedding Provider。把这些调用
        # 放进与后续评测相同的 request ID、用户和租户账本，预算和归因不能
        # 从评测报告的统计窗口中漏掉。
        from backend.core.request_context import scoped_tool_identity
        from backend.evaluation.run_guards import EVAL_TENANT_ID
        from backend.evaluation.service import _handle_budget_binding_error
        from backend.infra.llm.budget import (
            current_request_budget,
            scoped_request_budget,
        )
        from backend.infra.llm.proxy import scoped_current_user_id

        actor = os.getenv("EVAL_TRIGGERED_BY", "").strip() or "eval-runner"
        with scoped_current_user_id(actor):
            with scoped_tool_identity(user_id=actor, tenant_id=EVAL_TENANT_ID):
                with scoped_request_budget(
                    f"eval:{run_id}",
                    user_id=actor,
                    tenant_id=EVAL_TENANT_ID,
                    on_error=_handle_budget_binding_error,
                ):
                    result = int(ingest_eval_fixtures.main())
                    state = current_request_budget()
                    if state is not None:
                        snapshot = state.snapshot()
                        exceeded = []
                        if (
                            state.limits.max_calls > 0
                            and snapshot.calls > state.limits.max_calls
                        ):
                            exceeded.append("calls")
                        if (
                            state.limits.max_total_tokens > 0
                            and snapshot.total_tokens > state.limits.max_total_tokens
                        ):
                            exceeded.append("tokens")
                        if (
                            state.limits.max_cost > 0
                            and snapshot.cost > state.limits.max_cost
                        ):
                            exceeded.append("cost")
                        if exceeded:
                            raise RuntimeError(
                                "fixture 索引阶段超出请求预算: "
                                + ", ".join(exceeded)
                            )
                    return result
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
