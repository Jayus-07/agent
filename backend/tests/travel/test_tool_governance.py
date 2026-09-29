"""tests/travel/test_tool_governance.py — Tool 治理规格守护测试（v4 §5）

冻结口径：TRANSACTION 只经 Commerce、确认门只挂 TRANSACTION、
fee_bearing 只出现在资金相关工具、白名单只引用规范 actor。
"""
import pytest

from backend.travel.core.agent_base import AGENT_SPECS, CANONICAL_AGENTS
from backend.travel.tools.spec import (
    TOOL_SPECS,
    TOOL_SPECS_BY_NAME,
    SideEffect,
    ToolSpec,
)

# 门禁层节点（非 Agent）可持有的工具 actor 白名单
GATE_ACTORS = frozenset({"reporter"})
ALL_ACTORS = frozenset(CANONICAL_AGENTS) | GATE_ACTORS


class TestSpecIntegrity:
    def test_unique_names(self):
        names = [s.name for s in TOOL_SPECS]
        assert len(names) == len(set(names)), "工具名重复 = 治理面歧义"

    def test_by_name_index_consistent(self):
        assert len(TOOL_SPECS_BY_NAME) == len(TOOL_SPECS)
        for spec in TOOL_SPECS:
            assert TOOL_SPECS_BY_NAME[spec.name] is spec

    def test_every_spec_declares_capability_and_agents(self):
        for spec in TOOL_SPECS:
            assert isinstance(spec, ToolSpec)
            assert spec.capability, f"{spec.name} 缺 capability"
            assert spec.allowed_agents, f"{spec.name} 缺 allowed_agents"

    def test_allowed_agents_are_canonical_actors(self):
        declared = {
            agent for spec in TOOL_SPECS for agent in spec.allowed_agents
        }
        unknown = declared - ALL_ACTORS
        assert not unknown, f"白名单引用了未定义 actor：{unknown}"

    def test_agent_specs_cover_canonical_seven(self):
        assert set(AGENT_SPECS) == set(CANONICAL_AGENTS)
        assert len(CANONICAL_AGENTS) == 7


class TestTransactionGovernance:
    @staticmethod
    def _transaction_specs() -> list[ToolSpec]:
        return [s for s in TOOL_SPECS if s.side_effect is SideEffect.TRANSACTION]

    def test_transaction_only_via_commerce(self):
        specs = self._transaction_specs()
        assert specs, "TRANSACTION 工具面不应为空（hotel/flight/cancel）"
        for spec in specs:
            assert spec.allowed_agents == ("commerce",), (
                f"{spec.name} 的副作用面越出了 Commerce Agent"
            )

    def test_transaction_requires_confirmation_and_fee_bearing(self):
        for spec in self._transaction_specs():
            assert spec.requires_confirmation, f"{spec.name} 必须要求确认门"
            assert spec.fee_bearing, f"{spec.name} 资金相关必须标 fee_bearing"

    def test_confirmation_only_on_transaction(self):
        for spec in TOOL_SPECS:
            if spec.side_effect is not SideEffect.TRANSACTION:
                assert not spec.requires_confirmation, (
                    f"{spec.name}：确认门只挂 TRANSACTION（规划类决策走 v4 §6 审批单）"
                )

    def test_fee_bearing_only_on_transaction(self):
        for spec in TOOL_SPECS:
            if spec.fee_bearing:
                assert spec.side_effect is SideEffect.TRANSACTION


class TestFrozenSurface:
    def test_key_tools_present(self):
        # v4 §4 表的关键面：冻结名缺位 = 契约被破坏
        expected = {
            "travel.search_poi",
            "travel.weather.query",   # 缺位补位项
            "route.optimizer",        # 2-opt 接口
            "travel.memory.save",
            "travel.hotel.book",
            "travel.plan.diff",
        }
        assert expected <= set(TOOL_SPECS_BY_NAME)

    def test_side_effect_values_frozen(self):
        assert {m.value for m in SideEffect} == {
            "read", "compute", "write", "transaction",
        }

    @pytest.mark.parametrize(
        "name",
        ["travel.ticket.search", "travel.event.search", "travel.notice.search",
         "travel.train.search", "travel.currency.exchange"],
    )
    def test_contract_only_placeholders_declared(self, name):
        # 契约位必须登记规格（无真实源 ≠ 无治理面）
        spec = TOOL_SPECS_BY_NAME[name]
        assert spec.side_effect in (SideEffect.READ, SideEffect.COMPUTE)
        assert not spec.fee_bearing
