"""
skills/business_analysis/skill.py — 业务分析 Skill（推理能力）

职责：接收前置 sql.query 的 SQLResult → RAG 检索 → LLM 生成 BusinessInsight
禁止：直接访问数据库（通过 SQLResult 获取数据）

与 SQLSkill 的关系：
  Planner 编排 sql.query → business.analyze 的 DAG 依赖
  Supervisor 在第 1 轮执行 sql.query，第 2 轮执行 business.analyze
  两者通过 previous_outputs 中的 SQLResult 数据协议解耦
"""
from __future__ import annotations

import time

from backend.shared.logger import logger
from backend.skills.base import BaseSkill
from backend.skills.sql.models import SQLResult
from backend.skills.business_analysis.models import BusinessInsight
from backend.skills.business_analysis.analyzer import BusinessAnalyzer


class BusinessAnalysisSkill(BaseSkill):
    """业务分析 Skill — 将 SQL 数据转化为业务洞察。

    Planner 在需要业务推理时，将 business.analyze 编排为 sql.query 的后置步骤。
    Supervisor 自动传递 previous_outputs 中的 SQLResult。
    """

    name = "business_analysis"
    capabilities = ["business.analyze"]
    description = (
        "对 SQL 查询结果进行业务分析，结合 RAG 知识库中的业务规则，"
        "生成风险洞察和行动建议。依赖前置 sql.query 步骤的 SQLResult。"
    )
    params_schema = {
        "sql_result": "前置 sql.query 步骤产出的 SQLResult（自动传递，无需手动指定）",
    }
    examples = [
        {
            "sql_result": "{sql: 'SELECT ...', tables: ['inventory.inventory'], rows: [...], ...}"
        }
    ]

    @property
    def _tool_fn(self):  # type: ignore[override]
        raise NotImplementedError(
            "BusinessAnalysisSkill 不依赖 _tool_fn；"
            "直接调用 BusinessAnalyzer.analyze()。"
        )

    async def execute(
        self,
        state: dict,
        step_capability: str = "",
        max_retries: int = 1,
        timeout: float = 30.0,
    ) -> dict:
        """执行业务分析：读取前置 SQLResult → RAG 检索 → LLM 分析。

        从 state["previous_outputs"] 获取前置步骤的 SQLResult，
        从 state["step_results"] 获取前置步骤的 RAG 知识（如有）。
        """
        step_id = state.get("current_step_id")
        if not step_id:
            logger.warning("[BusinessAnalysis] current_step_id 缺失，跳过")
            return {}

        plan_node = state.get("plan", {}).get("nodes", {}).get(step_id, {})
        description = plan_node.get("description", step_id)

        step_results = dict(state.get("step_results") or {})
        sr: dict = dict(step_results.get(step_id) or {})
        sr.update(
            step_id=step_id,
            capability=step_capability or "business.analyze",
            description=description,
            status="running",
            retries=0,
            started_at=time.time(),
            error=None,
            error_type=None,
        )
        step_results[step_id] = sr

        # 1. 从前置步骤获取 SQLResult
        previous_outputs: dict = state.get("previous_outputs", {})
        if not previous_outputs:
            sr["status"] = "failed"
            sr["error"] = "business.analyze 缺少前置 sql.query 的输出（previous_outputs 为空）"
            sr["error_type"] = "missing_dependency"
            sr["finished_at"] = time.time()
            logger.error(f"[BusinessAnalysis] {sr['error']}")
            return {"step_results": step_results}

        # 取第一个前置步骤的输出（通常只有一个前置步骤）
        raw_output = next(iter(previous_outputs.values()), None)
        if not raw_output:
            sr["status"] = "failed"
            sr["error"] = "前置步骤输出为空，无法进行分析"
            sr["error_type"] = "empty_input"
            sr["finished_at"] = time.time()
            logger.error(f"[BusinessAnalysis] {sr['error']}")
            return {"step_results": step_results}

        try:
            sql_result = SQLResult(**raw_output)
        except Exception as e:
            sr["status"] = "failed"
            sr["error"] = f"无法解析前置 SQLResult: {e}"
            sr["error_type"] = "parse_error"
            sr["finished_at"] = time.time()
            logger.error(f"[BusinessAnalysis] {sr['error']}")
            return {"step_results": step_results}

        # 2. RAG 检索业务知识
        rag_knowledge = ""
        try:
            # 从前置步骤中查找 RAG 检索结果（如有）
            for sid, s in step_results.items():
                if s.get("capability") == "rag.search" and s.get("status") == "success":
                    rag_knowledge = str(s.get("output", ""))
                    break

            # 如果前置没有 RAG 步骤，尝试直接检索
            if not rag_knowledge:
                rag_knowledge = self._fetch_rag_knowledge(sql_result)
        except Exception as e:
            logger.warning(f"[BusinessAnalysis] RAG 检索失败（非致命）: {e}")

        # 3. LLM 业务分析
        analyzer = BusinessAnalyzer()
        try:
            insight = analyzer.analyze(sql_result, rag_knowledge)
            sr["status"] = "success"
            sr["output"] = insight.model_dump()
            sr["finished_at"] = time.time()
            logger.info(
                f"[BusinessAnalysis] step={step_id} 完成: "
                f"summary={insight.summary[:80]}..."
            )
        except Exception as e:
            sr["status"] = "failed"
            sr["error"] = f"业务分析失败: {e}"
            sr["error_type"] = "analysis_error"
            sr["finished_at"] = time.time()
            logger.error(f"[BusinessAnalysis] {sr['error']}")

        return {"step_results": step_results}

    def _fetch_rag_knowledge(self, sql_result: SQLResult) -> str:
        """从 RAG 知识库检索相关业务规则（轻量检索，不触发 LLM 生成）。

        使用 RAGPipeline.retrieve_knowledge() 而非 ask()——
        避免完整 RAG 链路（LLM 回答+evidence gate，30-120s），
        只做 BM25+向量检索+返回原始文本（3-5s）。
        """
        try:
            from backend.app.api.deps import get_rag_pipeline
            pipeline = get_rag_pipeline()

            table_names = ", ".join(sql_result.tables[:3]) if sql_result.tables else "电商业务"
            search_question = f"{table_names} 业务规则 风险预警 运营策略"

            return pipeline.retrieve_knowledge(search_question)
        except Exception as e:
            logger.warning(f"[BusinessAnalysis] RAG 检索异常: {e}")
            return ""


# =================================================
# LangGraph 节点适配器
# =================================================

async def business_analysis_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = BusinessAnalysisSkill()
    cap = (
        state.get("plan", {}).get("nodes", {})
        .get(state.get("current_step_id", ""), {})
        .get("capability", "business.analyze")
    )
    logger.info(
        f"[BusinessAnalysis Node] cap={cap} step={state.get('current_step_id')}"
    )
    return await skill.execute(state, step_capability=cap)
