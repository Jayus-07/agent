"""test_rag_subject_resolution.py — RAG 检索主体判定（Chat/RAG 收口 2026-09-22）

背景（「知识库暂无相关资料」根因）：
  tools/rag.py 旧逻辑 = 未声明部门 → customer → 仅 cs_* 库可见。
  全量用户表 dept 为空（JWT 无 dept claim）→ 内部政策库从聊天主链整体不可达。

修正后的语义（与 knowledge_base.authorized_kbs 文档契约对齐）：
  - 带部门 → employee + 该部门（owner_depts 矩阵授权）
  - 已登录未声明部门 → employee + 空部门（"all" 库 = policy_general）
  - 匿名（guest/api-key）→ customer fail-safe（宁严勿漏，红线不动）
  - RAG_TOOL_FAILSAFE_CUSTOMER=false → 未声明主体（旧行为）
"""
import pytest

import backend.tools.rag as rag_tool_mod
from backend.core import request_context as rc


@pytest.fixture()
def captured(monkeypatch):
    """捕获 pipeline.ask 收到的主体参数；pipeline 本体不执行。"""
    got: dict = {}

    class _FakePipeline:
        def ask(self, question, session_id="default", kb_id="default",
                subject_type="", department="", permissions=None, **kw):
            got.update({"subject_type": subject_type, "department": department,
                        "kb_id": kb_id})
            return "ok"

    monkeypatch.setattr(rag_tool_mod, "_get_rag_pipeline", lambda: _FakePipeline())
    return got


def _bind(user_id: str = "", department: str = "", monkeypatch=None):
    """用真实 contextvar setter 绑定工具上下文（bind() 的最小组件）。"""
    from backend.core.request_context import (
        set_tool_department,
        set_tool_permissions,
        set_tool_user_id,
    )
    set_tool_user_id(user_id)
    set_tool_department(department)
    set_tool_permissions(None)


def _call():
    rag_tool_mod.search_knowledge_tool.invoke({"question": "差旅报销流程", "kb_id": "policy_general"})


class TestSubjectResolution:
    def test_logged_in_without_department_is_employee(self, captured, monkeypatch):
        """登录用户无部门 → employee（修复点：此前误判 customer）。"""
        _bind(user_id="u-42", department="", monkeypatch=monkeypatch)
        _call()
        assert captured["subject_type"] == "employee"
        assert captured["department"] == ""

    def test_logged_in_with_department(self, captured, monkeypatch):
        _bind(user_id="u-42", department="hr", monkeypatch=monkeypatch)
        _call()
        assert captured["subject_type"] == "employee"
        assert captured["department"] == "hr"

    @pytest.mark.parametrize("uid", ["", "default", "anonymous"])
    def test_anonymous_falls_back_to_customer(self, captured, monkeypatch, uid):
        """匿名/guest → customer fail-safe（宁严勿漏，红线不动）。"""
        _bind(user_id=uid, department="", monkeypatch=monkeypatch)
        _call()
        assert captured["subject_type"] == "customer"
        assert captured["department"] == ""

    def test_failsafe_off_keeps_legacy_unset(self, captured, monkeypatch):
        monkeypatch.setenv("RAG_TOOL_FAILSAFE_CUSTOMER", "false")
        _bind(user_id="", department="", monkeypatch=monkeypatch)
        _call()
        assert captured["subject_type"] == ""

    def test_kb_id_passthrough(self, captured, monkeypatch):
        _bind(user_id="u-42", department="", monkeypatch=monkeypatch)
        _call()
        assert captured["kb_id"] == "policy_general"
