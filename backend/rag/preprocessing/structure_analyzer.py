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
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return 0.1   # 无任何章节结构 → 结构性极低，交由递归兜底
        covered = sum(len(n.text) for n in leaves) + sum(len(n.text) for n in sections)
        coverage = covered / total
        oversized = sum(1 for n in leaves if count_tokens(n.text) > PARENT_CHUNK_TOKENS)
        size_fitness = 1.0 - oversized / len(leaves)
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        has_hierarchy = 1.0 if len(sections) >= 2 else 0.0
        return round(0.5 * min(coverage, 1.0) + 0.3 * size_fitness + 0.2 * has_hierarchy, 4)

    def _detect_deficit(self, ast: DocumentAST, completeness: float) -> str:
        if completeness >= STRUCTURE_COMPLETE_THRESHOLD:
            return ""
        sections = [n for n in walk(ast.root) if n.type == "section" and n.level > 0]
        if not sections:
            return "no_heading"
        return "long_narrative"
