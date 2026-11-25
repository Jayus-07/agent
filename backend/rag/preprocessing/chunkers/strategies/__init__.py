"""strategies — 切分策略族与业务文档类型分发映射表。"""
from __future__ import annotations

from backend.rag.preprocessing.chunkers.strategies.financial import (
    FinancialTableChunkStrategy,
)
from backend.rag.preprocessing.chunkers.strategies.legal import LegalChunkStrategy
from backend.rag.preprocessing.chunkers.strategies.qa import QAChunkStrategy
from backend.rag.preprocessing.chunkers.strategies.recursive import (
    FixedSizeChunkStrategy,
    RecursiveChunkStrategy,
)
from backend.rag.preprocessing.chunkers.strategies.semantic import (
    SemanticChunkStrategy,
)
from backend.rag.preprocessing.chunkers.strategies.step import StepChunkStrategy
from backend.rag.preprocessing.chunkers.strategies.structure import (
    StructureChunkStrategy,
    _split_unstructured,
)

STRUCTURE_STRATEGIES = {
    "policy": StructureChunkStrategy,
    "compliance": StructureChunkStrategy,
    "security": StructureChunkStrategy,
    "financial": FinancialTableChunkStrategy,
    "customer_data": StructureChunkStrategy,
    "product_spec": StructureChunkStrategy,
    "listing": StructureChunkStrategy,
    "sop": StepChunkStrategy,
    "training": StepChunkStrategy,
    "legal": LegalChunkStrategy,
    "contract_template": LegalChunkStrategy,
    "faq": QAChunkStrategy,
    "ad_policy": FixedSizeChunkStrategy,
    # M3：旅游攻略——POI 小节「一店一 chunk」，标题感知切分（文档规范
    # docs/travel-rag-doc-spec.md 的 ### 每 POI 一节 ≤500 token 与此配合）
    "travel_guide": StructureChunkStrategy,
}

__all__ = [
    "FixedSizeChunkStrategy",
    "RecursiveChunkStrategy",
    "StructureChunkStrategy",
    "StepChunkStrategy",
    "LegalChunkStrategy",
    "QAChunkStrategy",
    "SemanticChunkStrategy",
    "FinancialTableChunkStrategy",
    "STRUCTURE_STRATEGIES",
    "_split_unstructured",
]
