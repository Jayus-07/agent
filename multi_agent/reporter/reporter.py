"""
reporter.py — thin wrapper: reporter_node() → 委托 response.reporter

职责: 只做 graph node 适配（state 提取 + context_filter 启用），
      实际内容生成全部委托给 response/ 模块。
"""

from response.reporter import (
    generate_final_answer, REPORTER_SYSTEM,
    _extract_sources_from_steps, _extract_rag_references,
    _is_step_successful, _format_step_outputs, _fallback_summary,
)
from utils.logger import logger


def reporter_node(state: dict) -> dict:
    """LangGraph 节点适配器: state → generate_final_answer → {"final_answer": ...}"""
    question = state.get("question", "")
    step_results = state.get("step_results", {})

    answer = generate_final_answer(
        question=question,
        step_results=step_results,
        context_filter=True,
    )
    return {"final_answer": answer}


__all__ = [
    "reporter_node", "REPORTER_SYSTEM",
    "_extract_sources_from_steps", "_extract_rag_references",
    "_is_step_successful", "_format_step_outputs", "_fallback_summary",
]
