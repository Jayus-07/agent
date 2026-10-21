"""StructureAnalyzer — Raw AST → Normalized AST + StructureReport。

规则优先：归一化 AST + 计算结构完整度 + 判定结构不足信号。
结构混乱时的 LLM 补充是 Phase 2，本文件不实现。

P1 Structure Profile（2026-10-13）：StructureReport 携带结构信号集合
（章节/表格/QA 对/法律条款/SOP 标题/纯段落量），供 ChunkStrategyRouter
做「强信号豁免阈值」与可审计决策记录——文件类型决定 Parser，业务类型
决定分类属性，AST 具体结构决定 Chunk 边界。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from backend.config import STRUCTURE_COMPLETE_THRESHOLD, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens

_STEP_HEADING_RE = re.compile(r"^[一二三四五六七八九十]+、")
_LEGAL_CLAUSE_RE = re.compile(r"^第[零〇两一二三四五六七八九十百千万\d]+条")
_LEGAL_CLAUSE_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?P<marker>第[零〇两一二三四五六七八九十百千万\d]+条)"
)


@dataclass
class StructureProfile:
    """AST 结构信号集合（Router 决策与豁免判定的事实源）。"""
    has_sections: bool = False
    section_count: int = 0
    table_count: int = 0
    qa_pair_count: int = 0
    legal_clause_count: int = 0
    step_heading_count: int = 0
    plain_leaf_count: int = 0

    def as_dict(self) -> dict:
        return {
            "has_sections": self.has_sections,
            "section_count": self.section_count,
            "table_count": self.table_count,
            "qa_pair_count": self.qa_pair_count,
            "legal_clause_count": self.legal_clause_count,
            "step_heading_count": self.step_heading_count,
            "plain_leaf_count": self.plain_leaf_count,
        }


@dataclass
class StructureReport:
    ast: DocumentAST
    completeness: float
    deficit_signal: str = ""
    topic_shift_detected: bool = False        # Phase 2 接入
    is_high_value_and_chaotic: bool = False   # Phase 2 接入
    profile: StructureProfile = field(default_factory=StructureProfile)

    @property
    def is_complete(self) -> bool:
        return self.completeness >= STRUCTURE_COMPLETE_THRESHOLD


def build_profile(ast: DocumentAST) -> StructureProfile:
    """一次遍历统计 AST 结构信号（P1 Structure Profile）。"""
    profile = StructureProfile()
    qa_seen = 0
    for n in walk(ast.root):
        if n.type == "section":
            if n.level > 0:
                profile.has_sections = True
                profile.section_count += 1
                if _STEP_HEADING_RE.match((n.text or "").strip()):
                    profile.step_heading_count += 1
                if _LEGAL_CLAUSE_RE.match((n.text or "").strip()):
                    profile.legal_clause_count += 1
        elif n.type == "table":
            profile.table_count += 1
        elif n.type == "qa_question":
            qa_seen += 1
        elif n.type in LEAF_TYPES:
            profile.plain_leaf_count += 1
            if _LEGAL_CLAUSE_RE.match((n.text or "").strip()):
                profile.legal_clause_count += 1
    profile.qa_pair_count = qa_seen
    return profile


def _normalize_inline_legal_clauses(ast: DocumentAST) -> DocumentAST:
    """把同一解析叶子中的多条制度条款归一化为独立 section 节点。

    PDF 版面提取经常把连续的多条款合在一个 paragraph 中。覆盖率审计只能
    证明正文没有丢，不能证明条款边界正确；这里按行首条款编号把合并叶子拆成
    独立叶子，再交给既有 LegalChunkStrategy 切分。原始 AST 保持不变，清洗
    审计和来源证据仍基于解析器原始节点。
    """
    marker_count = sum(
        (
            len(_LEGAL_CLAUSE_LINE_RE.findall(node.text or ""))
            if node.type in {"paragraph", "list"}
            else int(bool(_LEGAL_CLAUSE_RE.match((node.text or "").strip())))
        )
        for node in walk(ast.root)
        if node.type in {"paragraph", "list", "section"}
    )
    if marker_count < 2:
        return ast

    normalized = copy.deepcopy(ast)

    def _visit(parent: DocumentNode) -> None:
        normalized_children: list[DocumentNode] = []
        for child in parent.children:
            if child.type == "section":
                _visit(child)
                normalized_children.append(child)
                continue
            if child.type not in {"paragraph", "list"}:
                normalized_children.append(child)
                continue

            text = child.text or ""
            matches = list(_LEGAL_CLAUSE_LINE_RE.finditer(text))
            if not matches:
                normalized_children.append(child)
                continue

            prefix = text[:matches[0].start()].strip()
            if prefix:
                prefix_node = copy.deepcopy(child)
                prefix_node.text = prefix
                prefix_node.raw_text = ""
                prefix_node.children = []
                normalized_children.append(prefix_node)

            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                clause_text = text[match.start():end].strip()
                if clause_text:
                    clause = copy.deepcopy(child)
                    clause.text = clause_text
                    clause.raw_text = ""
                    clause.children = []
                    normalized_children.append(clause)
        parent.children = normalized_children

    _visit(normalized.root)
    return normalized


def _find_parent_section(root: DocumentNode, target: DocumentNode) -> DocumentNode | None:
    """在 AST 中查找 target 的直接父 section 节点。"""
    stack: list[tuple[DocumentNode, DocumentNode | None]] = [(root, None)]
    while stack:
        node, parent = stack.pop()
        if node is target:
            return parent
        for child in node.children:
            if child.type == "section":
                stack.append((child, node))
            else:
                stack.append((child, parent))
    return None


class StructureAnalyzer:
    def analyze(self, raw_ast: DocumentAST) -> tuple[DocumentAST, StructureReport]:
        normalized = _normalize_inline_legal_clauses(raw_ast)
        completeness = self._compute_completeness(normalized)
        deficit = self._detect_deficit(normalized, completeness)
        report = StructureReport(
            ast=normalized,
            completeness=completeness,
            deficit_signal=deficit,
            profile=build_profile(normalized),
        )
        return normalized, report

    def _compute_completeness(self, ast: DocumentAST) -> float:
        total = len(ast.raw_text.strip())
        if total == 0:
            return 0.0
        leaves = [n for n in walk(ast.root) if n.type in LEAF_TYPES]
        if not leaves:
            return 0.0
        # Q/A 文档：qa_question/qa_answer 本身就是结构信号（无需 section 层级），
        # 视为完整结构，让 Router 命中 STRUCTURE_STRATEGIES["faq"] = QAChunkStrategy
        if any(n.type in ("qa_question", "qa_answer") for n in leaves):
            return 1.0
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return 0.1   # 无任何章节结构 → 结构性极低，交由递归兜底
        covered = sum(len(n.text) for n in leaves) + sum(len(n.text) for n in sections)
        coverage = covered / total
        oversized = sum(1 for n in leaves if count_tokens(n.text) > PARENT_CHUNK_TOKENS)
        size_fitness = 1.0 - oversized / len(leaves)
        hq = self._hierarchy_quality(ast, sections)
        return round(0.4 * min(coverage, 1.0) + 0.25 * size_fitness + 0.35 * hq, 4)

    @staticmethod
    def _hierarchy_quality(ast: DocumentAST, sections: list[DocumentNode]) -> float:
        """分级评估层级结构质量（替代原二值 has_hierarchy）。

        三维加权：
          - multi_section (0.25): 节数量是否足够（≥3 满分）
          - level_consistency (0.35): 子节层级是否遵循 parent+1
          - containment (0.40): 叶子内容是否被层级结构包含（非平铺于根）
        """
        if not sections:
            return 0.0

        # 1) multi_section: ≥3 sections → 1.0
        multi_section = min(len(sections) / 3.0, 1.0)

        # 2) level_consistency: 对每个非根节，检查其直接父 section 的 level
        level_correct = 0
        level_total = 0
        for sec in sections:
            if sec.level <= 1:
                continue
            parent_section = _find_parent_section(ast.root, sec)
            if parent_section is not None:
                level_total += 1
                if sec.level == parent_section.level + 1:
                    level_correct += 1
        level_consistency = (level_correct / level_total) if level_total > 0 else 1.0

        # 3) containment: 1 - orphan_root_chars / total_leaf_chars
        root_direct_leaves_chars = sum(
            len(n.text) for n in ast.root.children
            if n.type in LEAF_TYPES
        )
        total_leaf_chars = sum(
            len(n.text) for n in walk(ast.root) if n.type in LEAF_TYPES
        )
        containment = 1.0 - (root_direct_leaves_chars / total_leaf_chars) if total_leaf_chars > 0 else 0.0

        return round(0.25 * multi_section + 0.35 * level_consistency + 0.40 * containment, 4)

    def _detect_deficit(self, ast: DocumentAST, completeness: float) -> str:
        if completeness >= STRUCTURE_COMPLETE_THRESHOLD:
            return ""
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return "no_heading"
        hq = self._hierarchy_quality(ast, sections)
        if hq < 0.4:
            return "weak_hierarchy"
        return "long_narrative"
