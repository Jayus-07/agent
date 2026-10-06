"""Tool Governance 生产库存门禁测试。"""

from backend.scripts.tool_governance_check import build_report


def test_tool_inventory_has_spec_and_no_runtime_bypass():
    report = build_report()

    assert report["TOOL_INVENTORY_COMPLETE"] is True
    assert report["TOOL_BYPASS_COUNT"] == 0
    assert report["TOOL_GOVERNANCE_PRODUCTION_READY"] is True
