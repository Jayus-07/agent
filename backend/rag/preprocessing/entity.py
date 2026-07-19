"""
电商实体提取 — 品牌/平台/SKU/法规/人名 NER + 规则

P1 升级：从纯正则白名单升级为 jieba NER + 规则 + 白名单 三层提取。
兼容 extract_person_names() 旧接口。
"""
import re
from typing import List

from backend.config import KNOWN_PERSON_NAMES
from backend.shared.logger import logger

_SKU_PATTERN = re.compile(r'\b([A-Z]{2,4}\d{2,4}(?:-[A-Z0-9]+)*)\b')
_REGULATION_PATTERN = re.compile(r'(GDPR|CCPA|HIPAA|PCI[-\s]?DSS|SOX)\b', re.IGNORECASE)


def extract_entities(text: str) -> dict:
    """文档级实体提取 — NER + 规则 + 白名单。

    Returns: {
        "person": ["张三", ...],
        "organization": ["Amazon", ...],
        "regulation": ["GDPR", "第21条", ...],
        "product": ["SKU-123", ...],
        "platform": ["TikTok Shop", ...],
    }
    """
    result: dict[str, set[str]] = {
        "person": set(),
        "organization": set(),
        "regulation": set(),
        "product": set(),
        "platform": set(),
    }

    # 1. jieba NER（词性标注）
    try:
        import jieba.posseg as pseg
        for word, flag in pseg.cut(text[:8000]):
            if flag == "nr" and len(word) > 1:
                result["person"].add(word)
            elif flag in ("nt", "nz") and len(word) > 1:
                result["organization"].add(word)
    except Exception as e:
        logger.debug(f"[Entity] jieba NER 异常: {e}")

    # 2. 白名单（品牌/平台/渠道）
    text_lower = text.lower()
    for name in KNOWN_PERSON_NAMES:
        if name.lower() in text_lower:
            if any(p in name.lower() for p in ("amazon", "shopify", "tiktok", "ebay", "walmart", "shopee", "lazada")):
                result["platform"].add(name)
            elif len(name) <= 4:
                result["organization"].add(name)
            else:
                result["organization"].add(name)

    # 3. SKU 编码
    skus = _SKU_PATTERN.findall(text)
    result["product"].update(skus[:10])

    # 4. 法规编号
    regulations = _REGULATION_PATTERN.findall(text)
    result["regulation"].update(regulations)
    # 中文法规引用
    cn_regs = re.findall(r'(第[一二三四五六七八九十\d]+条)', text)
    result["regulation"].update(cn_regs[:5])

    # 去空、截断
    return {k: sorted([v for v in vals if v and len(v) > 1])[:10] for k, vals in result.items() if vals}


def extract_person_names(text: str) -> List[str]:
    """向后兼容：返回所有实体名称的合并列表。"""
    entities = extract_entities(text)
    return entities.get("person", []) + entities.get("organization", []) + entities.get("platform", [])


def extract_sku_codes(text: str) -> List[str]:
    """从文本中提取 SKU 编码（正则匹配）。"""
    return list(set(_SKU_PATTERN.findall(text)))


def extract_platforms(text: str) -> List[str]:
    """从文本中提取平台/渠道名。"""
    entities = extract_entities(text)
    return entities.get("platform", [])


def extract_all_entities(text: str) -> dict:
    """提取所有电商实体（供 QueryAnalyzer 等使用）。"""
    entities = extract_entities(text)
    return {
        "entities": entities.get("person", []) + entities.get("organization", []),
        "sku_codes": entities.get("product", []),
        "platforms": entities.get("platform", []),
    }
