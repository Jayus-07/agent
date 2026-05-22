from typing import List

from langchain_core.prompts import ChatPromptTemplate

from config import MULTI_QUERY_NUM
from llm.llm_factory import llm
from utils.logger import logger

# =====================================================
# Prompt
# =====================================================

prompt = ChatPromptTemplate.from_template("""
你是企业知识库检索查询生成助手。

任务：
  基于原始问题，生成{num_queries}个不同角度的检索查询。

规则：
1. 保持原问题语义和意图不变
2. 从不同视角、用不同词汇重新表达
3. 每行一个查询，不要编号
4. 不要解释、不要 markdown 格式
5. 不要空行
6. 保留原问题中的专有名词和实体

原始问题：
{question}

检索查询：
""")

chain = prompt | llm


def _parse_queries(raw_output: str) -> List[str]:
    lines = [line.strip() for line in raw_output.split('\n') if line.strip()]

    cleaned = []
    for line in lines:
        cleaned_line = line.lstrip('0123456789.-) ')
        if cleaned_line and len(cleaned_line) > 2:
            cleaned.append(cleaned_line)

    seen = set()
    unique = []
    for q in cleaned:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    return unique if unique else [raw_output.strip()]


def _guard_query(original: str, rewritten: str) -> bool:
    """轻量守卫：长度合理、非空、非完全重复"""
    if not rewritten or len(rewritten) < 3:
        return False
    if len(rewritten) > len(original) * 5:
        return False
    if rewritten == original:
        return False
    return True


def generate_multi_queries(question: str, num_queries: int = MULTI_QUERY_NUM) -> List[str]:
    """
    生成多个不同视角的检索查询（含轻量守卫）

    Args:
        question: 单条查询字符串
        num_queries: 生成数量

    Returns:
        查询列表
    """
    try:
        logger.info(f"Multi-query 生成开始: '{question}'")

        result = chain.invoke({
            "question": question,
            "num_queries": num_queries
        })

        raw_output = result.content.strip()
        logger.debug(f"LLM原始输出: {repr(raw_output)}")

        queries = _parse_queries(raw_output)

        # 轻量守卫：过滤明显不合理的改写
        guarded = [q for q in queries if _guard_query(question, q)]
        rejected = len(queries) - len(guarded)
        if rejected:
            logger.info(f"Guard 过滤 {rejected} 个不合理查询")

        if not guarded:
            logger.warning("所有查询未通过守卫，回退原问题")
            return [question]

        logger.info(f"生成 {len(guarded)} 个查询: {guarded}")
        return guarded

    except Exception as e:
        logger.error(f"Multi-query 生成失败: {e}", exc_info=True)
        return [question]
