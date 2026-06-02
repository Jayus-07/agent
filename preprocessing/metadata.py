import hashlib
import os
import re
import time
from collections import Counter
from functools import lru_cache
from typing import List, Set, Dict, Any, Optional, Literal, Tuple

from config import DOC_TYPE_RULES, TIME_PATTERNS, DOMAIN_RULES, SUMMARY_MAX_LENGTH, LLM_REQUEST_TIMEOUT
from llm.llm_factory import llm
from preprocessing.entity import extract_person_names

from preprocessing.keyword import extract_doc_keywords, extract_chunk_keywords
from utils.async_utils import async_safe_call_with_timeout

from utils.logger import logger


# =====================================================
# 文档类型分类（改进：使用正则、优先级顺序）
# =====================================================

def classify_doc_type(text: str) -> Literal["resume", "project", "report", "manual", "policy", "general"]:
    """分类文档类型，优先级按字典顺序（可调整）"""
    text_lower = text.lower()
    for doc_type, patterns in DOC_TYPE_RULES.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return doc_type
    return "general"

# =====================================================
# 章节提取（改进：支持更多标题格式）
# =====================================================

def extract_sections(text: str, max_sections: int = 10) -> List[str]:
    """
    提取文档章节标题，支持：
    - Markdown: # 标题
    - 数字编号: 1. 标题、1.1 标题
    - 中文编号: 一、标题、第一章 标题
    - 括号编号: 1) 标题
    """
    section_patterns = [
        r'^#{1,6}\s+(.+)$',
        r'^\d+(?:\.\d+)*\s+(.+)$',
        r'^第[一二三四五六七八九十]+章\s*(.+)$',
        r'^[一二三四五六七八九十]+、\s*(.+)$',
        r'^\d+\)\s+(.+)$'
    ]
    compiled_patterns = [re.compile(p) for p in section_patterns]
    sections: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pattern in compiled_patterns:
            match = pattern.match(line)
            if match:
                title = match.group(1).strip()
                if len(title) < 100:
                    sections.append(title)
                    break
        if len(sections) >= max_sections:
            break
    return sections


# =====================================================
# 时间引用提取（改进：使用预编译正则、限制月份范围）
# =====================================================

def extract_time_refs(text: str) -> List[str]:
    times: Set[str] = set()

    for pattern in TIME_PATTERNS:
        for match in pattern.findall(text):

            if isinstance(match, str):

                if match.endswith("月"):
                    month_num = match[:-1]
                    if month_num.isdigit():
                        if not (1 <= int(month_num) <= 12):
                            continue

                times.add(match)

            else:
                times.add(match[0])

    result = list(times)
    return result if result else None


# =====================================================
# 业务领域识别（改进：带权重、最低得分阈值）
# =====================================================

def detect_business_domain(text: str, min_score: int = 2) -> str:
    """识别业务领域，返回得分最高的领域，若最高分低于阈值则返回"general" """
    scores: Dict[str, int] = Counter()
    text_lower = text.lower()
    for domain, kw_weights in DOMAIN_RULES.items():
        domain_score = 0
        for kw, weight in kw_weights.items():
            if kw.lower() in text_lower:
                domain_score += weight
        if domain_score > 0:
            scores[domain] = domain_score

    if not scores:
        return "general"
    best_domain, best_score = max(scores.items(), key=lambda x: x[1])
    return best_domain if best_score >= min_score else "general"


# =====================================================
# 文档摘要（改进：智能截断、保留语义）
# =====================================================

def _smart_truncate(text: str, max_length: int) -> str:
    """智能截断文本，尽量在句号、换行处截断，避免切断单词或乱码。"""
    if len(text) <= max_length:
        return text
    separators = ['。', '？', '！', '\n', '.', '!', '?']
    best_pos = -1
    for sep in separators:
        pos = text.rfind(sep, 0, max_length)
        if pos > best_pos:
            best_pos = pos
    if best_pos != -1:
        return text[:best_pos + 1]
    else:
        space_pos = text.rfind(' ', 0, max_length)
        if space_pos != -1:
            return text[:space_pos] + '...'
        else:
            return text[:max_length] + '...'


@lru_cache(maxsize=32)
async def build_llm_summary_cached(text_hash: str, text: str, max_length: int = SUMMARY_MAX_LENGTH) -> tuple:
    """使用LLM生成摘要和人名（带缓存、超时保护和并发控制）"""
    safe_text = text.encode('utf-8', errors='ignore')[:4000].decode('utf-8', errors='ignore')
    prompt = f"""
你是企业级RAG系统的文档压缩与检索增强助手。

你的任务是：
1. 将文档压缩为"可用于语义检索"的高信息密度摘要
2. 提取文档中出现的所有真实人名

⚠️ 重要要求：
- 摘要必须用于搜索匹配（不是阅读总结）
- 摘要必须保留关键名词、技术词、业务词
- 人名只能提取文本中明确出现的，禁止猜测/编造
- 如果没有人名，返回空列表

请严格按以下 JSON 格式输出：
{{
  "summary": "摘要内容（{max_length}字以内）",
  "person_names": ["人名1", "人名2"]
}}

文档：
{safe_text}
"""
    try:
        response = await async_safe_call_with_timeout(
            llm.invoke,
            timeout=LLM_REQUEST_TIMEOUT,
            default_value=None,
            error_message=f"LLM摘要生成超时 ({LLM_REQUEST_TIMEOUT}s)",
            input=prompt
        )

        if response is None:
            logger.warning("⚠️ LLM摘要生成超时，使用降级摘要")
            raise TimeoutError("LLM超时")

        import json
        try:
            result = json.loads(response.content.strip())
            summary = result.get('summary', '')
            person_names = result.get('person_names', [])

            summary = _smart_truncate(summary, max_length)

            logger.info(f"✅ LLM同时生成摘要({len(summary)}字)和人名({len(person_names)}个): {person_names}")

            return summary, person_names

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}，尝试从文本提取")
            content = response.content.strip()
            summary = _smart_truncate(content, max_length)
            return summary, []

    except Exception as e:
        logger.error(f"LLM摘要生成失败: {e}, 使用降级摘要")
        sentences = re.split(r'[。!?；\n]', text)
        first_two = '。'.join(sentences[:2]) + ('。' if len(sentences[:2]) > 0 else '')
        return _smart_truncate(first_two, max_length), []


async def build_llm_summary(text: str, max_length: int = SUMMARY_MAX_LENGTH) -> tuple:
    """使用LLM生成摘要和人名（入口函数，带缓存）"""
    text_hash = hash(text[:1000])
    return await build_llm_summary_cached(text_hash, text, max_length)


async def generate_summary_if_needed(text: str, is_full_document: bool) -> tuple:
    """根据文档类型决定是否生成摘要和人名"""
    if not is_full_document:
        return None, []

    doc_type = classify_doc_type(text.lower())
    if doc_type in ["resume", "project", "report"]:
        return await build_llm_summary(text)

    return None, []

# =====================================================
# 主 metadata 构建器（改进：预计算小写文本、增加日志）
# =====================================================

async def build_metadata(text: str, fname: str, doc_id: str, chunk_id: str, is_full_document: bool = False):
    """构建元数据（异步版本）"""
    start_time = time.time()

    text_lower = text.lower()

    doc_type = classify_doc_type(text_lower)

    metadata = {
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "source_file": fname,
        "doc_type": doc_type,
        "business_domain": detect_business_domain(text_lower),
    }

    time_refs = extract_time_refs(text)
    if time_refs:
        metadata["time_refs"] = time_refs

    if not is_full_document:
        keywords = extract_chunk_keywords(text)
    else:
        keywords = extract_doc_keywords(text)
        logger.debug(f"Doc关键词 ({len(keywords)}个): {keywords[:5]}..., 文档id: {doc_id}")

    if keywords:
        metadata["keywords"] = keywords

    sections = extract_sections(text)
    if sections:
        metadata["sections"] = sections

    person_names = []

    if is_full_document:
        summary, llm_person_names = await generate_summary_if_needed(text, is_full_document)

        if summary:
            logger.info(summary)
            metadata["summary"] = summary
            if llm_person_names:
                person_names = llm_person_names
                logger.info(f"✅ 从LLM摘要提取人名 ({len(person_names)}个): {person_names[:5]}..., 文档id: {doc_id}")

        if not person_names:
            person_names = extract_person_names(text)
            logger.info(
                f"⚠️ LLM未提取到人名，降级使用规则提取 ({len(person_names)}个): {person_names[:5]}..., 文档id: {doc_id}，文档类别: {doc_type}")
    else:
        person_names = extract_person_names(text)
        if person_names:
            logger.debug(f"Chunk人名 ({len(person_names)}个): {person_names[:3]}..., chunk_id: {chunk_id}")

    if person_names:
        metadata["person_names"] = person_names

    elapsed = time.time() - start_time
    if elapsed > 1.0:
        logger.debug(f"元数据构建耗时: {elapsed:.2f}s, doc_id={doc_id}, is_full={is_full_document}")

    return metadata


async def build_all_metadata_async(docs, doc_map):
    """异步批量构建元数据（利用并发加速）"""
    logger.info("🚀 开始异步批量构建元数据...")
    start_time = time.time()

    chunk_tasks = []
    for i, d in enumerate(docs):
        fname = os.path.basename(d.metadata["file_path"])
        doc_id = hashlib.md5(fname.encode()).hexdigest()[:10]

        task = build_metadata(
            text=d.page_content,
            fname=fname,
            doc_id=doc_id,
            chunk_id=f"{doc_id}_{i}",
            is_full_document=False
        )
        chunk_tasks.append((i, task))

    logger.info(f"📦 提交 {len(chunk_tasks)} 个 chunk 元数据任务...")
    for i, task in chunk_tasks:
        metadata = await task
        docs[i].metadata.update(metadata)

    logger.info(f"✅ Chunk 元数据构建完成")

    doc_level_texts = []
    doc_level_meta = []

    doc_tasks = []
    for name, chunks in doc_map.items():
        full_text = "\n".join(chunks)
        doc_id = hashlib.md5(name.encode()).hexdigest()[:10]

        task = build_metadata(
            text=full_text,
            fname=name,
            doc_id=doc_id,
            chunk_id=f"{doc_id}_full",
            is_full_document=True
        )
        doc_tasks.append((name, full_text, task))

    logger.info(f"📦 提交 {len(doc_tasks)} 个 doc 元数据任务...")
    for name, full_text, task in doc_tasks:
        full_metadata = await task
        doc_level_texts.append(full_text)
        doc_level_meta.append(full_metadata)

    elapsed = time.time() - start_time
    logger.info(f"✅ 所有元数据构建完成，总耗时: {elapsed:.2f}s")

    return doc_level_texts, doc_level_meta
