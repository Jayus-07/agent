"""中文数字 → 阿拉伯数字 —— RAG 侧共用的小映射。

存在理由：报告期解析在两处都要把「一季度 / 二季度 / …」里的中文数字换成阿拉伯
数字——入库侧的 `preprocessing.financial_normalizer.extract_reporting_period`
与查询侧的 `retrieval.query_analyzer._extract_reporting_period_from_query`。
两边历史上各写了一份**逐字相同**的映射，属同一份事实的第二种表达。
现在只此一份，两侧共同导入。
"""
from __future__ import annotations

# 季度用中文数字 → 阿拉伯数字（一~四）。只覆盖季度所需的最小集合，
# 不做通用中文数字解析（「十」「百」等不在此范畴，避免被误用）。
CN_QUARTER_DIGITS: dict[str, str] = {"一": "1", "二": "2", "三": "3", "四": "4"}
