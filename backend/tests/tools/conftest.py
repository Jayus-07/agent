"""tests/tools/conftest.py — 工具层测试公共夹具

写操作工具（email/export/data_collection/competitor）已接入审批门
（TOOL_APPROVAL_MODE=required 时返回待审批提示而非执行）。工具层测试
关注的是工具本身的行为，统一切换 auto 审批模式放行；
审批门自身的行为测试在 tests/security/test_tool_approval.py。
"""
import pytest


@pytest.fixture(autouse=True)
def _auto_approval_mode(monkeypatch):
    """工具层测试默认 auto 审批模式（跳过审批门，行为与接入前一致）。"""
    from backend.security import tool_approval
    monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_MODE", "auto")
