"""test_structure_profile_router.py — P1 Structure Profile 与路由决策记录（2026-10-13）。

覆盖：
- StructureProfile 信号统计（章节/表格/QA/条款/SOP 标题）
- 强信号豁免：legal/faq/sop 完整度低于阈值时按强信号路由，不再整篇递归硬切
- ChunkRouteDecision 可审计字段完整性（signals/策略版本/降级原因/范围）
- route() 旧签名兼容（现有调用方零改动）
"""
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode
from backend.rag.preprocessing.chunking import (
    ChunkStrategyRouter,
    LegalChunkStrategy,
    QAChunkStrategy,
    RecursiveChunkStrategy,
    StepChunkStrategy,
    StructureChunkStrategy,
)
from backend.rag.preprocessing.structure_analyzer import (
    StructureAnalyzer,
    build_profile,
)


def _report(completeness: float, ast: DocumentAST | None = None):
    ast = ast or DocumentAST(
        root=DocumentNode(type="section", text="", level=0))
    _, report = StructureAnalyzer().analyze(ast)
    # 测试直接控制完整度（信号与完整度可独立组合）
    report.completeness = completeness
    return report


def test_profile_counts_signals():
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="一、准备", level=1, children=[
                DocumentNode(type="paragraph", text="准备内容"),
                DocumentNode(type="table", text="t", rows=[["h"], ["v"]]),
            ]),
            DocumentNode(type="section", text="二、执行", level=1, children=[
                DocumentNode(type="paragraph", text="第一条 合同标的"),
                DocumentNode(type="paragraph", text="第二条 价款"),
                DocumentNode(type="paragraph", text="第三条 违约"),
            ]),
        ]),
        raw_text="x",
    )
    p = build_profile(ast)
    assert p.has_sections and p.section_count == 2
    assert p.table_count == 1
    assert p.step_heading_count == 2
    assert p.legal_clause_count == 3
    assert p.plain_leaf_count == 4


def test_legal_strong_signal_overrides_low_completeness():
    """合同强信号（≥3 条款）+ 完整度 0.4 → Legal 策略豁免（不再整篇递归）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text=f"第{i}条 条款内容{i}")
            for i in range(1, 6)
        ]),
        raw_text="第1条 条款内容1 第2条 条款内容2 ...",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.4   # 低于 0.7 阈值
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "legal", report)
    assert isinstance(strategy, LegalChunkStrategy)
    assert decision.degrade_reason == "signal_legal_clauses"
    assert decision.signals.get("legal_clause_count", 0) >= 3
    assert decision.signals["legal_clause_count"] >= 3
    assert decision.strategy_version


def test_faq_strong_signal_overrides_low_completeness():
    """faq 完整度不足但有 qa 节点 → QAChunkStrategy（QA 信号本就设 1.0 的双保险）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="Q1？"),
            DocumentNode(type="qa_answer", text="A1。"),
        ]),
        raw_text="Q1？A1。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.3
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "faq", report)
    assert isinstance(strategy, QAChunkStrategy)
    assert decision.strategy == "QAChunkStrategy"


def test_sop_strong_signal_overrides_low_completeness():
    """sop 完整度不足但 ≥3 个「一、」标题 → StepChunkStrategy 豁免。"""
    children = []
    for i, cn in enumerate(["一", "二", "三"]):
        children.append(DocumentNode(
            type="section", text=f"{cn}、步骤{i+1}", level=1,
            children=[DocumentNode(type="paragraph", text=f"内容{i}")]))
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=children),
        raw_text="一、步骤1 二、步骤2 三、步骤3",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.5
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "sop", report)
    assert isinstance(strategy, StepChunkStrategy)
    assert decision.degrade_reason == "signal_step_headings"


def test_weak_signal_still_falls_back_to_recursive():
    """完整度不足且无强信号（或信号不足）→ 仍 Recursive 兜底。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="普通段落，没有结构。"),
        ]),
        raw_text="普通段落，没有结构。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.3
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "policy", report)
    assert isinstance(strategy, RecursiveChunkStrategy)
    assert decision.degrade_reason


def test_decision_record_complete_for_complete_doc():
    """完整结构 + policy：决策记录含信号/版本/无降级。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="第一章", level=1, children=[
            DocumentNode(type="paragraph", text="正文"),
        ]),
        raw_text="第一章 正文",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.9
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "policy", report)
    assert isinstance(strategy, StructureChunkStrategy)
    d = decision.as_dict()
    assert d["strategy"] == "StructureChunkStrategy"
    assert d["strategy_version"] == "v2"  # StructureChunkStrategy 策略版本，非路由版本
    assert d["degrade_reason"] == "signal_sections"
    assert d["degrade_reason"] == "signal_sections"  # v3：直判信号即决策原因
    assert "section_count" in d["signals"]
    assert d["threshold"] > 0


def test_route_signature_backwards_compatible():
    """route() 旧签名只返回策略实例（现有调用方/测试零改动）。
    v3 行为变化：无任何结构的文档不再由 doc_type 映射救援，走递归。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="section", text="第一章", level=1,
                         children=[DocumentNode(type="paragraph", text="正文")]),
        ]),
        raw_text="第一章 正文",
    )
    _, report = StructureAnalyzer().analyze(ast)
    assert isinstance(ChunkStrategyRouter().route("policy", report),
                      StructureChunkStrategy)
    empty = _report(0.9)
    assert isinstance(ChunkStrategyRouter().route("policy", empty),
                      RecursiveChunkStrategy)


# ── v3 结构驱动直判（P0-1 治理改造，2026-10-19）─────────────────────

def test_v3_structure_signal_ignores_doc_type():
    """信号直判不再受 doc_type 误分类影响：被误分类为 policy 的 QA 文档
    仍走 QA（v2 时 strong_signal 豁免要求 doc_type 命中豁免表）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="qa_question", text="Q1？"),
            DocumentNode(type="qa_answer", text="A1。"),
        ]),
        raw_text="Q1？A1。",
    )
    _, report = StructureAnalyzer().analyze(ast)
    report.completeness = 0.3
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "policy", report)  # 故意错误分类
    assert isinstance(strategy, QAChunkStrategy)
    assert decision.doc_type == "policy"  # 审计字段保留原分类


def test_v3_step_signal_ignores_doc_type():
    """被误分类为 faq 的 SOP 文档（≥3 步骤标题）→ Step 而非 QA/递归。"""
    children = []
    for i, cn in enumerate(["一", "二", "三"]):
        children.append(DocumentNode(
            type="section", text=f"{cn}、步骤{i+1}", level=1,
            children=[DocumentNode(type="paragraph", text=f"内容{i}")]))
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=children),
        raw_text="一、步骤1 二、步骤2 三、步骤3",
    )
    _, report = StructureAnalyzer().analyze(ast)
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "faq", report)
    assert isinstance(strategy, StepChunkStrategy)
    assert decision.degrade_reason == "signal_step_headings"


def test_v3_legal_without_clauses_no_longer_enters_legal():
    """doc_type=legal 但无条款结构 → 不再进 Legal（按 sections/递归直判）。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="paragraph", text="普通合同文本，无条款编号。"),
        ]),
        raw_text="普通合同文本",
    )
    _, report = StructureAnalyzer().analyze(ast)
    strategy, decision = ChunkStrategyRouter().route_with_decision(
        "legal", report)
    assert not isinstance(strategy, LegalChunkStrategy)
    assert decision.degrade_reason in ("signal_qa_pairs", "signal_table",
                                       "signal_sections",
                                       "structure_insufficient")


def test_v3_financial_table_business_exception_preserved():
    """financial+表格 保留数值列过滤例外；非 financial+表格 → 通用表格行级。"""
    ast = DocumentAST(
        root=DocumentNode(type="section", text="", level=0, children=[
            DocumentNode(type="table", text="t", rows=[["科目", "金额"], ["a", "1"]]),
        ]),
        raw_text="t",
    )
    _, report = StructureAnalyzer().analyze(ast)
    from backend.rag.preprocessing.chunking import FinancialTableChunkStrategy
    s1, d1 = ChunkStrategyRouter().route_with_decision("financial", report)
    assert isinstance(s1, FinancialTableChunkStrategy)
    assert d1.degrade_reason == "signal_table_financial"
    s2, d2 = ChunkStrategyRouter().route_with_decision("policy", report)
    assert isinstance(s2, StructureChunkStrategy)
    assert d2.degrade_reason == "table_structure_fallback"  # 显式降级原因优先于 signal
