"""
response — 最终回答生成层

职责:
  - 汇总原始步骤结果 (step_results)
  - Context Filter: CrossEncoder 验证 RAG 结果相关性
  - 生成最终 Markdown 回答
  - 引用解析 + 参考文献提取

不关心:
  - 数据如何获取（那是 retrieval/sql_agent 的事）
  - 任务如何调度（那是 multi_agent 的事）
  - 状态如何流转（那是 LangGraph 的事）
"""

from backend.response.reporter import generate_final_answer, REPORTER_SYSTEM
from backend.response.context_filter import (
    filter_step_results, filter_by_bm25, check_reranker_available,
    parse_sources_from_text,
)

__all__ = [
    "generate_final_answer", "REPORTER_SYSTEM",
    "filter_step_results", "filter_by_bm25", "check_reranker_available",
    "parse_sources_from_text",
]
