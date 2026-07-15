"""
guardrails — RAG 答案忠实性检测层

职责:
  - 将 LLM 生成答案拆解为可验证的事实断言
  - 筛选高风险 claim（数字/金额/政策/流程）
  - 用 NLI 模型验证 claim 是否被检索文档支撑
  - 汇总 faithfulness 分数

用法:
    from backend.rag.guardrails import check_faithfulness
    result = check_faithfulness(answer, context_docs)
    # result.claims, result.supported, result.unsupported, result.score
"""

from backend.rag.guardrails.scorer import check_faithfulness, FaithfulnessResult

__all__ = ["check_faithfulness", "FaithfulnessResult"]
