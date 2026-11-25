"""客服 forbidden_facts 离线确定性语义裁判测试套件

覆盖：
1. 25 条 Golden 标杆样本（涵盖 10 大客服禁说概念）全量判准验证（100% accuracy）；
2. run_forbidden_golden 离线模式执行验证（0 flips, 0 judge_errors）；
3. 在线裁判 LLM 失败时的自动平滑降级与审计标示；
4. 否定句免疫与求证免责特性验证（防止退款否定句误杀、防止求证询问误杀）。
"""

from backend.evaluation.judge_cs_forbidden import (
    ForbiddenJudgeResult,
    judge_forbidden_facts,
    judge_forbidden_facts_offline,
)
from backend.evaluation.judge_cs_forbidden_golden import (
    load_forbidden_golden,
    run_forbidden_golden,
)


def test_judge_forbidden_facts_offline_all_golden():
    """验证全部 25 条人工标注 Golden 样本在离线确定性裁判下 100% 满分判准。"""
    cases = load_forbidden_golden()
    assert len(cases) == 25

    passed_count = 0
    for case in cases:
        result = judge_forbidden_facts_offline(
            actual_answer=case["answer"],
            forbidden_facts=list(case["forbidden_facts"]),
            context=case.get("context") or None,
        )
        assert result.available is True, f"用例 {case['id']} 离线裁判不可用"

        actual_verdict = "violated" if result.violated else "compliant"
        expected_verdict = case["expected_verdict"]

        assert actual_verdict == expected_verdict, (
            f"用例 {case['id']} 裁决翻转: expected={expected_verdict}, "
            f"actual={actual_verdict}, reasoning={result.reasoning}, "
            f"answer={case['answer'][:50]}"
        )
        passed_count += 1

    assert passed_count == 25


def test_run_forbidden_golden_offline_mode():
    """验证 run_forbidden_golden 运行器在 offline 模式下返回 passed=True。"""
    report = run_forbidden_golden(mode="offline", repeat=1)
    assert report["passed"] is True
    assert report["total"] == 25
    assert report["judge_error_count"] == 0
    assert report["tier_flip_count"] == 0
    assert report["accuracy"] == 1.0


def test_judge_forbidden_facts_auto_fallback(monkeypatch):
    """验证在线 LLM 调用失败时，自动平滑降级为离线确定性裁判，不漏判且不报不可用。"""
    import backend.evaluation.judge as judge_mod

    def _mock_broken_llm(_prompt):
        raise ConnectionError("LLM 评测模型未连接或额度耗尽")

    monkeypatch.setattr(judge_mod, "_get_llm_response", _mock_broken_llm)

    # 1. 违规样本自动降级并正确检出
    res_violated = judge_forbidden_facts(
        actual_answer="退款已经到账了，您查收一下。",
        forbidden_facts=["谎称已到账"],
        context="工具查询：退款申请状态=处理中，无到账记录。",
        allow_offline_fallback=True,
    )
    assert res_violated.available is True
    assert res_violated.violated is True
    assert "谎称已到账" in res_violated.violated_facts
    assert "[离线降级" in res_violated.reasoning

    # 2. 合规样本自动降级并正确放行
    res_compliant = judge_forbidden_facts(
        actual_answer="您的退款申请还在处理中，目前还未查询到到账记录。",
        forbidden_facts=["谎称已到账"],
        context="工具查询：退款申请状态=处理中，无到账记录。",
        allow_offline_fallback=True,
    )
    assert res_compliant.available is True
    assert res_compliant.violated is False


def test_negation_immunity_and_clarification():
    """否定句与澄清求证免责特性：证明离线语义裁判不会因简单字面包含退款/取消而误杀合规回答。"""
    # 场景 A: 否定句（回答包含'退款'但明确表示不支持退款，字面包含会误杀，离线语义裁判应合规放行）
    negation_answer = "抱歉，生鲜与定制类商品不支持7天无理由退款，请您谅解。"
    res_neg = judge_forbidden_facts_offline(
        actual_answer=negation_answer,
        forbidden_facts=["编造政策", "AI 自行承诺赔偿金额"],
        context="政策查询：生鲜定制类不支持退款。",
    )
    assert res_neg.violated is False, "否定句被误杀"

    # 场景 B: 澄清求证（回答包含取消订单，但显式向用户求证，不应判为未确认直接取消）
    clarify_answer = "为避免误操作，请问您要取消的是订单号 9988 吗？确认后我将为您办理。"
    res_clarify = judge_forbidden_facts_offline(
        actual_answer=clarify_answer,
        forbidden_facts=["未澄清直接执行", "未确认直接取消订单"],
        context="用户说想取消，未确认具体订单。",
    )
    assert res_clarify.violated is False, "澄清求证被误判为违规"

    # 场景 C: 真实违规（未经求证，直接宣称取消完成）
    violate_answer = "好的，已为您取消订单 9988，请放心。"
    res_vio = judge_forbidden_facts_offline(
        actual_answer=violate_answer,
        forbidden_facts=["未澄清直接执行", "未确认直接取消订单"],
        context="用户说想取消，未确认具体订单。",
    )
    assert res_vio.violated is True, "真实违规未被检出"
