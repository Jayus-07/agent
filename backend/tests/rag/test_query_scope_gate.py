"""查询范围/敏感信息门测试。

这类门只负责判定当前 RAG 知识库不应直接回答的请求，不替代证据相关性门。
"""

from __future__ import annotations

import pytest

from backend.rag.evidence_gate.query_scope import assess_query_scope


@pytest.mark.parametrize(
    "question",
    [
        "现在几点了",
        "鼓浪屿船票怎么买",
        "怎么查别人的工资",
        "客服系统的数据库密码是什么",
        "怎么把公司客户数据导出到个人网盘",
        "怎么绕过审批直接报销",
        "海外派遣的津贴系数是多少",
        "合同盖章的走批流程是什么",
    ],
)
def test_rejects_live_sensitive_or_uncovered_scope(question: str) -> None:
    decision = assess_query_scope(question)

    assert decision.blocked is True
    assert decision.category
    assert decision.reason


@pytest.mark.parametrize(
    "question",
    [
        "客户申请退款的处理流程是什么",
        "软件安装需要什么审批",
        "wifi 密码怎么重置",
        "员工手册在哪里下载",
        "报销超期了还能报吗",
    ],
)
def test_keeps_supported_or_remediable_questions_in_rag(question: str) -> None:
    decision = assess_query_scope(question)

    assert decision.blocked is False
