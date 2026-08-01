"""test_state_machine.py — 告警状态机单元测试

覆盖 11 个 transition case（决策 2 + 2.5）：
1. 首次+低 → CREATE
2. 首次+正常 → SILENT
3. open low → critical → UPGRADE
4. critical 0h（4h 内）→ SILENT
5. critical 5h → REMIND
6. critical → normal (open) → RESOLVE
7. resolved+再次异常 → REOPEN
8. resolved+正常 → SILENT
9. 人工 resolve (open) → RESOLVE 但 notify=False
10. 人工 resolve (无 case) → SILENT
11. critical → low → RESOLVE
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.orchestration.inventory.state_machine import (
    REMIND_INTERVAL_HOURS,
    decide,
    transition,
    transition_for_manual_resolve,
    transition_for_resolved_case,
)
from backend.orchestration.inventory.state import InventoryState


# 时间锚定（避免测试不稳定）
T = datetime(2026, 7, 31, 10, 0, 0)


class TestStateMachine:
    """告警状态机核心 11 case"""

    def test_1_first_alert_low(self):
        """首次+低库存 → CREATE"""
        d = decide(InventoryState.LOW, "warning", None, None, T)
        assert d.action == "CREATE"
        assert d.notify is True

    def test_2_first_alert_normal_silent(self):
        """首次+正常 → SILENT"""
        d = decide(InventoryState.NORMAL, "info", None, None, T)
        assert d.action == "SILENT"
        assert d.notify is False

    def test_3_low_to_critical_upgrade(self):
        """open low → critical → UPGRADE"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {"status": "open", "last_notified_at": T.isoformat()},
            {"event_type": "created", "to_state": "low"},
            T,
        )
        assert d.action == "UPGRADE"
        assert d.notify is True
        assert "low" in d.reason[0]
        assert "critical" in d.reason[0]

    def test_4_critical_0h_no_remind(self):
        """critical 0h (4h 内) → SILENT"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {"status": "open", "last_notified_at": T.isoformat()},
            {"event_type": "upgraded", "to_state": "critical"},
            T,
        )
        assert d.action == "SILENT"
        assert d.notify is False

    def test_5_critical_5h_remind(self):
        """critical 5h → REMIND（超过 4h 阈值）"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {"status": "open", "last_notified_at": (T - timedelta(hours=5)).isoformat()},
            {"event_type": "upgraded", "to_state": "critical"},
            T,
        )
        assert d.action == "REMIND"
        assert d.notify is True
        assert "4" in d.reason[0] or "critical" in d.reason[0]

    def test_5b_critical_exactly_4h_remind(self):
        """critical 正好 4h → REMIND（边界）"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {"status": "open", "last_notified_at": (T - timedelta(hours=4)).isoformat()},
            {"event_type": "upgraded", "to_state": "critical"},
            T,
        )
        assert d.action == "REMIND"

    def test_6_critical_to_normal_resolve(self):
        """critical → normal (open) → RESOLVE"""
        d = decide(
            InventoryState.NORMAL, "info",
            {"status": "open", "last_notified_at": T.isoformat()},
            {"event_type": "upgraded", "to_state": "critical"},
            T,
        )
        assert d.action == "RESOLVE"
        assert d.notify is True

    def test_7_resolved_reopen(self):
        """resolved + 再次异常 → REOPEN（D-1：同 case 复用）"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {
                "status": "resolved",
                "resolution_type": "AUTO_RECOVERED",
                "last_notified_at": T.isoformat(),
            },
            {"event_type": "resolved", "to_state": "normal"},
            T,
        )
        assert d.action == "REOPEN"
        assert d.notify is True
        assert "AUTO_RECOVERED" in d.reason[0]

    def test_8_resolved_normal_silent(self):
        """resolved + 正常 → SILENT"""
        d = decide(
            InventoryState.NORMAL, "info",
            {"status": "resolved", "resolution_type": "MANUAL_RESOLVED"},
            {"event_type": "resolved", "to_state": "normal"},
            T,
        )
        assert d.action == "SILENT"
        assert d.notify is False

    def test_9_manual_resolve_open(self):
        """人工 resolve (open) → RESOLVE 但 notify=False"""
        d = transition_for_manual_resolve(
            InventoryState.CRITICAL,
            {"status": "open", "last_notified_at": T.isoformat()},
        )
        assert d.action == "RESOLVE"
        assert d.notify is False
        assert "人工" in d.reason[0]

    def test_10_manual_resolve_no_case(self):
        """人工 resolve (无 case) → SILENT"""
        d = transition_for_manual_resolve(InventoryState.NORMAL, None)
        assert d.action == "SILENT"
        assert d.notify is False

    def test_11_critical_to_low_resolve(self):
        """critical → low (状态降级但不恢复正常) → RESOLVE"""
        d = decide(
            InventoryState.LOW, "warning",
            {"status": "open", "last_notified_at": T.isoformat()},
            {"event_type": "upgraded", "to_state": "critical"},
            T,
        )
        assert d.action == "RESOLVE"
        assert d.notify is True
        assert "critical" in d.reason[0]
        assert "low" in d.reason[0]

    def test_remind_interval_is_4_hours(self):
        """REMIND_INTERVAL_HOURS = 4（D-3 配置）"""
        assert REMIND_INTERVAL_HOURS == 4


class TestStateMachineEdgeCases:
    """边界 / 异常情况"""

    def test_resolved_manual_reopen(self):
        """resolved_manual + 异常 → REOPEN（区别于 resolved_auto）"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {
                "status": "resolved",
                "resolution_type": "MANUAL_RESOLVED",  # 人工标记
                "last_notified_at": T.isoformat(),
            },
            {"event_type": "resolved", "to_state": "normal"},
            T,
        )
        assert d.action == "REOPEN"  # 即使是 manual resolved，状态异常仍 reopen

    def test_resolved_closed_no_reopen(self):
        """closed 状态不触发 reopen（CLOSED 是终态）"""
        d = decide(
            InventoryState.CRITICAL, "critical",
            {
                "status": "closed",
                "resolution_type": "MANUAL_RESOLVED",
                "last_notified_at": T.isoformat(),
            },
            {"event_type": "closed", "to_state": "normal"},
            T,
        )
        # closed 是终态，不 reopen
        assert d.action == "SILENT"

    def test_low_to_low_silent(self):
        """low → low（无变化）→ SILENT"""
        d = decide(
            InventoryState.LOW, "warning",
            {"status": "open", "last_notified_at": T.isoformat()},
            {"event_type": "created", "to_state": "low"},
            T,
        )
        assert d.action == "SILENT"

    def test_low_to_low_reminder_due(self):
        """low 持续 5h（last_notified 5h 前）→ REMIND？"""
        # 注意：REMIND 只对 critical / out_of_stock 触发
        # low 不在 REMIND 范围
        d = decide(
            InventoryState.LOW, "warning",
            {"status": "open", "last_notified_at": (T - timedelta(hours=5)).isoformat()},
            {"event_type": "created", "to_state": "low"},
            T,
        )
        # low 持续不应 REMIND（决策 2 D-3）
        assert d.action == "SILENT"

    def test_out_of_stock_4h_remind(self):
        """out_of_stock 持续 4h → REMIND（决策 2 D-3）"""
        d = decide(
            InventoryState.OUT_OF_STOCK, "critical",
            {"status": "open", "last_notified_at": (T - timedelta(hours=4)).isoformat()},
            {"event_type": "upgraded", "to_state": "out_of_stock"},
            T,
        )
        assert d.action == "REMIND"
        assert d.notify is True