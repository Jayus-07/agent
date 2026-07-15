"""graph — 向后兼容 re-export（新代码请用 multi_agent.graph.*）"""
from backend.agent.graph.builder import build_graph, _NODE_LABELS, _parse_event
from backend.agent.graph.system import MultiAgentSystem

__all__ = ["build_graph", "MultiAgentSystem", "_NODE_LABELS", "_parse_event"]
