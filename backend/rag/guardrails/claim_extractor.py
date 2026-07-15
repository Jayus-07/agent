"""
claim_extractor.py — 将 LLM 生成答案拆分为可验证的事实断言

Rule-based 优先（按句子边界 + 事实性关键词过滤），LLM fallback 可选。
"""

import re
from typing import List

from backend.shared.logger import logger

# 事实性关键词：含这些词的句子更可能是可验证的事实声明
_FACT_INDICATORS = re.compile(
    r'\d+'                          # 数字
    r'|[一-鿿]*?(?:元|天|小时|工作日|月|年|周)'  # 中文时间/金额单位
    r'|(?:必须|禁止|不得|应当|需要|要求|规定|允许|可以申请)'  # 政策/规则
    r'|(?:登录|提交|填写|上传|审核|审批|通过|打款)'  # 流程操作
    r'|(?:不超过|至少|最多|最少|高于|低于|大于|小于)'  # 限制条件
    r'|(?:%|％|百分之)'  # 百分比
    r'|(?:费用|金额|价格|成本|预算|报销|退款|扣费|罚款|赔偿|承担)'  # 金额/费用（无数字时也抓）
    r'|(?:全额|部分|一半|全部)'  # 数量限定词
)

# 非事实性开头：跳过这些模式（仅在去掉编号前缀后）
_SKIP_PATTERNS = re.compile(
    r'^(#+)'                        # Markdown 标题
    r'|^(以下|如下|介绍|概述|总结|参考|注意|提示|建议)'  # 描述性开头
    r'|^[\(\（].*[\)\）]$'          # 纯括号包裹
)

# 编号前缀：列表编号、中文数字编号
_STRIP_NUMBERING = re.compile(r'^[\d一二三四五六七八九十]+[\.\)、]?\s*')


def _is_factual(sentence: str) -> bool:
    """判断单句是否包含可验证的事实声明。"""
    stripped = sentence.strip()
    if not stripped or len(stripped) < 3:
        return False
    # 去掉 Markdown 标题标记
    if stripped.startswith('#'):
        return False
    # 去掉编号前缀再判断
    clean = _STRIP_NUMBERING.sub('', stripped).strip()
    if not clean or len(clean) < 3:
        return False
    if _SKIP_PATTERNS.match(clean):
        return False
    return bool(_FACT_INDICATORS.search(clean))


def extract_claims(text: str) -> List[str]:
    """从 LLM 生成文本中提取事实性声明。

    Args:
        text: LLM 生成的 Markdown 回答

    Returns:
        可验证的事实声明列表（去重）
    """
    if not text:
        return []

    # 按句子边界切分
    sentences = re.split(r'(?<=[。！？\n])', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    claims = []
    for s in sentences:
        # 去掉 markdown 格式干扰
        clean = re.sub(r'\*\*|__|~~|`', '', s)
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)  # [text](url)
        # 去掉编号前缀
        clean = _STRIP_NUMBERING.sub('', clean).strip()

        if _is_factual(clean):
            claims.append(clean)

    # 去重相邻重复
    seen = set()
    unique = []
    for c in claims:
        key = c[:60]  # 前 60 字符作为去重键
        if key not in seen:
            seen.add(key)
            unique.append(c)

    logger.debug(f"[ClaimExtractor] {len(sentences)} 句 → {len(unique)} claims")
    return unique


def extract_claims_with_llm(text: str) -> List[str]:
    """LLM fallback: 用 LLM 提取事实断言（成本高，仅 rule-based 无结果时使用）。"""
    if not text:
        return []

    try:
        from backend.infra.llm import llm
        from langchain_core.messages import HumanMessage

        prompt = f"""从以下文本中提取所有可验证的事实声明。每行一条，只输出声明本身。

规则：
1. 只提取包含具体数字/日期/金额/规则/流程步骤的句子
2. 不要提取标题、过渡句、建议
3. 保持原句表达，不要改写

文本：
{text[:3000]}

事实声明："""

        result = llm.invoke([HumanMessage(content=prompt)])
        content = result.content if hasattr(result, "content") else str(result)
        lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
        # 去编号
        lines = [re.sub(r'^\d+[\.\)、]\s*', '', l) for l in lines]
        logger.info(f"[ClaimExtractor:LLM] 提取 {len(lines)} 条 claim")
        return lines
    except Exception as e:
        logger.warning(f"[ClaimExtractor:LLM] fallback 失败: {e}")
        return extract_claims(text)  # 回退到 rule-based
