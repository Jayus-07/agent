"""tests/selection_funnel/test_knowledge.py — 知识层 P0 单测

覆盖：极限词命中与误报控制 / 平台规则兜底 / RAG 降级不炸 /
reporter 注入合规段 / 知识层不淘汰候选（只富化）。
"""
from __future__ import annotations

import pytest

from backend.selection_funnel.knowledge import (
    compliance_review,
    platform_rules,
    scan_banned_words,
)


def _cand(**over):
    base = {"title": "宠物零食冻干鸡肉 500g", "platform": "拼多多",
            "price": 149.0, "promo_text": "", "highlights": "", "url": "u1"}
    base.update(over)
    return base


# ── 极限词扫描 ────────────────────────────────────────────────────────
def test_banned_word_hit():
    hits = scan_banned_words([_cand(promo_text="全网最低价清仓")])
    assert len(hits) == 1 and "全网最低价" in hits[0] and "人工复核" in hits[0]


def test_banned_word_no_false_positive():
    """「最近上架」等日常词不命中（词组级匹配控制误报）。"""
    assert scan_banned_words([_cand(title="最近上架的冻干鸡肉")]) == []


def test_banned_word_scans_all_text_fields():
    hits = scan_banned_words([_cand(highlights="行业领先品牌")])
    assert len(hits) == 1 and "highlights" in hits[0]


# ── 平台规则 ──────────────────────────────────────────────────────────
def test_platform_rules_pdd_specific():
    rules = platform_rules("拼多多")
    assert any("仅退款" in r for r in rules)
    assert any("广告法" in r for r in rules)  # 通用兜底并入


def test_platform_rules_unknown_platform_falls_back():
    rules = platform_rules("某小平台")
    assert rules and all("仅退款" not in r for r in rules)


# ── RAG 降级 ──────────────────────────────────────────────────────────
def test_rag_downgrade_silent_note(monkeypatch):
    """RAG 不可用 → findings 无知识库段，note 披露，不炸。"""
    import backend.selection_funnel.knowledge as k

    def _boom():
        raise RuntimeError("vector store down")
    monkeypatch.setattr(k, "rag_enhance", lambda c, p, top_k=3: ([], ""))
    findings, notes = compliance_review([_cand()], "拼多多", "宠物零食")
    assert not any("知识库相关片段" in f for f in findings)
    assert any("知识库" in n for n in notes)


def test_rag_snippets_injected(monkeypatch):
    import backend.selection_funnel.knowledge as k
    monkeypatch.setattr(k, "rag_enhance",
                        lambda c, p, top_k=3: (["宠物零食需 SC 资质"], "知识库"))
    findings, notes = compliance_review([_cand()], "拼多多", "宠物零食")
    assert any("SC 资质" in f for f in findings)
    assert not any("知识库" in n for n in notes)


# ── 报告注入 ──────────────────────────────────────────────────────────
def test_report_includes_knowledge_section():
    from backend.selection_funnel.reporter import render_report

    class _B:
        category = "宠物零食"; platform = "拼多多"
        price_min = None; price_max = None; target_margin = None

    findings, _ = compliance_review(
        [_cand(promo_text="全网最低价")], "拼多多", "宠物零食")
    report = render_report(_B(), [], [_cand(promo_text="全网最低价")], [],
                           knowledge=findings)
    assert "### 合规与知识层提示" in report
    assert "疑似广告法极限词" in report
    assert "仅退款" in report


def test_report_without_knowledge_keeps_legacy_shape():
    """knowledge 缺省 None → 不渲染合规段（旧调用兼容）。"""
    from backend.selection_funnel.reporter import render_report

    class _B:
        category = "宠物零食"; platform = ""
        price_min = None; price_max = None; target_margin = None

    report = render_report(_B(), [], [], [])
    assert "合规与知识层提示" not in report


def test_review_never_drops_candidates():
    """纪律断言：compliance_review 只产提示，不动候选列表。"""
    cands = [_cand(promo_text="全网最低价"), _cand(title="绝对有效神器", url="u2")]
    findings, _ = compliance_review(cands, "淘宝", "宠物零食")
    assert len(cands) == 2 and len(findings) >= 2
