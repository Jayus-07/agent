"""test_dag.py — DAG 拓扑分层 + cycle/missing 检测

覆盖 [docs/architecture/workflow-phase1.md](../../../docs/architecture/workflow-phase1.md) 验证项：
- 拓扑分层（Kahn's algorithm）正确性
- 循环检测（CycleDetectedError）
- 缺失依赖（MissingDependencyError）
- 自依赖检测
- 上下游查询
"""
from __future__ import annotations

import pytest

from backend.orchestration.workflow.dag import (
    DAG,
    CycleDetectedError,
    MissingDependencyError,
)
from backend.orchestration.workflow.meta import StepConfig


# ─────────────────────────────────────────────────────────────
# 拓扑分层（happy path + 边界）
# ─────────────────────────────────────────────────────────────

class TestDAGLayers:
    """DAG.layers 拓扑分层"""

    @pytest.mark.parametrize("steps,expected", [
        # 单节点
        ({"a": StepConfig(depends_on=[])}, [["a"]]),
        # 三并行
        (
            {"a": StepConfig(depends_on=[]), "b": StepConfig(depends_on=[]), "c": StepConfig(depends_on=[])},
            [["a", "b", "c"]],
        ),
        # 链式
        (
            {"a": StepConfig(depends_on=[]), "b": StepConfig(depends_on=["a"]), "c": StepConfig(depends_on=["b"])},
            [["a"], ["b"], ["c"]],
        ),
        # 菱形 DAG
        (
            {
                "a": StepConfig(depends_on=[]),
                "b": StepConfig(depends_on=["a"]),
                "c": StepConfig(depends_on=["a"]),
                "d": StepConfig(depends_on=["b", "c"]),
            },
            [["a"], ["b", "c"], ["d"]],
        ),
        # 类似 daily_report 风格（3 fetch 并行）
        (
            {
                "fetch_sales": StepConfig(depends_on=[]),
                "fetch_inventory": StepConfig(depends_on=[]),
                "fetch_promotions": StepConfig(depends_on=[]),
                "analyze": StepConfig(depends_on=["fetch_sales", "fetch_inventory", "fetch_promotions"]),
                "report": StepConfig(depends_on=["analyze"]),
                "send_email": StepConfig(depends_on=["report"]),
            },
            [["fetch_inventory", "fetch_promotions", "fetch_sales"], ["analyze"], ["report"], ["send_email"]],
        ),
    ])
    def test_topological_order(self, steps, expected):
        """DAG 分层正确性（含并行/串行/菱形）"""
        dag = DAG(steps)
        assert dag.layers == expected

    def test_layers_returns_list_of_lists(self):
        """layers 返回二维列表"""
        dag = DAG({"a": StepConfig(depends_on=[])})
        layers = dag.layers
        assert isinstance(layers, list)
        assert all(isinstance(layer, list) for layer in layers)

    def test_layers_within_layer_are_sorted(self):
        """同一层内节点按字母序排序（保证 deterministic）"""
        dag = DAG({
            "z": StepConfig(depends_on=[]),
            "a": StepConfig(depends_on=[]),
            "m": StepConfig(depends_on=[]),
        })
        assert dag.layers[0] == ["a", "m", "z"]


# ─────────────────────────────────────────────────────────────
# 错误检测
# ─────────────────────────────────────────────────────────────

class TestDAGValidation:
    """DAG 构造时校验错误"""

    def test_self_dependency_raises_value_error(self):
        """自依赖（a → a）应抛 ValueError"""
        with pytest.raises(ValueError, match="不能依赖自己"):
            DAG({"a": StepConfig(depends_on=["a"])})

    def test_cycle_detection_raises(self):
        """循环依赖应抛 CycleDetectedError"""
        # 先单独验证 property 会抛
        dag = DAG({
            "a": StepConfig(depends_on=["b"]),
            "b": StepConfig(depends_on=["a"]),
        })
        # 关键：property 必须被访问才抛，所以显式调用
        with pytest.raises(CycleDetectedError):
            _ = dag.layers

        # 简化版：直接断言调用 layers 会抛
        with pytest.raises(CycleDetectedError) as exc_info:
            _ = DAG({
                "a": StepConfig(depends_on=["b"]),
                "b": StepConfig(depends_on=["a"]),
            }).layers
        assert "循环依赖" in str(exc_info.value)
        assert "未访问的节点" in str(exc_info.value)

    def test_three_node_cycle(self):
        """三节点循环 a → b → c → a"""
        with pytest.raises(CycleDetectedError):
            _ = DAG({
                "a": StepConfig(depends_on=["c"]),
                "b": StepConfig(depends_on=["a"]),
                "c": StepConfig(depends_on=["b"]),
            }).layers

    def test_missing_dependency_raises(self):
        """depends_on 不存在的 step 应抛 MissingDependencyError"""
        with pytest.raises(MissingDependencyError) as exc_info:
            DAG({"a": StepConfig(depends_on=["ghost"])})
        assert "ghost" in str(exc_info.value)

    def test_partial_missing_dependency_lists_all(self):
        """多个 missing dependency 都要列出"""
        with pytest.raises(MissingDependencyError) as exc_info:
            DAG({"a": StepConfig(depends_on=["ghost1", "ghost2"])})
        assert "ghost1" in str(exc_info.value)
        assert "ghost2" in str(exc_info.value)

    def test_cycle_detected_error_is_value_error_subclass(self):
        """CycleDetectedError 是 ValueError 子类（向后兼容）"""
        assert issubclass(CycleDetectedError, ValueError)


# ─────────────────────────────────────────────────────────────
# downstream / upstream
# ─────────────────────────────────────────────────────────────

class TestDAGQueries:
    """DAG.upstream / downstream 查询方法"""

    def test_downstream_returns_immediate_dependents(self):
        """downstream 返回直接依赖当前 step 的下游节点"""
        dag = DAG({
            "a": StepConfig(depends_on=[]),
            "b": StepConfig(depends_on=["a"]),
            "c": StepConfig(depends_on=["a"]),
            "d": StepConfig(depends_on=["b", "c"]),
        })
        assert set(dag.downstream("a")) == {"b", "c"}
        assert dag.downstream("b") == ["d"]

    def test_upstream_returns_dependencies(self):
        """upstream 返回当前 step 直接依赖的上游"""
        dag = DAG({
            "a": StepConfig(depends_on=[]),
            "b": StepConfig(depends_on=["a"]),
            "c": StepConfig(depends_on=["a", "b"]),
        })
        assert dag.upstream("a") == []
        assert dag.upstream("b") == ["a"]
        assert set(dag.upstream("c")) == {"a", "b"}

    def test_downstream_for_unknown_step_returns_empty(self):
        """未知 step 的 downstream 返回空列表"""
        dag = DAG({"a": StepConfig(depends_on=[])})
        assert dag.downstream("nonexistent") == []


# ─────────────────────────────────────────────────────────────
# 边界
# ─────────────────────────────────────────────────────────────

class TestDAGEdgeCases:
    """DAG 边界情况"""

    def test_empty_dag_returns_empty_layers(self):
        """空 DAG 返回空 layers"""
        # 空 dict 不会被 init 校验，但 layers 应该空
        # 实际上 DAG.__post_init__ 会先校验 depends_on（空 dict 不报错）
        dag = DAG({})
        assert dag.layers == []

    def test_single_node_dag(self):
        """单节点 DAG"""
        dag = DAG({"only": StepConfig(depends_on=[])})
        assert dag.layers == [["only"]]

    def test_wide_parallel_dag(self):
        """10 个完全独立的 step"""
        steps = {f"step{i}": StepConfig(depends_on=[]) for i in range(10)}
        dag = DAG(steps)
        assert len(dag.layers) == 1
        assert len(dag.layers[0]) == 10

    def test_deep_chain_dag(self):
        """10 层链式依赖"""
        steps = {}
        for i in range(10):
            deps = [f"step{i-1}"] if i > 0 else []
            steps[f"step{i}"] = StepConfig(depends_on=deps)
        dag = DAG(steps)
        assert len(dag.layers) == 10
        assert dag.layers[0] == ["step0"]
        assert dag.layers[-1] == ["step9"]

    def test_layer_index_is_correct_for_diamond(self):
        """菱形 DAG 各层节点数正确（1, 2, 1）"""
        dag = DAG({
            "root": StepConfig(depends_on=[]),
            "left": StepConfig(depends_on=["root"]),
            "right": StepConfig(depends_on=["root"]),
            "sink": StepConfig(depends_on=["left", "right"]),
        })
        layer_sizes = [len(layer) for layer in dag.layers]
        assert layer_sizes == [1, 2, 1]