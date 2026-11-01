"""Legacy route 字段单生产写入口守卫。"""

import ast
from pathlib import Path


_PRODUCTION_FILES = (
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "router_node.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "routing" / "hierarchical.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "routing" / "prefilter_chain.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "routing" / "continuation.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "cs_prefilter.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "travel_prefilter.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "selection_funnel_prefilter.py",
    # 2026-11-01 收编重构：booking/commerce prefilter 与 booking_pending_resolver
    # 随域图壳删除，守卫清单同步收窄。
    Path(__file__).resolve().parents[3] / "orchestration" / "context" / "travel_pending_resolver.py",
)


def test_route_mode_literal_is_not_written_outside_projection():
    hits: list[str] = []
    for path in _PRODUCTION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "route_mode":
                    hits.append(f"{path.name}:{node.lineno}")

    assert not hits, "生产路由出口仍直接构造 route_mode：" + ", ".join(hits)
