"""Runtime 架构兼容基线的只读快照。"""

from backend.orchestration.graph.builder import _NODE_LABELS
from backend.orchestration.graph.event_schema import KNOWN_EVENTS
from backend.orchestration.state import KNOWN_STATE_KEYS


CORE_NODE_IDS = frozenset({
    "router",
    "tool_selector",
    "skill_executor",
    "workflow_executor",
    "planner",
    "critique",
    "supervisor",
    "reporter",
    "general_chat",
})

# 这些键在 STOP A 之前已存在，后续只能追加兼容字段，不能删除。
LEGACY_STATE_KEYS = frozenset(KNOWN_STATE_KEYS)
SSE_EVENT_NAMES = frozenset(KNOWN_EVENTS)


def compatibility_snapshot() -> dict[str, frozenset[str]]:
    """返回当前运行时兼容快照，供机械验收门比较。"""

    return {
        "core_node_ids": frozenset(CORE_NODE_IDS),
        "legacy_state_keys": frozenset(LEGACY_STATE_KEYS),
        "sse_event_names": frozenset(SSE_EVENT_NAMES),
        "node_labels": frozenset(_NODE_LABELS),
    }


def assert_legacy_baseline(snapshot: dict[str, frozenset[str]] | None = None) -> None:
    """确认兼容基线仍存在；新增字段/节点不构成破坏。"""

    current = snapshot or compatibility_snapshot()
    assert CORE_NODE_IDS <= current["core_node_ids"]
    assert LEGACY_STATE_KEYS <= current["legacy_state_keys"]
    assert SSE_EVENT_NAMES <= current["sse_event_names"]
