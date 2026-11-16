"""RAG 评测用例题型的共享契约。"""

from typing import Literal, get_args

RagQueryType = Literal[
    "single_doc",
    "multi_doc",
    "single_hop",
    "single_hop_specific",
    "multi_hop",
    "multi_hop_abstract",
    "multi_hop_specific",
    "table",
    "procedural",
    "no_evidence",
    "permission_denied",
    "negative",
    "adversarial",
    "ambiguous",
    "version_conflict",
    "chunk_level",
    "long_doc",
    "comparative",
    "aggregation",
    "conditional_reasoning",
    "negation_exclusion",
    "implicit_condition",
]

RAG_QUERY_TYPES = frozenset(get_args(RagQueryType))
RAGAS_QUERY_TYPES = (
    "single_hop_specific",
    "multi_hop_abstract",
    "multi_hop_specific",
)
RAGAS_MULTI_HOP_QUERY_TYPES = frozenset({
    "multi_hop_abstract",
    "multi_hop_specific",
})
MULTI_HOP_QUERY_TYPES = frozenset({
    "multi_hop",
    "multi_hop_abstract",
    "multi_hop_specific",
})
