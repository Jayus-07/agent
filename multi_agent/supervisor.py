"""supervisor — 向后兼容 re-export（新代码请用 multi_agent.supervisor.*）"""
from multi_agent.supervisor.scheduler import supervisor_node, route_after_supervisor

__all__ = ["supervisor_node", "route_after_supervisor"]
