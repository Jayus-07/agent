"""
电商实体提取 — 品牌/平台/SKU编码/类目等实体识别

历史说明:
  - 旧版仅支持人名提取（KNOWN_PERSON_NAMES 白名单），现已扩展为：
    1. 品牌/平台名（config.KNOWN_PERSON_NAMES 白名单匹配）
    2. SKU 编码（正则模式匹配：字母+数字）
    3. 平台渠道名（config.SIGNAL_RULES["平台渠道"] 信号词匹配）
  - extract_person_names() 保留函数名不变，避免改动所有调用方
"""

import re
from typing import List

from config import KNOWN_PERSON_NAMES
from utils.logger import logger

# SKU 编码正则: 大写字母+数字+变体后缀 (如 MK202-RED-L, ZN105-BLK-M)
_SKU_PATTERN = re.compile(r'\b([A-Z]{2,4}\d{2,4}(?:-[A-Z0-9]+)*)\b')


def extract_person_names(text: str) -> List[str]:
    """电商实体提取: 品牌名 + 平台名（复用原有函数名以保持向后兼容）。

    Args:
        text: 用户查询文本

    Returns:
        匹配到的实体名列表（品牌/平台/渠道）
    """
    names = set()
    # 1. 白名单匹配（品牌 + 平台）
    for known_name in KNOWN_PERSON_NAMES:
        if known_name in text:
            names.add(known_name)
    return list(names)


def extract_sku_codes(text: str) -> List[str]:
    """从文本中提取 SKU 编码（正则匹配）。"""
    matches = _SKU_PATTERN.findall(text)
    return list(set(matches))


def extract_platforms(text: str) -> List[str]:
    """从文本中提取平台/渠道名。"""
    platforms = set()
    text_lower = text.lower()
    platform_keywords = [
        "amazon", "shopify", "tiktok", "ebay", "walmart",
    ]
    for p in platform_keywords:
        if p in text_lower:
            platforms.add(p.title() if p != "ebay" else "eBay")
    return list(platforms)


def extract_all_entities(text: str) -> dict:
    """提取所有电商实体（供 QueryAnalyzer 等使用）。

    Returns:
        {
            "entities": [...],    # 品牌/平台名
            "sku_codes": [...],   # SKU 编码
            "platforms": [...],   # 平台名
        }
    """
    return {
        "entities": extract_person_names(text),
        "sku_codes": extract_sku_codes(text),
        "platforms": extract_platforms(text),
    }
