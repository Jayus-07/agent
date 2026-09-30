"""test_domain_registration.py — 域注册显式化（P2-2）。

覆盖：
  - register_all_domains 幂等：重复调用零副作用（返回 0、registry 键集
    不变、无「重复注册」warning）
  - _DOMAIN_MODULES 清单卫生：每个模块路径真实可导入（防手写清单漂移）
  - build_graph 显式接线：构建后 5 域全数在册
"""
from __future__ import annotations

import logging


def test_register_all_domains_idempotent(caplog):
    """重复调用：返回 0、registry 不变、无重复注册 warning。"""
    from backend.domains import register_all_domains
    from backend.orchestration.domain_registry import domain_graph_registry

    first = register_all_domains()  # 包级 import 已注册 → 首调也应是 0
    keys_before = set(domain_graph_registry.get_all())

    with caplog.at_level(logging.WARNING):
        second = register_all_domains()

    assert first == 0
    assert second == 0
    assert set(domain_graph_registry.get_all()) == keys_before
    assert not any("重复注册" in r.message for r in caplog.records)


def test_domain_modules_list_resolves():
    """清单卫生：_DOMAIN_MODULES 每个路径真实存在（防手写漂移）。"""
    import importlib.util

    from backend.domains import _DOMAIN_MODULES

    assert len(_DOMAIN_MODULES) == 5
    for name in _DOMAIN_MODULES:
        assert importlib.util.find_spec(name) is not None, f"模块不存在: {name}"


def test_build_graph_registers_all_domains():
    """builder 显式调用接线：构建后 5 域全数在册（双保险生效面）。"""
    from backend.orchestration.domain_registry import domain_graph_registry
    from backend.orchestration.graph.builder import build_graph

    build_graph()
    names = set(domain_graph_registry.get_all())
    assert {
        "travel", "customer_service", "selection_funnel",
        "travel_commerce", "travel_booking",
    } <= names
