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
from dataclasses import dataclass, field
from typing import Dict, List
from functools import lru_cache

from backend.shared.logger import logger

# 兼容导出：外部曾从 config 间接引用；保留空集合避免悬空 import
LLM_SCORE_THRESHOLD = 50
LLM_FORCED_TYPES: set[str] = set()
LLM_FALLBACK_TYPES: set[str] = set()

# 通用标识符：字母前缀(1-10) + 可选连字符 + 数字主体(2-12) + 尾部字母数字；
# 前后零宽断言排除更长的字母数字串 → AB123 不会命中 AB1234-X
_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z]{1,10}-?[0-9]{2,12}(?:-[A-Za-z0-9]{1,8})?[A-Za-z0-9]{0,4})(?![A-Za-z0-9-])"
)
# 显式编号标注（如「(编号 FAQ-001)」「单号: SO2024…」）放宽到 1 位起
_LABELLED_ID_RE = re.compile(
    r"(?:编号|单号|订单号|错误码|型号|SKU|sku|No\.?)[：:\s]*([A-Za-z0-9][A-Za-z0-9_-]{1,31})"
    r"(?![A-Za-z0-9-])"
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
    for m in _IDENTIFIER_RE.finditer(text):
        v = m.group(1)
        if v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    for m in _LABELLED_ID_RE.finditer(text):
        v = m.group(1)
        if v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
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
    """通用标识符识别 — metadata_stage 复杂度特征（len()）继续可用。"""
    return extract_chunk_keywords_cached(text or "", doc_type=doc_type, top_k=top_k)


def extract_doc_keywords(text: str, top_k: int = 10) -> KeywordResult:
    """doc 级普通关键词停发（P0-2）：恒空产出，字段兼容保留。"""
    return KeywordResult(rule_keywords=[], llm_keywords=[])


def extract_doc_keywords_typed(text: str, doc_type: str = "general",
                               top_k: int = 10, confidence: float = 0.0,
                               complexity: dict | None = None,
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
        key = w.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(w.strip())
        if limit is not None and len(out) >= limit:
            break
    return out


# 向后兼容别名（历史调用方）
extract_doc_keywords_rule = extract_rule_keywords
