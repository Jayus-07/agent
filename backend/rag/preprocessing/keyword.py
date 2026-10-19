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
