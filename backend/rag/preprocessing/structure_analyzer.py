"""StructureAnalyzer — Raw AST → Normalized AST + StructureReport。

规则优先：归一化 AST + 计算结构完整度 + 判定结构不足信号。
结构混乱时的 LLM 补充是 Phase 2，本文件不实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.config import STRUCTURE_COMPLETE_THRESHOLD, PARENT_CHUNK_TOKENS
from backend.rag.preprocessing.ast import DocumentAST, DocumentNode, LEAF_TYPES, walk
from backend.rag.preprocessing.token_counter import count_tokens


@dataclass
class StructureReport:
    ast: DocumentAST
    completeness: float
    deficit_signal: str = ""
    topic_shift_detected: bool = False        # Phase 2 接入
    is_high_value_and_chaotic: bool = False   # Phase 2 接入

    @property
    def is_complete(self) -> bool:
        return self.completeness >= STRUCTURE_COMPLETE_THRESHOLD


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
        # Phase 1 归一化为最小实现：原样透传（结构已由 Parser 建好）
        normalized = raw_ast
        completeness = self._compute_completeness(raw_ast)
        deficit = self._detect_deficit(raw_ast, completeness)
        report = StructureReport(
            ast=normalized,
            completeness=completeness,
            deficit_signal=deficit,
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
