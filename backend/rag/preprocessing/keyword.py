"""关键词模块 — 去词表化改造（P0-1/P0-2，2026-10-19）。

治理结论：人工产品词表、品牌词表、领域词表、业务术语词表与 Jieba 排名
提取全部退役——检索召回由「原文 BM25 + Chunk Embedding + RRF + Rerank」
承担（BM25 语料 = chunk 原文，从未消费关键词字段）；Stage1 关键词门控
已移除（retrievers._filter_docs_by_keywords 直通版）。

本模块仅保留：
1. 通用标识符识别（extract_rule_keywords / extract_chunk_keywords）：
   只匹配「字母前缀+数字主体」这类有明确边界结构的值（SKU/型号/编号/
   错误码），零宽断言防短型号误命中长型号（AB123 ≠ AB1234-X 的前缀）；
   不维护任何业务词表；无法识别的名称留在原文由 BM25/向量负责。
2. KeywordResult 契约壳与 dedupe_keywords_normalized（既有消费者兼容）。
3. doc 级普通关键词停发（extract_doc_keywords / _typed 返回空产出）——
   doc_keywords 字段不再有检索消费者，仅历史数据/展示兼容。

已删除：动态词库热加载、DOMAIN_RULES 领域词臂、SIGNAL_RULES 信号词、
Jieba extract_tags、LLM 关键词提取全链（qwen batch/单条/DeepSeek/
ollama/评分决策）。LLM_FORCED_TYPES 唯一非关键词消费点
（indexer chunk LLM 开关）已随 P0-2 一并移除。
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List
from functools import lru_cache

from backend.shared.logger import logger

# 兼容导出：外部曾从 config 间接引用；保留空集合避免悬空 import
LLM_SCORE_THRESHOLD = 50
LLM_FORCED_TYPES: set[str] = set()
LLM_FALLBACK_TYPES: set[str] = set()

# 通用格式实体：必须包含字母和数字；允许常见分隔符，但不维护行业词表。
# 前后边界防止把更长型号切成短前缀。
_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"(?=[A-Za-z0-9_/-]*\d)"
    r"([A-Za-z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)*)"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_STANDARD_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{1,8}/[A-Za-z]{1,8}\s?"
    r"\d{2,8}(?:[-.]\d{1,4})?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
# 编号标签只描述通用格式，不绑定任何行业。
_LABELLED_ID_RE = re.compile(
    r"(?:编号|单号|订单(?:编号|标识|号)|错误码|SKU|No\.?|"
    r"error\s*code|order\s*(?:id|number|no\.?))"
    r"[：:=\s]*([A-Za-z0-9][A-Za-z0-9_.:/-]{1,47})"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_ENTITY_LABEL_RE = re.compile(
    r"(?:产品名|产品名称|品牌|制度名|制度名称|标准编号|标准名|型号|SKU|"
    r"编号|单号|订单号|错误码|版本)[：:\s]*([^\s，,。；;、]{2,80})",
    re.IGNORECASE,
)
_QUOTED_ENTITY_RE = re.compile(r"[《〈「『【“\"]([^》〉」』】”\"]{2,80})[》〉」』】”\"]")
_ENTITY_SUFFIXES = (
    "管理办法", "制度", "办法", "条例", "规范", "标准", "手册", "协议",
    "研究院", "大学", "有限公司", "股份公司", "系统", "平台", "产品",
    "品牌", "型号", "软件", "服务", "系列", "版本",
)
_NON_ALNUM_EDGE_RE = re.compile(r"^[\s:：,，。;；()（）\[\]【】《》]+|"
                                r"[\s:：,，。;；()（）\[\]【】《》]+$")

# 只过滤明确的普通主题词，避免普通词被误标成精确实体；不承载行业词库。
_GENERIC_KEYWORD_STOPWORDS = frozenset({
    "申请", "处理", "业务", "员工", "hr", "年假", "病假", "报销", "加班",
})


def normalize_identifier(value: str) -> str:
    """使用 NFKC 与大写规范化格式实体，保留可读分隔符。"""
    return unicodedata.normalize("NFKC", str(value or "")).strip().upper()


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def filter_exact_llm_keywords(text: str, candidates: list) -> list[str]:
    """只接受原文中可精确匹配的专名候选；普通主题词和臆造值会被丢弃。"""
    if not text or not candidates:
        return []

    normalized_text = _normalize_match_text(text)
    output: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        word = candidate.strip().strip(" \t\r\n\"'`，,。.!！?？:：;；()（）[]【】")
        normalized = _normalize_match_text(word)
        if not normalized or len(normalized) > 64:
            continue
        if normalized in _GENERIC_KEYWORD_STOPWORDS:
            continue
        if normalized not in normalized_text:
            continue
        key = normalize_identifier(word).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(word)
        if len(output) >= 8:
            break
    return output


@dataclass
class KeywordResult:
    """关键词提取结果契约（保留供既有消费者兼容）。"""

    rule_keywords: List = field(default_factory=list)
    llm_keywords: List = field(default_factory=list)
    llm_tokens: Dict = field(default_factory=dict)
    llm_strategy: str = ""
    llm_decision: Dict = field(default_factory=dict)

    def all_keywords(self) -> List[str]:
        return [k.get("word") if isinstance(k, dict) else k
                for k in (self.rule_keywords + self.llm_keywords)]


@lru_cache(maxsize=1024)
def extract_identifiers_cached(text: str) -> tuple:
    """通用标识符识别（确定、可复现；无词表、无随机性）。"""
    if not text:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for pattern in (_IDENTIFIER_RE, _STANDARD_IDENTIFIER_RE, _LABELLED_ID_RE):
        for m in pattern.finditer(text):
            value = normalize_identifier(m.group(1))
            if not any(char.isdigit() for char in value) or len(value) > 48:
                continue
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                out.append(value)
    return tuple(out[:12])


def extract_identifiers(text: str) -> list[str]:
    """通用标识符（SKU/型号/编号/错误码）列表 — 标识符识别唯一入口。"""
    return list(extract_identifiers_cached(text))


def contains_exact_source_match(text: str, candidate: str) -> bool:
    """按原文精确匹配实体；拉丁字母与数字项需同时满足 token 边界。"""
    value = str(candidate or "").strip()
    if not text or not value:
        return False
    if not any(ch.isascii() and ch.isalnum() for ch in value):
        return value in text
    left = r"(?<![A-Za-z0-9])" if value[0].isascii() and value[0].isalnum() else ""
    right = r"(?![A-Za-z0-9])" if value[-1].isascii() and value[-1].isalnum() else ""
    return re.search(left + re.escape(value) + right, text, re.IGNORECASE) is not None


def extract_precise_keywords(
    text: str, candidates=(), *, exact_named_entities=(),
) -> list[str]:
    """只保留有原文证据和通用格式信号的精确实体。

    格式信号来自编号模式、明确实体标签、书名/名称引号、实体类型后缀，
    或具有内部大小写/较长全大写形式的拉丁专名；不使用行业词表或主题词排序。
    """
    if not text:
        return []

    identifiers = extract_identifiers(text)
    source_identifier_by_key: dict[str, str] = {}
    for pattern in (_IDENTIFIER_RE, _STANDARD_IDENTIFIER_RE, _LABELLED_ID_RE):
        for match in pattern.finditer(text):
            source_value = match.group(1).strip()
            key = normalize_identifier(source_value).casefold()
            source_identifier_by_key.setdefault(key, source_value)
    identifiers = [
        source_identifier_by_key.get(value.casefold(), value)
        for value in identifiers
    ]
    identifier_keys = {value.casefold() for value in identifiers}
    labelled = {
        match.group(1).strip().casefold()
        for match in _ENTITY_LABEL_RE.finditer(text)
    }
    quoted = {
        match.group(1).strip().casefold()
        for match in _QUOTED_ENTITY_RE.finditer(text)
    }
    out = list(identifiers)
    seen = set(identifier_keys)

    # 统一元数据链中由 LLM 标注为专名类型的实体（组织、产品、制度/标准）
    # 可补足格式规则无法识别的中文专名；仍要求原文逐字匹配。
    for raw in exact_named_entities or ():
        value = str(raw or "").strip()
        if not value or len(value) > 80 or not contains_exact_source_match(text, value):
            continue
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            out.append(value)

    for raw in candidates or ():
        if raw is None:
            continue
        value = str(raw).strip()
        value = _NON_ALNUM_EDGE_RE.sub("", value)
        if not value or len(value) > 80:
            continue
        key = value.casefold()
        if key in seen or not contains_exact_source_match(text, value):
            continue

        has_format_evidence = (
            key in labelled
            or key in quoted
            or any(
                key.endswith(suffix)
                and (
                    len(value) >= len(suffix) + 3
                    or any(
                        ch.isascii() and ch.isalnum()
                        for ch in value[:-len(suffix)]
                    )
                )
                for suffix in _ENTITY_SUFFIXES
            )
        )
        latin = "".join(ch for ch in value if ch.isascii() and ch.isalpha())
        if latin:
            has_format_evidence = has_format_evidence or (
                len(latin) >= 4 and any(ch.islower() for ch in latin)
                and any(ch.isupper() for ch in latin[1:])
            ) or (len(latin) >= 4 and latin.isupper())
        if not has_format_evidence:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= 12:
            break
    return out


# ── 兼容契约（既有调用方零改动）──────────────────────────────────────

def extract_chunk_keywords_cached(text: str, doc_type: str = "general",
                                  top_k: int = 6) -> list[str]:
    """通用标识符识别（原词表/Jieba 版已退役；检索主链已无调用方）。"""
    return extract_identifiers(text)[:top_k] if text else []


def extract_chunk_keywords(text: str, top_k: int = 6,
                           doc_type: str = "general") -> list[str]:
    """同上（无缓存入口）。"""
    return extract_chunk_keywords_cached(text or "", doc_type=doc_type, top_k=top_k)


def extract_rule_keywords(text: str, doc_type: str = "general", top_k: int = 6) -> list:
    """按原文提取通用标识符。"""
    return extract_chunk_keywords_cached(text or "", doc_type=doc_type, top_k=top_k)


def extract_doc_keywords(text: str, top_k: int = 10) -> KeywordResult:
    """doc 级普通关键词停发（P0-2）：恒空产出，字段兼容保留。"""
    return KeywordResult(rule_keywords=[], llm_keywords=[])


def extract_doc_keywords_typed(text: str, doc_type: str = "general",
                               top_k: int = 10, confidence: float = 0.0,
                               allow_llm: bool = False) -> KeywordResult:
    """同上（保留签名兼容；allow_llm 参数忽略，绝不调 LLM）。"""
    return extract_doc_keywords(text, top_k=top_k)


def dedupe_keywords_normalized(words: list, limit: int | None = None) -> list:
    """关键词大小写归一去重（2026-10-19 引入，保留）。"""
    out: list = []
    seen: set = set()
    for w in words or []:
        if not w:
            continue
        key = normalize_identifier(w).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(w.strip())
        if limit is not None and len(out) >= limit:
            break
    return out


# 向后兼容别名（历史调用方）
extract_doc_keywords_rule = extract_rule_keywords
