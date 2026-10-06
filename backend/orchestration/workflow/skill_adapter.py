"""workflow/skill_adapter.py — Skill 适配器

让现有 BaseSkill（SQL/RAG/Report/Email/...）能在 workflow step 里被便捷调用。

失败语义契约（2026-10-07 STOP A）：
    成败只依据 BaseSkill 写回的结构化 ``status`` 判定，禁止按 output 是否
    为空猜测（合法业务输出可以是 [] / {} / ""）。status=failed/skipped/
    未知形态一律上抛 :class:`SkillStepFailure`（携带完整 step_result），
    交由 WorkflowExecutor 的 on_error 策略（abort/skip/agent_degrade）
    决定 workflow 走向——适配器绝不把失败静默成 ``{}`` 假成功。

用法：
    from orchestration.workflow.skill_adapter import call_sql, call_rag, call_report, call_email

    @step()
    async def fetch_sales(self, ctx):
        return await call_sql({"query": "SELECT * FROM sales WHERE date=today"})
"""
from __future__ import annotations

from typing import Any

from backend.shared.logger import logger

# BaseSkill 治理路径写回的失败语义字段（skills/base.py:548-630）。
# SkillStepFailure.step_result 只透传这些键，消费方（WorkflowExecutor
# 留痕 / trace metrics / 测试断言）按存在性读取。
_FAILURE_FIELDS = (
    "error", "error_type", "error_protocol", "tool_status",
    "criticality", "error_code", "fallback_used", "degraded",
    "needs_verification", "retries",
)


class SkillStepFailure(ValueError):
    """Skill 步骤失败（status=failed / skipped / 契约外形态）。

    继承 ValueError：既有按 ValueError 捕获 step 失败的调用方与测试
    （WorkflowExecutor 的 except Exception、call_sql 失败断言）契约不破坏。
    ``step_result`` 携带 BaseSkill 的完整结构化失败信息，供上层留痕与
    跨路径 parity（direct/planner/workflow 三条路径的 tool_status 同源）。
    """

    def __init__(self, message: str, *, step_result: dict | None = None):
        super().__init__(message)
        self.step_result = {
            k: v for k, v in dict(step_result or {}).items()
            if k in _FAILURE_FIELDS or k == "status"
        }

    @property
    def tool_status(self) -> str:
        return str(self.step_result.get("tool_status") or "")

    @property
    def criticality(self) -> str:
        return str(self.step_result.get("criticality") or "")

    @property
    def error_code(self) -> str:
        return str(self.step_result.get("error_code") or "")

    @property
    def degraded(self) -> bool:
        return bool(self.step_result.get("degraded"))


def _build_state(step_id: str, capability: str, params: dict) -> dict:
    """构造 BaseSkill.execute() 所需的最小 state dict"""
    return {
        "current_step_id": step_id,
        "plan": {
            "nodes": {
                step_id: {
                    "capability": capability,
                    "params": params,
                }
            }
        },
        "step_results": {},
    }


def _extract_step_result(state: dict, step_id: str) -> dict:
    """从 BaseSkill.execute() 返回值提取完整 step result（不只 output）。"""
    return state.get("step_results", {}).get(step_id, {})


async def call_skill(skill_name: str, capability: str, params: dict) -> dict:
    """通用 Skill 调用入口

    Args:
        skill_name: Skill 名（"sql" / "rag" / "report" / "email" / ...）
        capability: capability 名（如 "sql.query"）
        params: 输入参数

    Returns:
        dict: Skill 输出（仅 status=success 时返回；空 [] / {} 是合法成功）

    Raises:
        SkillStepFailure: status=failed/skipped 或返回形态缺失 status
            （契约破坏 fail-loud，不伪装成功）。
    """
    from backend.skills.base import BaseSkill
    from backend.skills.sql.skill import SQLSkill
    from backend.skills.rag.skill import RAGSkill
    from backend.skills.report.skill import ReportSkill
    from backend.skills.email.skill import EmailSkill
    from backend.skills.data_export.skill import DataExportSkill
    from backend.skills.web_search.skill import WebSearchSkill
    from backend.skills.web_crawl.skill import WebCrawlSkill

    skill_map: dict[str, type] = {
        "sql": SQLSkill,
        "rag": RAGSkill,
        "report": ReportSkill,
        "email": EmailSkill,
        "data_export": DataExportSkill,
        "web_search": WebSearchSkill,
        "web_crawl": WebCrawlSkill,
    }
    skill_cls = skill_map.get(skill_name)
    if skill_cls is None:
        raise ValueError(
            f"Unknown skill: {skill_name!r}, supported: {list(skill_map.keys())}"
        )

    skill = skill_cls()
    state = _build_state(skill_name, capability, params)
    result = await skill.execute(state, step_capability=capability)
    sr = _extract_step_result(result, skill_name)
    status = sr.get("status")
    if status == "success":
        output = sr.get("output", {})
        if not output:
            # 空 [] / {} / "" 是合法业务结果（如 0 行查询），不是失败；
            # 只留观测痕迹，不做任何语义改写。
            logger.info(f"[SkillAdapter] {skill_name}:{capability} 成功且业务输出为空")
        return output
    if status == "skipped":
        # BaseSkill OPTIONAL criticality 失败：仍不许静默成功——上抛后由
        # step 的 on_error 策略裁决（skip → partial / abort → failed）。
        raise SkillStepFailure(
            f"{skill_name}:{capability} 非关键步骤失败已跳过: "
            f"{sr.get('error') or '未知原因'}",
            step_result=sr,
        )
    raise SkillStepFailure(
        f"{skill_name}:{capability} 执行失败: {sr.get('error') or '未知错误'}",
        step_result=sr,
    )


# 便捷封装（按 capability 命名）
async def call_sql(params: dict) -> dict:
    """调 SQL Skill

    两种模式：
    - params 含 "query" 键 → 直接执行原始 SQL（Workflow step 确定性查询）
    - params 含 "question" 键 → 走 NL→SQL Agent（自然语言查询）
    """
    if "query" in params:
        # 直接执行 raw SQL（绕过 Agent，避免 NL→SQL 开销和误差）。
        # P1-4 补盲区（2026-09-30）：此前裸调 ainvoke 不经治理层，workflow
        # 侧工具调用不进 record_tool_result（/tools 页 merged 口径看不到）。
        # 现经统一 safe_tool_executor：获得统计 + 超时/重试/熔断治理。
        # 封套语义不变——failed 封套仍由本层上抛 ValueError 进 step 失败
        # 路径（与 BaseSkill 治理路径同口径：语义失败计 ok，异常才计错误
        # 分类）；治理层拦截（熔断/隔离舱/超时）同样上抛保持 step 失败。
        # STOP A（2026-10-07）：失败改抛 SkillStepFailure（ValueError 子类，
        # 兼容既有捕获方），携带 tool_status/error_code —— 与 call_skill
        # 路径同构，跨执行路径 parity 不再断在形态上。
        from backend.core.tool_runtime.executor import safe_tool_executor
        from backend.core.tool_runtime.models import ToolStatus
        from backend.orchestration.tools import execute_sql_tool
        from backend.shared.tool_envelope import unwrap_envelope
        executed = await safe_tool_executor.run(
            tool_key="sql.query",
            call=lambda: execute_sql_tool.ainvoke({"query": params["query"]}),
            domain="sql",
            tool_name="execute_sql_tool",
            trace_capability="sql.query",
            trace_agent="workflow_sql",
            trace_params={"query": params["query"]},
        )
        if executed.status is not ToolStatus.SUCCESS:
            raise SkillStepFailure(
                f"execute_sql_tool 失败: {executed.error_message or executed.error_code}",
                step_result={
                    "status": "failed",
                    "error": executed.error_message or executed.error_code or "",
                    "tool_status": executed.status.value,
                    "error_code": executed.error_code or "",
                    "fallback_used": executed.fallback_used or "",
                    "retries": executed.retry_count,
                },
            )
        result_str = executed.data
        # 边界归一（STOP G M2：解包统一走 shared/tool_envelope.unwrap_envelope，
        # 不再手写 json.loads + status 判断）：execute_sql_tool 已统一封套
        # （shared/tool_envelope.py）——成功拆出 data 返回；失败上抛，
        # 让 step 走失败路径，而不是把 error dict 当业务数据往下传。
        is_envelope, payload = unwrap_envelope(result_str)
        if is_envelope and isinstance(payload, dict) and payload.get("status") == "failed":
            raise SkillStepFailure(
                f"execute_sql_tool 失败: {payload.get('error', '未知错误')}",
                step_result={
                    "status": "failed",
                    "error": str(payload.get("error", "")),
                    "tool_status": ToolStatus.FAILED.value,
                    "error_protocol": payload.get("error_protocol"),
                },
            )
        if is_envelope:
            return payload or {}
        # 兼容：无 status 的历史形态——维持旧语义「解析后原样透传 dict」
        # （消费方按 dict 消费；非 JSON 字符串原样返回不炸）
        import json as _json  # noqa: F811
        try:
            return _json.loads(result_str)
        except (ValueError, TypeError):
            return result_str
    return await call_skill("sql", "sql.query", params)


async def call_rag(params: dict) -> dict:
    """调 RAG Skill

    params: {"question": "...", "kb_id": "analytics", "top_k": 5}

    fix f10：RAGSkill 参数契约是 question；旧调用方传 query 导致
    pydantic 校验失败（question Field required），重试 3 次后步骤失败。
    此处做 query → question 兼容转换。

    fix f14：RAGSkill 的 output 是纯文本答案（str），调用方若直接
    .get() 会报 'str' object has no attribute 'get'；在适配层边界
    归一为 {"answer": ...} dict。
    """
    if "question" not in params and "query" in params:
        params = {"question": params["query"],
                  **{k: v for k, v in params.items() if k != "query"}}
    output = await call_skill("rag", "rag.search", params)
    if not isinstance(output, dict):
        output = {"answer": output}
    return output


async def call_report(params: dict) -> dict:
    """调 Report Skill

    params: {"report_type": "daily_sales", "filters": {...}}

    fix f16b：ReportSkill（generate_report_tool）的 output 是纯 Markdown
    str，调用方若直接 .get() 会报 'str' object has no attribute 'get'；
    在适配层边界归一为 {"content": ...} dict（与 f14 同类边界归一）。
    """
    output = await call_skill("report", "report.generate", params)
    if not isinstance(output, dict):
        output = {"content": output}
    return output


async def call_email(params: dict) -> dict:
    """调 Email Skill

    params: {"to": [...] 或 "a@b.com", "subject": "...", "body": "..."}
    自动将 list 类型 to 转为 ; 分隔字符串（send_email_tool 期望 str）
    """
    if isinstance(params.get("to"), list):
        params = {**params, "to": "; ".join(params["to"])}
    return await call_skill("email", "email.send", params)
