"""TD-15 实体覆盖校验误报回归（2026-10-03）。

实测：财务用户问"公司员工日常办公守则里，工作时间是几点到几点？"，
召回正确命中通用守则文档，但 jieba 将"公司员工"切为整体词典词绕过
停用词过滤 → 强实体"公司员工"在证据（"员工日常办公守则"）中无连写
→ 实体覆盖校验误报拒答（missing=['公司员工']），挡住正确召回。

钉死：泛指停用词前缀剥离（公司员工→员工）修复误报；真实 hard
negative 实体（无停用词前缀）行为不变，门禁强度不降。
"""
import pytest

from backend.rag.evidence_gate.operations import (
    _strip_stopword_prefix,
    find_missing_entities,
)


class _Doc:
    def __init__(self, content):
        self.page_content = content


def test_stopword_prefix_stripped():
    assert _strip_stopword_prefix("公司员工") == "员工"
    assert _strip_stopword_prefix("贵公司员工手册") == "员工手册"


def test_non_stopword_prefix_untouched():
    """真实 hard negative 实体无停用词前缀 → 原样保留（强度不变）。"""
    assert _strip_stopword_prefix("出口退税") == "出口退税"
    assert _strip_stopword_prefix("增值税专用发票") == "增值税专用发票"
    assert _strip_stopword_prefix("预付款") == "预付款"


def test_strip_to_empty_skips_entity():
    """剥离后为空（整体是泛指）→ 跳过该实体，不产生 missing。"""
    assert _strip_stopword_prefix("公司问题") in ("", "问题") or True
    # "问题"本身在停用表 → 剥离结果不合法 → 返回空
    assert _strip_stopword_prefix("公司问题") == ""


def test_realworld_misreport_fixed():
    """复现实测误报：守则文档在召回 top3 → 不应再因'公司员工'拒答。"""
    docs = [
        _Doc("员工日常办公守则（行政通用版）一、作息时间 工作时间：9:00-18:00，午休 12:00-13:30。"),
        _Doc("差旅费报销管理办法 出差期间餐费补贴实行包干制。"),
    ]
    missing = find_missing_entities("公司员工日常办公守则里，工作时间是几点到几点？", docs)
    assert "公司员工" not in missing
    # 文档确实覆盖了问题的全部核心概念（员工/守则/办公/工作时间）→ 零缺失
    assert missing == []


def test_hard_negative_still_rejected():
    """门禁强度保持：主题相近但核心实体不在 → missing 非空 → 拒答路径触发。

    实体切分粒度随 jieba 词典而变（出口退税→出口/退税），断言钉行为
    （有缺失实体）而非具体词面。
    """
    docs = [
        _Doc("笔记本电脑售后政策：7 天无理由退货，15 天换新。"),
    ]
    missing = find_missing_entities("出口退税的办理流程和预付款要求是什么？", docs)
    assert missing, "hard negative 必须保持实体缺失判定（拒答路径）"
    assert "预付款" in missing
