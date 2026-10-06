"""Router 出口必须携带 V2 决策的回归测试。"""

from backend.orchestration.state import OrchestratorState


def test_route_decision_v2_is_registered_in_main_state_schema():
    assert "route_decision_v2" in OrchestratorState.__annotations__
