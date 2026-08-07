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
from backend.skills.base import BaseSkill
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
    params_schema = {"question": "自然语言查询问题（中文/英文）"}
    examples = [{"question": "查询库存低于安全库存的商品及其库存量"}]

    @property
    def _tool_fn(self):  # type: ignore[override]
        raise NotImplementedError(
            "SQLSkill 不依赖 _tool_fn；通过 SQLAgent 单例直接调用 ask_struct。"
        )

    async def execute(
        self,
        state: dict,
        step_capability: str = "",
        max_retries: int = _DEFAULT_MAX_RETRIES,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict:
        """执行 SQL 查询，返回结构化 SQLResult。

        Trace 由 TraceMiddleware 统一记录，本方法不手动管理 Span。
        """
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
        last_result: AgentSQLResult | None = None

        for attempt in range(max_retries + 1):
            sr["retries"] = attempt
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(agent.ask_struct, question),
                    timeout=timeout,
                )
                last_result = result

                # 成功 / 无数据 → 转换为 Pydantic SQLResult
                if result.status in ("success", "no_data"):
                    sr["status"] = "success"
                    sr["output"] = _agent_result_to_pydantic(result).model_dump()
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
                    return {"step_results": step_results}

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
                    return {"step_results": step_results}

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

        logger.error(f"[SQL Skill] step={step_id} 最终失败: {sr['error']}")
        return {"step_results": step_results}


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
