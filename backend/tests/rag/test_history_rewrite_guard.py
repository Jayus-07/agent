"""历史感知查询改写的确定性守卫。"""

import pytest

from backend.rag.chain import _needs_history_rewrite


@pytest.mark.parametrize(
    "question,expected",
    [
        ("员工账号密码有什么要求？", False),
        ("公司放假是几号", False),
        ("退款流程是什么", False),
        ("那密码呢", True),
        ("这个怎么弄", True),
        ("还有哪些要求", True),
        ("换成杭州", True),
        ("多久", True),
        ("你刚才列出三个审核阈值，请核对原始依据", True),
        (
            "请检索知识库中所有退款审核的金额门槛和对应审批角色，逐条列出金额、"
            "条件、角色和引用出处，并明确检查不同金额规则是否冲突；不要补造内容。",
            False,
        ),
    ],
)
def test_needs_history_rewrite(question, expected):
    assert _needs_history_rewrite(question) is expected
