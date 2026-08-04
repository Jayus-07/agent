"""test_workflow_inventory_alert.py — InventoryAlert Workflow 端到端测试

覆盖：
- DAG 结构（8 Step 6 层）
- Workflow 注册到 Registry
- Step 方法可被 collect_step_methods 扫描
"""
from __future__ import annotations

import pytest

from backend.orchestration.workflow import workflow, step
from backend.orchestration.workflow.dag import DAG
from backend.orchestration.workflow.meta import collect_step_methods
from backend.orchestration.workflows.inventory_alert import InventoryAlert


class TestWorkflowStructure:
    """Workflow 静态结构验证"""

    def test_class_has_workflow_decorator(self):
        """InventoryAlert 已被 @workflow 装饰"""
        from backend.orchestration.workflow.meta import get_workflow_meta
        meta = get_workflow_meta(InventoryAlert)
        assert meta is not None
        assert meta.name == "inventory_alert"

    def test_metadata_contains_chinese_business_terms(self):
        """metadata 包含中文业务对象 + 动作"""
        from backend.orchestration.workflow.meta import get_workflow_meta
        meta = get_workflow_meta(InventoryAlert)
        assert "库存" in meta.objects
        assert "补货" in meta.objects
        assert "扫描" in meta.actions

    def test_examples_provided(self):
        """metadata 包含 examples"""
        from backend.orchestration.workflow.meta import get_workflow_meta
        meta = get_workflow_meta(InventoryAlert)
        assert len(meta.examples) > 0


class TestStepCollection:
    """8 Step 收集"""

    def test_collects_exactly_8_steps(self):
        steps = collect_step_methods(InventoryAlert)
        assert len(steps) == 8

    def test_step_names_match_design(self):
        """Step 名称与设计一致"""
        steps = collect_step_methods(InventoryAlert)
        expected = {
            "scan_inventory",
            "fetch_sales_history",
            "calculate_inventory_health",
            "evaluate_thresholds",
            "alert_state_machine",
            "create_event",
            "load_notification_policies",
            "send_alert_email",
        }
        assert set(steps.keys()) == expected


class TestDAGTopology:
    """DAG 拓扑分层"""

    def test_dag_has_6_layers(self):
        """8 Step 排成 6 层"""
        steps = collect_step_methods(InventoryAlert)
        dag = DAG({n: cfg for n, (_, cfg) in steps.items()})
        assert len(dag.layers) == 6

    def test_layer_0_has_2_parallel_steps(self):
        """Layer 0: scan_inventory + fetch_sales_history 并行"""
        steps = collect_step_methods(InventoryAlert)
        dag = DAG({n: cfg for n, (_, cfg) in steps.items()})
        assert set(dag.layers[0]) == {"scan_inventory", "fetch_sales_history"}

    def test_layer_4_has_2_parallel_steps(self):
        """Layer 4: create_event + load_notification_policies 并行"""
        steps = collect_step_methods(InventoryAlert)
        dag = DAG({n: cfg for n, (_, cfg) in steps.items()})
        assert set(dag.layers[4]) == {"create_event", "load_notification_policies"}

    def test_layer_5_is_send_alert_email(self):
        """Layer 5: send_alert_email（终点）"""
        steps = collect_step_methods(InventoryAlert)
        dag = DAG({n: cfg for n, (_, cfg) in steps.items()})
        assert dag.layers[5] == ["send_alert_email"]

    def test_no_cycles(self):
        """无循环（用 layers 自动检测）"""
        steps = collect_step_methods(InventoryAlert)
        dag = DAG({n: cfg for n, (_, cfg) in steps.items()})
        # layers 不抛 = 无环
        layers = dag.layers
        assert len(layers) == 6


class TestStepConfig:
    """StepConfig 字段"""

    def test_send_email_has_retry(self):
        """send_alert_email 配 retry=2（决策 2：邮件失败要重试）"""
        steps = collect_step_methods(InventoryAlert)
        cfg = steps["send_alert_email"][1]
        assert cfg.retry == 2
        assert cfg.on_error == "abort"  # 邮件失败必须让 workflow 失败

    def test_calculate_inventory_health_timeout(self):
        """calculate_inventory_health 配 timeout=30s"""
        steps = collect_step_methods(InventoryAlert)
        cfg = steps["calculate_inventory_health"][1]
        assert cfg.timeout_sec == 30


class TestWorkflowRegistry:
    """Workflow 注册到 Registry"""

    def test_can_register_to_registry(self):
        from backend.orchestration.workflow.registry import WorkflowRegistry
        reg = WorkflowRegistry()
        reg.register(InventoryAlert)
        assert reg.get("inventory_alert") is InventoryAlert

    def test_build_router_index_includes_inventory_alert(self):
        from backend.orchestration.workflow.registry import WorkflowRegistry
        reg = WorkflowRegistry()
        reg.register(InventoryAlert)
        reg.build_router_index()
        assert "inventory_alert" in reg.router_index
        entry = reg.router_index["inventory_alert"]
        assert "库存" in entry.objects
        assert "扫描" in entry.actions

    def test_router_can_route_chinese_query_to_inventory_alert(self):
        """TaskRouter 能匹配中文 '扫描库存风险' 到 inventory_alert"""
        import asyncio
        from backend.orchestration.workflow.registry import WorkflowRegistry
        from backend.orchestration.workflow.router import TaskRouter

        reg = WorkflowRegistry()
        reg.register(InventoryAlert)
        reg.build_router_index()
        router = TaskRouter(registry=reg)

        async def run():
            return await router.route("扫描库存风险")
        result = asyncio.run(run())
        assert result.is_workflow
        assert result.workflow_candidate == "inventory_alert"