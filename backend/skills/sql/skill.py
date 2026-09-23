"""
skills/sql/skill.py — SQL Skill（数据能力）

职责：接收自然语言问题 → 调用 SQLAgent → 返回结构化 SQLResult
禁止：业务分析（由 BusinessAnalysisSkill 负责）、手动 Trace（由 TraceMiddleware 负责）

与 BusinessAnalysisSkill 通过 SQLResult 数据协议解耦：
  SQLSkill → SQLResult (Pydantic, step_results[step_id].output)
  BusinessAnalysisSkill → 读取 previous_outputs → BusinessInsight
"""
from __future__ import annotations

import asyncio
import time

from backend.shared.logger import logger
from backend.shared.error_protocol import error_envelope_from_exception
from backend.skills.base import BaseSkill
from backend.skills.validation import (
    ValidationFailure,
    validate_invocation,
    validate_output,
    validate_semantics,
)
from backend.skills.sql.models import SQLResult
from backend.sql.sql_agent import get_sql_agent
from backend.sql.sql_result import SQLResult as AgentSQLResult

# 不可重试的状态集合
_NON_RETRYABLE_STATUSES = {
    "syntax_error",
    "permission_denied",
    "validation_error",
    "no_table",
}

_DEFAULT_MAX_RETRIES = 2
_DEFAULT_TIMEOUT = 60


def _build_sql_policy_context(state: dict):
    """从图状态构造 SQL 策略上下文（STOP B 身份透传）。

    生产链路 runner 恒写 request_context（实例或 checkpoint_safe dict），
    据此装配 SQLPolicyContext → 权限门 + 表域 + scope 注入生效
    （guest/无权限主体会被权限门 fail-closed 拒绝）。
    state 无 request_context（旧路径/测试假 state）返回 None = 授权未启用
    旧行为——与 get_context_from_state 的 None 语义一致，不虚构身份。
    """
    from backend.orchestration.request_context import get_context_from_state

    ctx = get_context_from_state(state)
    if ctx is None:
        return None
    from backend.sql.policy import build_sql_policy_context

    return build_sql_policy_context(
        user_id=ctx.user_id or "",
        department=ctx.department or "",
        tenant_id=ctx.tenant_id or "",
        roles=tuple(ctx.roles or ()),
        data_scope=ctx.data_scope or None,
        source_channel="graph",
    )


def _agent_result_to_pydantic(result: AgentSQLResult) -> SQLResult:
    """将 SQLAgent 的 dataclass SQLResult 转换为 Skill 层 Pydantic SQLResult。

    只传递纯数据字段，不包含 status/error 等执行状态。
    """
    # 从 sql_text 解析涉及的表名（简单启发式）
    tables: list[str] = []
    if result.sql_text:
        import re
        # 匹配 FROM/JOIN 后的 schema.table 全限定名
        tables = list(set(re.findall(
            r'(?:FROM|JOIN)\s+([a-z_]+"?"?\.[a-z_]+"?"?)',
            result.sql_text,
            re.IGNORECASE,
        )))

    return SQLResult(
        sql=result.sql_text or "",
        tables=tables,
        columns=result.columns or [],
        rows=result.rows or [],
        row_count=result.row_count,
        execution_time=result.elapsed_sec,
    )


class SQLSkill(BaseSkill):
    """数据库查询 Skill — 纯数据能力，不耦合业务分析。

    Planner 将 sql.query + business.analyze 编排为独立 Capability，
    Supervisor 按 DAG 依赖调度。
    """

    name = "sql"
    capabilities = ["sql.query"]
    description = "查询 PostgreSQL 数据库并返回结构化 SQLResult（行/列/耗时）"
    params_schema = {
        "question": {"type": "string", "required": True, "description": "自然语言查询问题（中文/英文）"},
    }
    examples = [{"question": "查询库存低于安全库存的商品及其库存量"}]
    # 输出契约：SQLResult.model_dump()（execute 已重写并用 Pydantic 收口，
    # 此声明供注册表/下游消费方识别输出形态）
    output_type = "structured"

    @property
    def _tool_fn(self):  # type: ignore[override]
        raise NotImplementedError(
            "SQLSkill 不依赖 _tool_fn；通过 SQLAgent 单例直接调用 ask_struct。"
        )

    async def execute(
        self,
        state: dict,
        step_capability: str = "",
        max_retries: int | None = None,
        timeout: float | None = None,
    ) -> dict:
        """执行 SQL 查询，返回结构化 SQLResult。

        Trace 由 TraceMiddleware 统一记录，本方法不手动管理 Span。
        timeout/max_retries 缺省时按策略注册表（core/tool_runtime/policy）
        解析 —— sql.query 注册策略 timeout 15s / retries 0（Deadline 兜底）；
        未注册场景回退旧默认 60s / 2。
        2026-09-22 Tool 治理：执行前检查请求 Deadline 剩余预算，不足直接
        降级；最终失败按 criticality 标注 business_outcome（root trace 不再
        因 SQL 挂掉整体 error）。
        """
        from backend.core.tool_runtime.executor import _BUDGET_MARGIN_MS
        from backend.core.tool_runtime.policy import get_policy, is_registered
        from backend.skills.base import (
            _deadline_from_state,
            _mark_business_outcome,
        )
        from backend.observability.tracer import trace_collector

        # kill switch（STOP C §十四）：graph 入口与服务不可用语义务必与
        # HTTP/Tool/MCP 一致——不产生 LLM 生成，executor 零调用
        from backend.config import SQL_AGENT_ENABLED

        if not SQL_AGENT_ENABLED:
            step_id_ks = state.get("current_step_id") or "sql"
            logger.warning("[SQL Skill] SQL_AGENT_ENABLED=false，服务不可用降级")
            return {"step_results": {step_id_ks: {
                "step_id": step_id_ks,
                "capability": step_capability or "sql.query",
                "status": "failed",
                "output": None,
                "error": "SQL 查询服务暂不可用，请稍后重试。",
                "error_type": "service_unavailable",
                "tool_status": "unavailable",
                "finished_at": time.time(),
            }}}

        reg = get_policy(step_capability or "sql.query")
        if is_registered(step_capability or "sql.query"):
            timeout = reg.timeout_ms / 1000 if timeout is None else timeout
            max_retries = reg.retries if max_retries is None else max_retries
        else:
            timeout = _DEFAULT_TIMEOUT if timeout is None else timeout
            max_retries = _DEFAULT_MAX_RETRIES if max_retries is None else max_retries
        deadline = _deadline_from_state(state)
        step_id = state.get("current_step_id")
        if not step_id:
            logger.warning("[SQL Skill] current_step_id 缺失，跳过")
            return {}

        plan_node = state.get("plan", {}).get("nodes", {}).get(step_id, {})
        description = plan_node.get("description", step_id)
        question = state.get("question", "")

        step_results = dict(state.get("step_results") or {})
        sr: dict = dict(step_results.get(step_id) or {})
        sr.update(
            step_id=step_id,
            capability=step_capability or "sql.query",
            description=description,
            status="running",
            retries=0,
            started_at=time.time(),
            error=None,
            error_type=None,
        )
        step_results[step_id] = sr

        agent = get_sql_agent()
        try:
            # SQLSkill 为保留 SQLResult 协议重写了 execute，不能绕过
            # BaseSkill 的前置参数闸门；问题文本由请求状态提供。
            validate_invocation(
                step_capability or "sql.query",
                {"question": question},
                self._validate_params,
            )
        except ValidationFailure as exc:
            reason = (exc.envelope.details or {}).get("reason", "")
            error = (
                f"参数校验失败: {reason}"
                if exc.layer == "parameter" and reason
                else exc.envelope.message
            )
            protocol = error_envelope_from_exception(exc, source="skill").to_dict()
            sr.update(
                status="failed", output=None, error=error,
                error_type=protocol["code"].lower(),
                error_protocol=protocol, finished_at=time.time(),
            )
            return {"step_results": {step_id: sr}}

        last_result: AgentSQLResult | None = None

        for attempt in range(max_retries + 1):
            # Deadline 检查：剩余预算装不下一次完整调用 → 直接降级（§4）
            if deadline is not None and (
                deadline.remaining_workflow_ms() < timeout * 1000 + _BUDGET_MARGIN_MS
            ):
                sr.update(
                    status="failed", output=None,
                    error="剩余处理时间不足，跳过 SQL 查询",
                    error_type="timeout",
                    tool_status="unavailable", criticality=reg.criticality.value,
                    error_code="DEADLINE_BUDGET_INSUFFICIENT",
                    fallback_used="deadline_budget",
                    finished_at=time.time(),
                )
                logger.warning(f"[SQL Skill] step={step_id} Deadline 预算不足，降级")
                _mark_business_outcome(trace_collector, reg.criticality,
                                       step_capability or "sql.query",
                                       "DEADLINE_BUDGET_INSUFFICIENT")
                return {"step_results": {step_id: sr}}

            sr["retries"] = attempt
            try:
                # STOP B：策略上下文随状态装配。
                # STOP D P0 纵深防御（2026-09-23）：生产 graph 节点缺可信
                # request_context = 上下文传播断裂（如任务执行器未注入），
                # fail-closed 拒绝，绝不静默退回 policy=None legacy（授权
                # 未启用）。评测/脚本路径直接调 sql_agent.ask_struct，不经
                # 本 Skill，不受影响。
                policy_ctx = _build_sql_policy_context(state)
                if policy_ctx is None:
                    sr.update(
                        status="failed", output=None,
                        error="SQL 查询缺少可信请求上下文，已拒绝执行",
                        error_type="permission_denied",
                        finished_at=time.time(),
                    )
                    logger.warning(
                        f"[SQL Skill] step={step_id} 缺 request_context，"
                        "fail-closed 拒绝（上下文传播断裂）")
                    return {"step_results": {step_id: sr}}
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        agent.ask_struct, question, policy=policy_ctx),
                    timeout=timeout,
                )
                last_result = result

                # 成功 / 无数据 → 转换为 Pydantic SQLResult
                if result.status in ("success", "no_data"):
                    output = _agent_result_to_pydantic(result).model_dump()
                    validate_output(
                        step_capability or "sql.query", output, "structured"
                    )
                    validate_semantics(
                        step_capability or "sql.query", {"question": question}, output
                    )
                    sr["status"] = "success"
                    # L1 上下文预算：与 BaseSkill 同语义（校验后、写 step_results 前）
                    from backend.skills.base import _apply_tool_result_budget
                    output = _apply_tool_result_budget(
                        output, step_capability or "sql.query", step_id)
                    if isinstance(output, dict) and output.get("context_compacted"):
                        sr["context_compacted"] = True
                        sr["original_output_tokens"] = output.get("original_tokens")
                    sr["output"] = output
                    sr["row_count"] = result.row_count
                    sr["is_empty"] = result.is_empty
                    sr["error"] = None
                    sr["error_type"] = None
                    sr["finished_at"] = time.time()
                    elapsed = sr["finished_at"] - sr["started_at"]
                    logger.info(
                        f"[SQL Skill] step={step_id} 成功 ({result.status}) "
                        f"{result.row_count} 行, 耗时 {elapsed:.2f}s"
                    )
                    return {"step_results": {step_id: sr}}

                # 不可重试的错误
                if result.status in _NON_RETRYABLE_STATUSES:
                    sr["status"] = "failed"
                    sr["output"] = None
                    sr["error"] = result.error
                    sr["error_type"] = result.status
                    sr["finished_at"] = time.time()
                    logger.warning(
                        f"[SQL Skill] step={step_id} 不可重试失败: "
                        f"{result.status} - {result.error}"
                    )
                    return {"step_results": {step_id: sr}}

                # 其它错误 → 重试
                logger.warning(
                    f"[SQL Skill] step={step_id} 可重试失败 "
                    f"(第{attempt+1}次): {result.status} - {result.error}"
                )

            except asyncio.TimeoutError:
                logger.warning(
                    f"[SQL Skill] step={step_id} 第{attempt+1}次执行超时 (>{timeout}s)"
                )
            except Exception as e:
                logger.error(
                    f"[SQL Skill] step={step_id} 第{attempt+1}次执行异常: {e}"
                )
                last_result = AgentSQLResult.failed(
                    status="failed", error=str(e), error_type="exception"
                )

            if attempt < max_retries:
                await asyncio.sleep(1.5 ** (attempt + 1))
                continue
            break

        # 重试耗尽
        sr["status"] = "failed"
        sr["finished_at"] = time.time()
        if last_result is not None:
            sr["output"] = None
            sr["error"] = last_result.error
            sr["error_type"] = (
                "timeout"
                if last_result.status == "timeout"
                else last_result.error_type or "retry_exhausted"
            )
        else:
            sr["error"] = f"步骤执行超时（{timeout}s）"
            sr["error_type"] = "timeout"
        # 治理标注：criticality 决定业务结果（important → degraded，§16）
        sr["tool_status"] = "timeout" if sr["error_type"] == "timeout" else "failed"
        sr["criticality"] = reg.criticality.value
        _mark_business_outcome(trace_collector, reg.criticality,
                               step_capability or "sql.query",
                               sr["error_type"])

        logger.error(f"[SQL Skill] step={step_id} 最终失败: {sr['error']}")
        return {"step_results": {step_id: sr}}


# =================================================
# LangGraph 节点适配器
# =================================================

async def sql_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = SQLSkill()
    cap = (
        state.get("plan", {}).get("nodes", {})
        .get(state.get("current_step_id", ""), {})
        .get("capability", "sql.query")
    )
    logger.info(f"[SQL Skill Node] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
