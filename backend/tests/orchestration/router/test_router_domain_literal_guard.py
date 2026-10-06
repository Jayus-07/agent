"""Router 可注册域不得再维护第二份硬编码映射表。"""

from __future__ import annotations

import ast
from pathlib import Path


_PRODUCTION_FILES = (
    Path(__file__).resolve().parents[3] / "orchestration" / "router" / "projection.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "router" / "domain_router.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "router" / "execution_mode.py",
    Path(__file__).resolve().parents[3] / "orchestration" / "graph" / "routing" / "prefilter_chain.py",
)

_REGISTERED_DOMAIN_KEYS = {
    "customer_service",
    "travel",
    "travel_booking",
    "travel_commerce",
    "selection_funnel",
}


def test_router_has_no_second_registered_domain_mapping_literal():
    hits: list[str] = []
    for path in _PRODUCTION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value in _REGISTERED_DOMAIN_KEYS:
                    hits.append(f"{path.name}:{node.lineno}:{key.value}")
    assert not hits, "Router 仍存在可注册域硬编码映射：" + ", ".join(hits)
