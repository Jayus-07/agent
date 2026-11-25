"""router.py — 结构驱动切分策略路由（ChunkStrategyRouter）与内容单元局部分派。

策略选择主键 = AST 结构信号（StructureProfile + AST 实时判定），
按「问答对 > 强法律条款 > 表格 > 法律条款 > 步骤 > 章节 > 递归」优先级直判；
混合内容单元按嵌套章节局部选择策略（ContentUnitDispatchStrategy）。
"""
from __future__ import annotations

import copy
from typing import TYPE_CHECKING, List

from langchain_core.documents import Document

from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, walk
from backend.rag.preprocessing.chunkers.base import (
    _ast_has_sections, _enrich, _has_legal_clauses, _has_qa_nodes,
    _has_table_nodes, count_tokens,
)
from backend.rag.preprocessing.chunkers.stamper import _namespace_content_unit_chunks
from backend.rag.preprocessing.chunkers.strategies import (
    FinancialTableChunkStrategy, FixedSizeChunkStrategy, LegalChunkStrategy,
    QAChunkStrategy, RecursiveChunkStrategy, SemanticChunkStrategy,
    StepChunkStrategy, StructureChunkStrategy,
)
from backend.shared.logger import logger

if TYPE_CHECKING:
    from backend.rag.preprocessing.structure_analyzer import StructureReport


class ChunkRouteDecision:
    """Router 可审计决策记录（P1，2026-10-13）。

    回答「这份文档为什么用这个策略」：输入结构信号、业务类型、最终策略、
    策略版本、降级原因与处理范围。随 qc 进质量记录与 Trace，供管理端
    与评测归因。字段演进保持向后兼容（新增带默认值）。
    """

    def __init__(self, doc_type: str, completeness: float, strategy: str,
                 strategy_version: str, signals: dict,
                 degrade_reason: str = "", threshold: float = 0.0,
                 scope: str = ""):
        self.doc_type = doc_type
        self.completeness = completeness
        self.strategy = strategy
        self.strategy_version = strategy_version
        self.signals = signals
        self.degrade_reason = degrade_reason
        self.threshold = threshold
        self.scope = scope

    def as_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "completeness": self.completeness,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "signals": self.signals,
            "degrade_reason": self.degrade_reason,
            "threshold": self.threshold,
            "scope": self.scope,
        }


# 策略版本表（决策记录元数据，集中维护）：切分行为变更的类递增——
# v2 = 2026-10-13 批次1（根引导块/覆盖补全/严格 Token 上限/局部 QA 配对）。
_STRATEGY_VERSIONS = {
    "StructureChunkStrategy": "v3",
    "FixedSizeChunkStrategy": "v2",
    "RecursiveChunkStrategy": "v2",
    "StepChunkStrategy": "v2",
    "LegalChunkStrategy": "v4",
    "QAChunkStrategy": "v2",
    "FinancialTableChunkStrategy": "v2",
    "SemanticChunkStrategy": "v1",
    "ContentUnitDispatchStrategy": "v2",
}

# 强信号豁免阈值（P1 Structure Profile）：强结构信号不因整篇完整度
# 低于 STRUCTURE_COMPLETE_THRESHOLD 失效。信号计数的下限防误判——
# 普通文本中偶现的「第N条」/单条 Q/A 不足以支撑整篇按该结构切。
_SIGNAL_MIN_BY_FIELD = {
    "legal_clause_count": 3,   # ≥3 条「第N条」→ Legal 策略豁免完整度阈值
    "qa_pair_count": 1,        # qa 节点即强信号（parser 局部配对已保证配对可信）
    "step_heading_count": 3,   # ≥3 个「一、二、三、」标题 → Step 策略豁免
}

# doc_type → 豁免信号名（profile 字段）
_STRONG_SIGNAL_FIELD = {
    "legal": "legal_clause_count",
    "contract_template": "legal_clause_count",
    "faq": "qa_pair_count",
    "sop": "step_heading_count",
    "training": "step_heading_count",
}


def _recursive_content_units(ast: DocumentAST) -> list[tuple[DocumentAST, list[str]]]:
    """依 AST 的实际嵌套层级拆出局部路由单元，并保留祖先标题路径。"""
    result: list[tuple[DocumentAST, list[str]]] = []

    def make_unit(nodes: list[DocumentNode], prefix_path: list[str]) -> None:
        copied = copy.deepcopy(nodes)
        unit_root = DocumentNode(type="section", text="", level=0, children=copied)
        body_text = "\n".join(
            node.text for node in walk(unit_root)
            if node is not unit_root and node.text
        )
        result.append((DocumentAST(
            root=unit_root,
            source_file=ast.source_file,
            raw_text=body_text,
            document_title=ast.document_title,
            source_format=ast.source_format,
            parser_version=ast.parser_version,
        ), list(prefix_path)))

    def visit(parent: DocumentNode, ancestors: list[str]) -> None:
        pending: list[DocumentNode] = []
        for child in parent.children:
            if child.type == "section":
                if pending:
                    if parent.level > 0:
                        wrapper = copy.deepcopy(parent)
                        wrapper.children = pending
                        make_unit([wrapper], ancestors)
                    else:
                        make_unit(pending, ancestors)
                    pending = []
                next_ancestors = ancestors
                if parent.level > 0 and parent.text.strip():
                    next_ancestors = ancestors + [parent.text.strip()]
                visit(child, next_ancestors)
            else:
                pending.append(child)
        if pending:
            if parent.level > 0:
                wrapper = copy.deepcopy(parent)
                wrapper.children = pending
                make_unit([wrapper], ancestors)
            else:
                make_unit(pending, ancestors)

    visit(ast.root, [])
    return result


def _top_level_content_units(ast: DocumentAST) -> list[DocumentAST]:
    """兼容旧调用：返回递归路由单元而不附带祖先路径。"""
    return [unit for unit, _ in _recursive_content_units(ast)]


def _local_strategy_class(ast: DocumentAST, doc_type: str):
    """仅依据当前内容单元结构选择处理器，不继承文档级优先级。"""
    if _has_qa_nodes(ast):
        return QAChunkStrategy
    if _has_legal_clauses(ast):
        return LegalChunkStrategy
    if _has_table_nodes(ast):
        return (
            FinancialTableChunkStrategy
            if doc_type == "financial" else StructureChunkStrategy
        )
    try:
        from backend.rag.preprocessing.structure_analyzer import build_profile

        profile = build_profile(ast)
        if profile.step_heading_count >= 3:
            return StepChunkStrategy
    except Exception as exc:  # 路由计算失败时保守走结构切分
        logger.warning(
            f"[Router] 内容单元结构分析失败，回退 Structure: {type(exc).__name__}"
        )
    if _ast_has_sections(ast):
        return StructureChunkStrategy
    return RecursiveChunkStrategy


class ContentUnitDispatchStrategy:
    """混合文档按嵌套章节局部选择策略，再合并成统一 Leaf/Parent 结果。"""

    def __init__(self, doc_type: str):
        self.doc_type = doc_type
        self.last_plan: list[dict] = []

    def split(self, ast: DocumentAST, file_path: str) -> List[Document]:
        chunks: list[Document] = []
        self.last_plan = []
        for index, (unit_ast, ancestor_path) in enumerate(
            _recursive_content_units(ast), start=1,
        ):
            strategy_cls = _local_strategy_class(unit_ast, self.doc_type)
            strategy = (
                strategy_cls(self.doc_type)
                if strategy_cls is QAChunkStrategy
                else strategy_cls()
            )
            unit_chunks = strategy.split(unit_ast, file_path)
            section_titles = [
                child.text for child in unit_ast.root.children
                if child.type == "section" and child.text
            ]
            for chunk in unit_chunks:
                current_path = chunk.metadata.get("section_path") or []
                if not current_path and section_titles:
                    current_path = section_titles
                if ancestor_path or current_path:
                    chunk.metadata["section_path"] = (
                        list(ancestor_path) + list(current_path)
                    )
            unit_name = " / ".join(section_titles)[:120] or f"根内容单元 {index}"
            _namespace_content_unit_chunks(
                unit_chunks, file_path, f"{index}:{unit_name}",
            )
            self.last_plan.append({
                "unit": index,
                "section": unit_name,
                "ancestor_path": list(ancestor_path),
                "strategy": strategy_cls.__name__,
            })
            for chunk in unit_chunks:
                chunk.metadata["content_unit"] = unit_name
                chunk.metadata["content_unit_strategy"] = strategy_cls.__name__
            chunks.extend(unit_chunks)
        return _enrich(chunks, file_path)


class ChunkStrategyRouter:
    """结构驱动路由（P0-1 治理改造，2026-10-19，v3）。

    策略选择主键 = AST 结构信号（StructureProfile + AST 实时判定），按
    「问答对 > 强法律条款 > 表格 > 法律条款 > 步骤 > 章节 > 递归」优先级直判；doc_type
    不再是切分前置条件，仅保留两处已论证的业务最小例外（ad_policy 固定
    大小、financial 数值表格过滤），并继续作为审计字段进入
    ChunkRouteDecision。v2 的 completeness 门槛/强信号豁免/资格否决分支
    由直判天然吸收（无 qa 节点不进 QA、无「第 N 条」不进 Legal）。

    安全降级：结构不足 → 语义切分（开关默认关）→ 递归兜底，不静默丢
    内容（覆盖审计仍按产出 Leaf 对账）。
    """

    ROUTER_VERSION = "v3"

    def route(self, doc_type: str, report: StructureReport):
        return self.route_with_decision(doc_type, report)[0]

    def route_with_decision(self, doc_type: str, report: StructureReport):
        """结构信号直判（v3）：优先级 问答对>强法律条款>表格>条款>步骤>章节>递归。

        doc_type 仅用于 ad_policy/financial 两个业务最小例外与决策审计；
        route() 旧签名兼容不变。
        """
        from backend.config import (
            ENABLE_SEMANTIC_CHUNKING, SEMANTIC_CHUNK_MIN_TOKENS,
            STRUCTURE_COMPLETE_THRESHOLD,
        )
        profile = getattr(report, "profile", None)
        signals = profile.as_dict() if profile is not None else {}

        def _decision(cls, degrade_reason: str = "", scope: str = "",
                      signal: str = "", strategy_instance=None):
            strategy = strategy_instance or cls()
            strategy_class = strategy.__class__
            return strategy, ChunkRouteDecision(
                doc_type=doc_type,
                completeness=report.completeness,
                strategy=strategy_class.__name__,
                strategy_version=_STRATEGY_VERSIONS.get(strategy_class.__name__, "v1"),
                signals=signals,
                degrade_reason=degrade_reason or signal,
                threshold=STRUCTURE_COMPLETE_THRESHOLD,
                scope=scope,
            )

        # 业务最小例外：广告法按既定固定大小切分（既有行为，结构无关）
        if doc_type == "ad_policy":
            return _decision(FixedSizeChunkStrategy,
                             degrade_reason="business_exception_ad_policy",
                             scope="全篇固定大小")

        # 多个嵌套内容单元命中不同结构时，让每个章节按本地信号选择处理器，
        # 避免 FAQ 主信号覆盖同一文档其他章节的合同或表格结构。
        content_units = _top_level_content_units(report.ast)
        unit_routes = [
            (unit, _local_strategy_class(unit, doc_type))
            for unit in content_units
        ]
        unit_strategy_names = {strategy.__name__ for _unit, strategy in unit_routes}
        if len(unit_routes) > 1 and len(unit_strategy_names) > 1:
            signals = dict(signals)
            signals["content_unit_routes"] = [
                {
                    "unit": index,
                    "section": next((
                        child.text for child in unit.root.children
                        if child.type == "section" and child.text
                    ), "根内容单元"),
                    "strategy": strategy.__name__,
                }
                for index, (unit, strategy) in enumerate(unit_routes, start=1)
            ]
            logger.info(
                "[Router] 混合内容单元 → 局部策略分派: "
                + ", ".join(sorted(unit_strategy_names))
            )
            return _decision(
                ContentUnitDispatchStrategy,
                scope="按嵌套章节/内容单元局部分派",
                signal="mixed_content_units",
                strategy_instance=ContentUnitDispatchStrategy(doc_type),
            )

        # 1) 可靠问答对：qa 节点由解析器局部配对产出，存在即结构成立
        if _has_qa_nodes(report.ast):
            logger.info("[Router] 结构信号 qa_pair → QA 切分")
            return _decision(
                QAChunkStrategy,
                scope="按问答对",
                signal="signal_qa_pairs",
                strategy_instance=QAChunkStrategy(doc_type),
            )

        # 多条款合同内的表格属于条款内容，必须先按合同结构规划。
        if profile is not None and profile.legal_clause_count >= 3:
            logger.info("[Router] 强结构信号 legal_clauses → Legal 切分")
            return _decision(LegalChunkStrategy, scope="按条款及条款内表格",
                             signal="signal_legal_clauses")

        # 2) 表格：表头/行列关系与章节上下文由 Structure 表格行级保留；
        #    financial 数值列过滤为业务最小例外
        if _has_table_nodes(report.ast):
            if doc_type == "financial":
                return _decision(FinancialTableChunkStrategy,
                                 scope="表格行级+数值列过滤",
                                 signal="signal_table_financial")
            return _decision(StructureChunkStrategy,
                             degrade_reason="table_structure_fallback",
                             scope="表格行级切分",
                             signal="signal_table")

        # 3) 法律条款：「第 N 条」多条款结构才可靠
        if _has_legal_clauses(report.ast):
            logger.info("[Router] 结构信号 legal_clauses → Legal 切分")
            return _decision(LegalChunkStrategy, scope="按条款分组",
                             signal="signal_legal_clauses")

        # 4) 明确步骤/有序列表（中文序号标题 ≥3 才可靠）
        if profile is not None and profile.step_heading_count >= 3:
            logger.info("[Router] 结构信号 step_headings → Step 切分")
            return _decision(StepChunkStrategy, scope="按步骤",
                             signal="signal_step_headings")

        # 5) 章节结构：保留标题链与上下文
        if _ast_has_sections(report.ast):
            return _decision(StructureChunkStrategy, scope="按章节",
                             signal="signal_sections")

        # 6) 超长无结构：语义切分仅在开关开启时启用（默认关，保持现状）
        if ENABLE_SEMANTIC_CHUNKING and count_tokens(report.ast.raw_text) >= SEMANTIC_CHUNK_MIN_TOKENS:
            logger.info("[Router] 无结构长文档 → Semantic 语义切分")
            return _decision(SemanticChunkStrategy,
                             degrade_reason="structure_insufficient_semantic",
                             scope="全篇按语义边界")

        # 7) 结构不足 → 递归兜底（doc_type 不再兜底救援）
        logger.info(
            f"[Router] 无可靠结构信号（doc_type={doc_type} 不参与路由）"
            f"→ Recursive 兜底")
        return _decision(
            RecursiveChunkStrategy,
            degrade_reason="structure_insufficient",
            scope="全篇递归切分",
        )
