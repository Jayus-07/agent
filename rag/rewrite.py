# =====================================================
# rag/rewrite.py
# Query Rewrite + Rewrite Guard
# =====================================================

from typing import List

from langchain_core.prompts import ChatPromptTemplate
from sentence_transformers import util

from config import MULTI_QUERY_NUM, REWRITE_SIMILARITY_THRESHOLD, EMBEDDING_TIMEOUT
from llm.llm_factory import llm, embedding_model
from utils.logger import logger
from utils.timeout import safe_call_with_timeout

# =====================================================
# 配置常量
# =====================================================
SIMILARITY_THRESHOLD = REWRITE_SIMILARITY_THRESHOLD  # 从配置加载
MAX_SHORT_TEXT_LEN = 20  # 短文本阈值
SHORT_TEXT_MULTIPLIER = 5  # 短文本允许倍数
LONG_TEXT_MULTIPLIER = 3  # 长文本允许倍数
MIN_ALLOWED_BYTES = 50  # 最小允许字节数

# =====================================================
# Prompt
# =====================================================

prompt = ChatPromptTemplate.from_template("""
你是企业知识库 Query Rewrite 助手。

任务：
  生成{num_queries}个不同表达方式的检索查询，语义相同但用词不同。

禁止：
1. 改变用户意图
2. 发散推理
3. 修改主题
4. 添加序号或编号
5. 改实体

规则：
1. 如果问题已经清晰，直接原样返回
2. 保持原语义
3. 每行一个改写结果
4. 不要解释、不要 markdown 格式
5. 不要空行

用户问题：
{question}

检索 Query：
""")


chain = prompt | llm


# =====================================================
# Rewrite Guard
# =====================================================

def _check_length(original: str, rewritten: str) -> tuple:
    """检查长度是否合理，返回 (是否通过, 原因)"""
    original_len = len(original.encode('utf-8'))
    rewritten_len = len(rewritten.encode('utf-8'))
    
    # 对于短文本，允许更大的扩展比例
    if original_len < MAX_SHORT_TEXT_LEN:
        max_allowed = max(original_len * SHORT_TEXT_MULTIPLIER, MIN_ALLOWED_BYTES)
    else:
        max_allowed = original_len * LONG_TEXT_MULTIPLIER
    
    if rewritten_len > max_allowed:
        return False, f"过长 ({rewritten_len} > {max_allowed})"
    
    return True, "OK"


def _check_similarity(original: str, rewritten: str) -> tuple:
    """检查语义相似度，返回 (是否通过, 相似度分数)"""
    try:
        # 使用超时保护计算 Embedding
        emb1 = safe_call_with_timeout(
            embedding_model.encode,
            timeout=EMBEDDING_TIMEOUT,
            default_value=None,
            error_message=f"Embedding计算超时 ({EMBEDDING_TIMEOUT}s)",
            sentences=original,
            convert_to_tensor=True
        )
        
        emb2 = safe_call_with_timeout(
            embedding_model.encode,
            timeout=EMBEDDING_TIMEOUT,
            default_value=None,
            error_message=f"Embedding计算超时 ({EMBEDDING_TIMEOUT}s)",
            sentences=rewritten,
            convert_to_tensor=True
        )
        
        if emb1 is None or emb2 is None:
            logger.warning("⚠️ Embedding计算失败或超时，跳过相似度检查")
            return True, 1.0  # 失败时默认通过
        
        sim = util.cos_sim(emb1, emb2).item()
        
        if sim < SIMILARITY_THRESHOLD:
            return False, sim
        
        return True, sim
    except Exception as e:
        logger.error(f"相似度计算失败: {e}，默认放行")
        return True, 1.0


def guard(original: str, rewritten: str) -> str:
    """
    重写守卫：检查改写质量
    
    Args:
        original: 原始问题
        rewritten: 改写后的问题
    
    Returns:
        通过的改写或原始问题
    """
    # 1. 长度检查
    passed, reason = _check_length(original, rewritten)
    if not passed:
        logger.warning(f"⚠ Rewrite{reason}，回退原问题")
        logger.debug(f"  原文: '{original}'")
        logger.debug(f"  改写: '{rewritten}'")
        return original

    # 2. 相似度检查
    passed, sim_score = _check_similarity(original, rewritten)
    logger.info(f"Rewrite相似度: {sim_score:.4f} (阈值: {SIMILARITY_THRESHOLD})")
    
    if not passed:
        logger.warning(f"⚠ Rewrite语义漂移 (sim={sim_score:.4f})，回退原问题")
        return original

    return rewritten


# =====================================================
# Parse LLM Output
# =====================================================

def _parse_queries(raw_output: str) -> List[str]:
    """
    解析 LLM 输出的多个查询
    
    Args:
        raw_output: LLM 原始输出
    
    Returns:
        清理后的查询列表
    """
    # 按行分割
    lines = [line.strip() for line in raw_output.split('\n') if line.strip()]
    
    # 去除序号（如 "1. xxx", "2. xxx"）
    cleaned = []
    for line in lines:
        # 移除开头的序号模式："1. ", "2) ", "- " 等
        cleaned_line = line.lstrip('0123456789.-) ')
        if cleaned_line and len(cleaned_line) > 2:
            cleaned.append(cleaned_line)
    
    # 去重（保持顺序）
    seen = set()
    unique_queries = []
    for q in cleaned:
        if q not in seen:
            seen.add(q)
            unique_queries.append(q)
    
    return unique_queries if unique_queries else [raw_output.strip()]


# =====================================================
# Rewrite Query
# =====================================================

def rewrite_query(question: str, num_queries: int = MULTI_QUERY_NUM) -> List[str]:
    """
    查询重写入口（无缓存，每次实时调用 LLM）
    
    Args:
        question: 原始问题
        num_queries: 生成查询数量
    
    Returns:
        查询列表
    """
    try:
        logger.info(f"查询重写开始: '{question}'")

        result = chain.invoke({
            "question": question,
            "num_queries": num_queries
        })

        raw_output = result.content.strip()
        logger.debug(f"LLM原始输出: {repr(raw_output)}")

        # 解析为列表
        queries = _parse_queries(raw_output)
        logger.info(f"解析得到 {len(queries)} 个查询: {queries}")

        # 对每个改写结果应用 Guard
        guarded_queries = []
        for idx, query in enumerate(queries, 1):
            logger.debug(f"Guard检查 [{idx}/{len(queries)}]: '{query}'")
            guarded = guard(question, query)
            guarded_queries.append(guarded)

        logger.info(f"查询重写完成: '{question}' -> {guarded_queries}")
        return guarded_queries

    except Exception as e:
        logger.error(f"查询重写失败: {e}", exc_info=True)
        return [question]