"""workflow/dag.py — DAG 拓扑分层

设计原则（按企业方案）：
- 并行由 Runtime 根据 DAG 自动计算（不要 parallel_with 字段）
- 规则：无 depends_on = 独立节点；其他节点依赖什么就 depends_on 什么
- 分层算法：拓扑排序，每层内的节点可并行
- 循环检测：分层失败时抛 CycleDetectedError
- 缺失依赖检测：启动时报错（depends_on 的 step 不存在）
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


class CycleDetectedError(ValueError):
    """DAG 存在循环依赖"""


class MissingDependencyError(ValueError):
    """depends_on 引用的 step 不存在"""


@dataclass
class DAG:
    """Workflow DAG — 从 step 集合 + depends_on 构建

    用法：
        dag = DAG(steps={step_name: step_config})
        layers = dag.layers  # [[step_1, step_2], [step_3], ...]
    """

    steps: dict[str, object]  # step_name → StepConfig（避免循环 import 用 object）

    def __post_init__(self):
        # 1. 校验：depends_on 的 step 必须存在
        all_names = set(self.steps.keys())
        for name, cfg in self.steps.items():
            deps = getattr(cfg, "depends_on", [])
            missing = [d for d in deps if d not in all_names]
            if missing:
                raise MissingDependencyError(
                    f"Step {name!r} depends on missing steps: {missing}"
                )
            # 检测自依赖
            if name in deps:
                raise ValueError(f"Step {name!r} 不能依赖自己")

    @property
    def layers(self) -> list[list[str]]:
        """拓扑分层：每层内的节点可并行

        例：
            fetch_sales  ─┐
            fetch_inventory ─┤── analyze ── send_email
            fetch_promotions ─┘

        返回: [["fetch_sales", "fetch_inventory", "fetch_promotions"],
               ["analyze"],
               ["send_email"]]
        """
        # Kahn's algorithm
        in_degree: dict[str, int] = {n: 0 for n in self.steps}
        # adj[u] = v 表示 u 是 v 的依赖 (即 v → u)，但分层时反向
        # 我们要构建 reverse: who depends on me
        dependents: dict[str, list[str]] = defaultdict(list)

        for name, cfg in self.steps.items():
            deps = getattr(cfg, "depends_on", [])
            in_degree[name] = len(deps)
            for dep in deps:
                dependents[dep].append(name)

        layers: list[list[str]] = []
        # 初始化：入度为 0 的节点（无依赖）
        current = sorted([n for n, d in in_degree.items() if d == 0])
        visited = 0

        while current:
            layers.append(current)
            visited += len(current)
            next_layer: list[str] = []
            for node in current:
                for dep in dependents[node]:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        next_layer.append(dep)
            current = sorted(next_layer)

        if visited != len(self.steps):
            remaining = [n for n, d in in_degree.items() if d > 0]
            raise CycleDetectedError(
                f"DAG 存在循环依赖，未访问的节点: {remaining}"
            )

        return layers

    def downstream(self, step_name: str) -> list[str]:
        """返回直接依赖当前 step 的下游节点（用于调试 / 可视化）"""
        dependents: list[str] = []
        for name, cfg in self.steps.items():
            deps = getattr(cfg, "depends_on", [])
            if step_name in deps:
                dependents.append(name)
        return dependents

    def upstream(self, step_name: str) -> list[str]:
        """返回当前 step 直接依赖的上游节点"""
        cfg = self.steps.get(step_name)
        if cfg is None:
            return []
        return list(getattr(cfg, "depends_on", []))