import hashlib
import os
import re
import time
from collections import Counter
from functools import lru_cache
from typing import List, Set, Dict, Any, Optional, Literal, Tuple

from backend.config.rag import DOC_TYPE_RULES, FILENAME_TYPE_HINTS, FOLDER_TYPE_HINTS, TIME_PATTERNS, DOMAIN_RULES, SUMMARY_MAX_LENGTH
from backend.config.llm import LLM_REQUEST_TIMEOUT
from backend.infra.llm import llm
from backend.rag.preprocessing.entity import extract_person_names

from backend.rag.preprocessing.keyword import extract_doc_keywords, extract_chunk_keywords
from backend.shared.async_utils import async_safe_call_with_timeout

from backend.shared.logger import logger


# =====================================================
# 文档类型分类（V2 加权计分 + 文件名辅助 + LLM 胶着仲裁）
# =====================================================

# legal/compliance/policy 得分差 < 此阈值时触发 LLM 仲裁
_ARBITRATION_THRESHOLD = 5

# LLM 仲裁提示词 — 轻量，只选类型不生成内容
_ARBITRATION_PROMPT = """你是电商文档分类专家。以下文档的类型有歧义，请从候选类型中选择最匹配的一个。

候选类型（三选一）: {candidates}
文档内容（前 1000 字）:
{text}

只输出一个类型名，不要额外解释:"""


def classify_doc_type(text: str, filename: str = "", file_path: str = "") -> str:
    """V2 加权计分分类 + 路径上下文 + LLM 胶着仲裁（兼容旧调用方）。"""
    result, _ = classify_with_confidence(text, filename, file_path)
    return result


def assess_quality(text: str) -> dict:
    """质量门禁：多维度文档质量评分。

    Returns: {score: int 0-100, status: pass/warn/reject, dimensions: dict, issues: [str]}
    """
    issues: list[str] = []
    chars = len(text.strip())

    # 文本完整度（0-40）
    if chars < 50:
        completeness = 0
        issues.append("文档过短(<50字符)")
    elif chars < 100:
        completeness = 10
        issues.append("文档偏短(<100字符)")
    elif chars < 500:
        completeness = 25
    elif chars < 1000:
        completeness = 35
    else:
        completeness = 40

    # 结构完整度（0-30）
    has_heading = bool(re.search(r'^#{1,6}\s+', text, re.MULTILINE))
    has_chapter = bool(re.search(r'(第[一二三四五六七八九十\d]+章|[一二三四五六七八九十]+、)', text))
    has_paragraph = chars > 200
    structure = (10 if has_heading else 0) + (10 if has_chapter else 0) + (10 if has_paragraph else 0)
    if structure == 0:
        issues.append("无标题/章节结构")

    # 噪音比例（0-20，越低越好）
    alpha_chars = sum(1 for c in text if c.isalpha() or '一' <= c <= '鿿')
    noise_ratio = 1 - alpha_chars / max(chars, 1)
    if noise_ratio < 0.1:       noise_score = 20
    elif noise_ratio < 0.3:     noise_score = 15
    elif noise_ratio < 0.5:     noise_score = 10
    elif noise_ratio < 0.7:     noise_score = 5
    else:
        noise_score = 0
        issues.append(f"噪音过高({noise_ratio:.0%})")

    # 重复检测（简易，0-10）
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    dup_ratio = 1 - len(set(lines)) / max(len(lines), 1) if lines else 0
    if dup_ratio < 0.1:      unique_score = 10
    elif dup_ratio < 0.3:    unique_score = 5
    else:
        unique_score = 0
        issues.append("内容重复率偏高")

    total = completeness + structure + noise_score + unique_score
    status = "reject" if total < 40 else ("warn" if total < 60 else "pass")
    passed = total >= 40

    return {
        "score": total,
        "passed": passed,
        "status": status,
        "issues": issues,
        "dimensions": {
            "completeness": completeness, "structure": structure,
            "noise": noise_score, "uniqueness": unique_score,
            "total": total,
        },
    }


def compute_minhash(text: str, n_gram: int = 3, n_hashes: int = 128) -> list[int]:
    """MinHash 签名 — 用于近似文档去重（无需 LLM）。

    返回 128 个最小 hash 值作为文档指纹。Jaccard 相似度 ≈ 签名匹配比例。
    """
    import hashlib
    # 提取 n-gram token（中文按字级 3-gram）
    tokens: set[str] = set()
    clean = re.sub(r'\s+', '', text)[:5000]  # 取前 5000 字，去空格
    for i in range(len(clean) - n_gram + 1):
        tokens.add(clean[i:i + n_gram])

    if not tokens:
        return [0] * n_hashes

    # 对每个 hash 函数，取所有 token 的最小 hash
    signature: list[int] = []
    for seed in range(n_hashes):
        min_val = 2**64
        for t in tokens:
            h = int(hashlib.md5(f"{seed}:{t}".encode()).hexdigest()[:16], 16)
            min_val = min(min_val, h)
        signature.append(min_val)
    return signature


def minhash_similarity(sig1: list[int], sig2: list[int]) -> float:
    """两个 MinHash 签名的 Jaccard 相似度估计。"""
    if len(sig1) != len(sig2) or not sig1:
        return 0.0
    matches = sum(1 for a, b in zip(sig1, sig2) if a == b)
    return matches / len(sig1)


_SIMILARITY_THRESHOLD = 0.85  # 相似度 > 85% 视为近重复


def classify_with_confidence(text: str, filename: str = "", file_path: str = "", return_detail: bool = False):
    """V2 加权计分分类 + 路径上下文 + confidence 计算。

    Returns: (doc_type, confidence) 或 (doc_type, confidence, detail_dict)
    """
    detail: dict = {"scores": {}, "filename_hits": [], "title_hits": [], "folder_hit": None, "llm_fallback": False} if return_detail else {}
    if return_detail:
        detail["keyword_hits"] = []  # [{type, keyword, weight}, ...]
    text_lower = text[:6000].lower()

    # ── 加权计分 ──
    scores: dict[str, int] = {}
    for doc_type, rules in DOC_TYPE_RULES.items():
        score = 0
        for pattern, weight in rules:
            found = re.findall(pattern, text_lower)
            if found:
                score += weight * len(found)
                if return_detail:
                    for kw in found:
                        kw_str = kw if isinstance(kw, str) else str(kw[0] if isinstance(kw, tuple) else kw)
                        detail["keyword_hits"].append({"type": doc_type, "keyword": kw_str, "weight": weight})
        if score > 0:
            scores[doc_type] = score
            if return_detail:
                detail["scores"][doc_type] = score

    # ── 文件名辅助 ──
    if filename:
        fname_no_ext = os.path.splitext(filename)[0]
        for hint, hint_type in FILENAME_TYPE_HINTS.items():
            if hint.lower() in fname_no_ext.lower():
                scores[hint_type] = scores.get(hint_type, 0) + 30
                if return_detail:
                    detail["filename_hits"].append(f"{hint} → {hint_type} +30")
                    detail["keyword_hits"].append({"type": hint_type, "keyword": hint, "weight": 30, "source": "filename"})
                logger.debug(f"[Classify] 文件名命中: {hint} → {hint_type} +30")

    # ── 标题关键词辅助 ──
    # 扫描文档前几行的标题，提取强信号词
    heading_lines = re.findall(r'^#{1,3}\s+(.+)$', text[:3000], re.MULTILINE)
    heading_lines += re.findall(r'^(?:第[一二三四五六七八九十\d]+章|第[一二三四五六七八九十\d]+节)\s*(.*)$', text[:3000], re.MULTILINE)
    _TITLE_TYPE_HINTS: dict[str, str] = {
        "合规": "compliance", "GDPR": "compliance", "数据保护": "compliance",
        "制度": "policy", "管理": "policy", "规范": "policy",
        "财务": "financial", "预算": "financial", "报销": "financial",
        "合同": "legal", "法律": "legal", "保密": "legal",
        "FAQ": "faq", "常见问题": "faq",
        "商品": "product_spec", "规格": "product_spec", "SKU": "product_spec",
        "SOP": "sop", "流程": "sop", "操作": "sop",
    }
    for h in heading_lines:
        h_lower = h.lower()
        for hint, hint_type in _TITLE_TYPE_HINTS.items():
            if hint.lower() in h_lower:
                scores[hint_type] = scores.get(hint_type, 0) + 20
                if return_detail:
                    detail["title_hits"].append(f"{hint} → {hint_type} +20")
                    detail["keyword_hits"].append({"type": hint_type, "keyword": hint, "weight": 20, "source": "title"})
                logger.debug(f"[Classify] 标题命中: {hint} → {hint_type} +20")
                if hint_type not in scores:
                    scores[hint_type] = 20

    # ── 文件夹路径辅助（强信号，直接 +0.3 confidence）──
    folder_bonus: str | None = None
    if file_path:
        path_lower = os.path.dirname(file_path).lower().replace("\\", "/")
        for hint, hint_type in FOLDER_TYPE_HINTS.items():
            if hint.lower() in path_lower.split("/"):
                scores[hint_type] = scores.get(hint_type, 0) + 40
                folder_bonus = hint_type
                if return_detail:
                    detail["folder_hit"] = f"{hint} → {hint_type} +40"
                logger.debug(f"[Classify] 文件夹命中: {hint} → {hint_type} +40 (path={path_lower})")
                break  # 一个文件夹只匹配第一个命中

    if not scores:
        return "general", 0.0

    # 同步 detail["scores"] 到最终 scores（标题/文件名可能后续改了 scores）
    if return_detail:
        detail["scores"] = dict(scores)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_type, top_score = sorted_scores[0]

    # confidence = top_score / (top_score + second_score)，单类时 = 1.0
    # 文件夹命中 → 额外 +0.3
    second_score = sorted_scores[1][1] if len(sorted_scores) >= 2 else 0
    confidence = top_score / (top_score + second_score) if (top_score + second_score) > 0 else 1.0
    if folder_bonus and top_type == folder_bonus:
        confidence = min(confidence + 0.3, 1.0)
    confidence = round(min(confidence, 1.0), 2)

    # ── 胶着仲裁 ──
    arbitration_candidates = {"legal", "compliance", "policy"}
    top3 = sorted_scores[:3]
    top3_types = {t for t, _ in top3}
    close_set = top3_types & arbitration_candidates
    if len(close_set) >= 2:
        scores_in_set = [(t, s) for t, s in top3 if t in close_set]
        diff = scores_in_set[0][1] - scores_in_set[1][1] if len(scores_in_set) >= 2 else 999
        if diff < _ARBITRATION_THRESHOLD:
            candidates = ", ".join(t for t, _ in scores_in_set[:3])
            logger.info(f"[Classify] 胶着仲裁: {scores_in_set[:3]}, diff={diff} < {_ARBITRATION_THRESHOLD}")
            try:
                from backend.infra.llm import llm
                result = llm.invoke(_ARBITRATION_PROMPT.format(candidates=candidates, text=text[:1000]))
                result_text = result.content.strip() if hasattr(result, "content") else str(result).strip()
                for t in close_set:
                    if t in result_text:
                        logger.info(f"[Classify] LLM 仲裁结果: {t}")
                        return t, 0.95
                logger.warning(f"[Classify] LLM 仲裁返回未知结果: {result_text[:100]}")
            except Exception as e:
                logger.warning(f"[Classify] LLM 仲裁失败，使用最高分: {e}")

    logger.debug(f"[Classify] {filename} → {top_type} (score={top_score}, confidence={confidence})")
    if return_detail:
        return top_type, confidence, detail
    return top_type, confidence


def analyze_complexity(text: str, keyword_count: int, confidence: float) -> dict:
    """文档复杂度 + 风险分析 — 用于 LLM Router 决策。

    Returns:
        {headings_count, table_rows, legal_refs, legal_refs_list,
         risk_keyword_hits, risk_keywords, keyword_count, structure_score,
         classification_clear}
    """
    chars = len(text)

    # 结构特征
    headings = len(re.findall(r'^#{1,6}\s+', text, re.MULTILINE))
    table_rows = len(re.findall(r'^\|.+\|', text, re.MULTILINE))
    legal_refs_raw = re.findall(r'(第[一二三四五六七八九十百\d]+条|§\s*\d+)', text)
    legal_refs = len(legal_refs_raw)

    # 结构评分（内部 Router 使用）
    structure_score = 0
    if headings > 10:       structure_score += 20
    elif headings > 3:      structure_score += 10
    if table_rows > 5:      structure_score += 15
    if legal_refs > 0:      structure_score += 15

    # 风险关键词
    _RISK_PATTERNS = re.compile(
        r'合同|GDPR|隐私|审计|监管|处罚|罚款|合规|诉讼|知识产权|保密',
    )
    risk_matches: list[str] = _RISK_PATTERNS.findall(text[:3000])
    risk_keyword_hits = len(risk_matches)
    risk_keywords: list[str] = sorted(set(m.lower().capitalize() for m in risk_matches))

    # LLM 决策维度计分（总分 100）
    dim_headings = 10 if headings > 5 else (5 if headings > 2 else 0)
    dim_tables = 5 if table_rows > 0 else 0
    dim_legal = 15 if legal_refs > 0 else 0
    dim_risk = min(risk_keyword_hits * 15, 30)  # 每个风险词 15，上限 30
    dim_length = 10 if chars > 2000 else (5 if chars > 500 else 0)
    dim_conf = 0 if confidence < 0.3 else 10  # 分类不确定时 +0
    complexity_score = dim_headings + dim_tables + dim_legal + dim_risk + dim_length + dim_conf

    return {
        "headings_count": headings,
        "table_rows": table_rows,
        "legal_refs": legal_refs,
        "legal_refs_list": list(set(legal_refs_raw))[:10],
        "risk_keyword_hits": risk_keyword_hits,
        "risk_keywords": risk_keywords,
        "keyword_count": keyword_count,
        "structure_score": min(structure_score, 50),
        "classification_clear": confidence >= 0.7,
        "complexity_score": min(complexity_score, 100),
        "dimensions": {
            "headings": dim_headings, "tables": dim_tables, "legal": dim_legal,
            "risk": dim_risk, "length": dim_length, "confidence": dim_conf,
            "total": min(complexity_score, 100),
        },
    }

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

def detect_business_domain(text: str, min_score: int = 2, return_detail: bool = False):
    """识别业务领域，返回得分最高的领域，若最高分低于阈值则返回"general" """
    scores: Dict[str, int] = Counter()
    detail: dict = {"scores": {}, "hits": []} if return_detail else {}
    text_lower = text.lower()
    for domain, kw_weights in DOMAIN_RULES.items():
        domain_score = 0
        matched = []
        for kw, weight in kw_weights.items():
            if kw.lower() in text_lower:
                domain_score += weight
                if return_detail:
                    matched.append(f"{kw}+{weight}")
        if domain_score > 0:
            scores[domain] = domain_score
            if return_detail and matched:
                detail["scores"][domain] = domain_score
                detail["hits"].extend(matched)

    if not scores:
        if return_detail:
            return "general", detail
        return "general"
    best_domain, best_score = max(scores.items(), key=lambda x: x[1])
    result = best_domain if best_score >= min_score else "general"
    if return_detail:
        return result, detail
    return result


# =====================================================
# 文档摘要（改进：智能截断、保留语义）
# =====================================================

def _fallback_questions(chunk_text: str) -> list[str]:
    """LLM 未生成问题或长度不符时的兜底 — 给 1 个无害占位问句。

    设计原则：fallback 必须"无害"（不引入噪声）而非"有用"。
    宁可让该 chunk 召回率低，也不要因为错误问题把不相关问句召回来。
    """
    import re as _re
    # 取 chunk 首句（按 。？！；\\n 切）
    first_sentence = _re.split(r'[。！？；\n]', chunk_text.strip(), maxsplit=1)[0].strip()
    if not first_sentence:
        return []
    # 通用兜底：只问"这段内容讲什么"，不含 chunk 特有信息（避免引入噪声）
    return ["这段内容讲什么？"]


def enrich_metadata_llm(text: str, doc_type: str, chunks_text: list[str] | None = None) -> dict:
    """合并关键词+摘要+实体(+ 模拟问题)为一次 LLM 调用（省 N 次 API 往返）。

    Args:
        text: 文档全文（或摘要采样）
        doc_type: 文档类型
        chunks_text: chunk 文本列表（提供时同时生成每 chunk 的模拟问题；为 None 时不生成）

    Returns:
        {"keywords": [...], "summary": "...", "entities": [...], "tokens": {...},
         "questions_by_chunk": [[q1, q2, q3], ...]}  # 长度 == len(chunks_text)
    失败返回空 dict，调用方降级到独立调用。
    """
    from backend.config.rag import DOC_LLM_MODEL
    from backend.config import LLM_MODEL
    import json as _json

    safe_text = text

    # chunks_text 提供时，附加编号预览到 prompt，让 LLM 按 chunk 编号生成问题
    chunks_block = ""
    questions_field = ""
    extra_schema = ""
    if chunks_text:
        chunks_block = "\n\n文档已切分为以下 chunks（编号从 0 开始）:\n"
        for idx, ct in enumerate(chunks_text):
            preview = ct[:300].replace("\n", " ")
            chunks_block += f"\n[Chunk #{idx}]\n{preview}...\n"
        questions_field = (
            f"- simulated_questions 是数组,长度必须等于 chunks 数量({len(chunks_text)})。"
            f"每个元素是该 chunk 的 2-3 个模拟用户提问(必须严格基于该 chunk 内容,口语化疑问句,"
            f"只问'是什么/怎么样/标准是什么/如何定义')"
        )
        # 移出 f-string 表达式（Python 3.10 不允许 f-string 内反斜杠）
        extra_schema = ', "simulated_questions": [["<问题 1>", "<问题 2>"], ...]'

    prompt = f"""你是专业 RAG 元数据提取专家。从文档提取 1 个 JSON 对象,严禁任何额外内容。

严格规则:
- summary 必须是 1-3 句中文摘要,严格控制在 200 个汉字以内(超出部分将被系统强制截断,视为不合格输出)
  纯自然语言,严禁用 markdown 标题符号/列表/换行/星号
- keywords 是 10 个以内的关键词数组
- entities 是 5 个以内的实体数组(每项含 name 和 type 字段,type 取 regulation/person/platform/brand)
{questions_field}
- 严禁在 JSON 外加解释、严禁使用 markdown 围栏、严禁重复文档原文当摘要

JSON Schema:
{{"summary": "<1-3 句中文摘要,<=200字>", "keywords": ["<词 1>", ...], "entities": [{{"name":"<名称>","type":"<类型>"}}, ...]{extra_schema}}}

文档:
{safe_text}
{chunks_block}

只返回这个 JSON,无其他文字:"""

    try:
        if DOC_LLM_MODEL:
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=DOC_LLM_MODEL, temperature=0.0, num_ctx=4096, request_timeout=60)
            result = llm.invoke(prompt)
            model_used = DOC_LLM_MODEL
            tokens = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "model": model_used}
        else:
            from backend.infra.llm import llm
            from backend.infra.llm.proxy import _last_call_meta
            for k in list(_last_call_meta.keys()):
                _last_call_meta.pop(k, None)
            result = llm.invoke(prompt)
            model_used = LLM_MODEL
            tokens = {
                "prompt_tokens": _last_call_meta.get("prompt_tokens", 0),
                "completion_tokens": _last_call_meta.get("completion_tokens", 0),
                "cost_usd": _last_call_meta.get("cost_usd", 0),
                "model": model_used,
            }

        content = result.content.strip() if hasattr(result, "content") else str(result).strip()
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            data = _json.loads(match.group())
            keywords = data.get("keywords", [])
            if isinstance(keywords, list):
                keywords = [str(k).strip() for k in keywords if str(k).strip() and len(str(k).strip()) > 1][:10]
            summary = _smart_truncate(str(data.get("summary", "")), 200)
            # P0 fix: 如果 LLM 把原文填进 summary (含 markdown 标题/加粗), fallback 到抽取式
            if summary and (summary.startswith("#") or "**" in summary):
                logger.warning("[Enrich LLM] summary 疑似原文 fallback, 改用抽取式")
                summary = _extract_first_sentences(safe_text, 2)
            entities = data.get("entities", [])
            if not isinstance(entities, list):
                entities = []

            # 解析 simulated_questions + 严格 fallback
            questions_by_chunk: list[list[str]] = []
            if chunks_text:
                raw_q = data.get("simulated_questions", [])
                expected_len = len(chunks_text)
                if isinstance(raw_q, list) and len(raw_q) == expected_len:
                    for i, qs in enumerate(raw_q):
                        if isinstance(qs, list):
                            # 取前 3 个非空问题，过滤过短（< 4 字）
                            cleaned = [str(q).strip() for q in qs
                                       if str(q).strip() and len(str(q).strip()) >= 4][:3]
                            questions_by_chunk.append(cleaned if cleaned else _fallback_questions(chunks_text[i]))
                        else:
                            questions_by_chunk.append(_fallback_questions(chunks_text[i]))
                else:
                    logger.warning(
                        f"[Enrich LLM] simulated_questions 缺失或长度不符 "
                        f"({len(raw_q) if isinstance(raw_q, list) else 0}/{expected_len}), 全部 fallback"
                    )
                    questions_by_chunk = [_fallback_questions(ct) for ct in chunks_text]

            logger.info(
                f"[Enrich LLM] {model_used} → {len(keywords)}kw + {len(summary)}字摘要 + "
                f"{len(entities)}实体 + {len(questions_by_chunk)}chunks问题"
            )
            return {
                "keywords": keywords,
                "summary": summary,
                "entities": entities,
                "tokens": tokens,
                "questions_by_chunk": questions_by_chunk,
            }
        else:
            # JSON 解析失败，尝试拆分行
            lines = [l.strip() for l in content.split("\n") if l.strip() and len(l.strip()) > 1]
            kws = [l for l in lines if not l.startswith("{")][:10]
            return {
                "keywords": kws,
                "summary": _extract_first_sentences(content, 2),
                "entities": [],
                "tokens": tokens,
                "questions_by_chunk": [_fallback_questions(ct) for ct in chunks_text] if chunks_text else [],
            }
    except Exception as e:
        logger.warning(f"[Enrich LLM] 失败: {e}")
        return {}


def _extract_first_sentences(text: str, n: int = 2) -> str:
    """抽取式摘要: 优先剥 markdown 标记再按句切分, 取前 n 句.

    P0 fix: LLM 偶尔不遵守 prompt 把原文填进 summary, 退而求其次用抽取式.
    输入可能是 LLM 输出(JSON 格式) 或原文, 需先剥 markdown.
    """
    if not text:
        return ""
    import re as _re
    # 0. 如果是 JSON, 优先 extract summary 字段 (LLM 真正的输出)
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        try:
            import json as _json
            data = _json.loads(t)
            if isinstance(data, dict) and isinstance(data.get("summary"), str):
                inner = data["summary"].strip()
                if inner and len(inner) > 4:
                    text = inner
        except Exception:
            pass
    # 1. 剥 markdown 标记
    text = _re.sub(r"^#{1,6}\s+", "", text, flags=_re.MULTILINE)  # 标题 # ## ###
    text = _re.sub(r"\*\*([^*]+)\*\*", r"", text)  # 加粗
    text = _re.sub(r"^\s*[-*+]\s+", "", text, flags=_re.MULTILINE)  # 列表
    # 2. 按中英文句号/问号/感叹号/空白切分
    parts = _re.split(r"[。！？!?.\s]+", text)
    parts = [p.strip() for p in parts if p and len(p.strip()) > 4][:n]
    if not parts:
        return text[:150].strip()
    summary = "。".join(parts)
    if not summary.endswith("。"):
        summary += "。"
    return summary[:200]


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
    """使用LLM生成摘要 — DOC_LLM_MODEL 有值走本地 Ollama，否则走 _LLMProxy。
    <2KB 文档直接提取前两段当摘要，不调 LLM。

    Returns: (summary, [person_names])
    """
    from backend.config.rag import DOC_LLM_MODEL

    # <2KB 文档：提取式摘要，不调 LLM
    if len(text) < 2000:
        sentences = re.split(r'[。!?\n]', text)
        extractive = '。'.join(s[:200] for s in sentences[:3] if s.strip())
        extractive = _smart_truncate(extractive, max_length)
        return extractive, []

    safe_text = text.encode('utf-8', errors='ignore')[:4000].decode('utf-8', errors='ignore')
    prompt = f"""请用 1-2 句话概括以下文档的核心内容。保留关键术语、数字、条款编号。

文档：
{safe_text}

摘要（≤{max_length}字）："""

    if DOC_LLM_MODEL:
        # 本地 Ollama —— 同步调用（indexer 线程内）
        try:
            from langchain_ollama import ChatOllama
            llm_local = ChatOllama(model=DOC_LLM_MODEL, temperature=0.0, num_ctx=4096, request_timeout=30)
            response = llm_local.invoke(prompt)
            summary = response.content.strip() if hasattr(response, "content") else str(response).strip()
            summary = _smart_truncate(summary, max_length)
            logger.info(f"[Summary Ollama] {DOC_LLM_MODEL} → {len(summary)}字")
            return summary, []
        except Exception as e:
            logger.warning(f"[Summary Ollama] 失败: {e}")
            return "", []

    # Cloud API —— 异步调用
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

        summary = response.content.strip() if hasattr(response, "content") else str(response).strip()
        summary = _smart_truncate(summary, max_length)
        logger.info(f"[Summary API] → {len(summary)}字")
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
        kw_list = extract_chunk_keywords(text)  # List[str]
    else:
        kw_result = extract_doc_keywords(text)   # KeywordResult 对象
        kw_list = kw_result.all_keywords()
        logger.debug(f"Doc关键词 ({len(kw_list)}个): {kw_list[:5]}..., 文档id: {doc_id}")

    if kw_list:
        metadata["keywords"] = kw_list

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
