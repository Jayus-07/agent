"""RAG Skill 对知识库追问检索问题的补全测试。"""

from langchain_core.messages import AIMessage, HumanMessage

from backend.skills.rag.skill import RAGSkill


def test_knowledge_followup_restores_topic_from_previous_user_question():
    previous_question = "请检索退款审核金额门槛和对应审批角色"
    current_question = "请核对原始依据，并把刚才提到的金额门槛逐条对应到原文出处。"
    state = {
        "product_id": "agent",
        "question": current_question,
        "raw_query": current_question,
        "routing_context": {"active_domain": "knowledge"},
        "messages": [
            HumanMessage(content=previous_question),
            AIMessage(content="上轮回答中的引用不作为本轮检索依据。"),
            HumanMessage(content=current_question),
        ],
    }

    result = RAGSkill()._normalize_invocation_params(
        state,
        "rag.search",
        {"question": "核对原始依据，金额门槛对应原文出处", "kb_id": "policy"},
    )

    assert result["question"] == previous_question
    assert result["kb_id"] == "policy"
    assert "上轮回答中的引用" not in result["question"]
    assert "刚才" not in result["question"]


def test_knowledge_followup_uses_original_user_text_after_router_rewrite():
    previous_question = (
        "请检索知识库中所有退款审核的金额门槛和对应审批角色，逐条列出金额、"
        "条件、角色和引用出处，并明确检查不同金额规则是否冲突；不要补造内容。"
    )
    raw_followup = "请核对原始依据，并把刚才提到的金额门槛逐条对应到原文出处。"
    state = {
        "product_id": "agent",
        "question": "核对原始依据并将金额门槛逐条对应到原文出处",
        "raw_query": raw_followup,
        "routing_context": {"active_domain": "knowledge"},
        "messages": [
            HumanMessage(content=previous_question),
            AIMessage(content="上一轮答案"),
        ],
    }

    result = RAGSkill()._normalize_invocation_params(
        state,
        "rag.search",
        {"question": "核对原始依据并将金额门槛逐条对应到原文出处"},
    )

    assert result["question"] == (
        "请检索知识库中所有退款审核的金额门槛和对应审批角色 原文出处"
    )


def test_knowledge_followup_does_not_rewrite_other_domains():
    current_question = "请核对刚才提到的金额门槛。"
    state = {
        "product_id": "agent",
        "question": current_question,
        "routing_context": {"active_domain": "data"},
        "messages": [HumanMessage(content="请检索退款审核金额门槛")],
    }

    result = RAGSkill()._normalize_invocation_params(
        state, "rag.search", {"question": "模型生成的检索问题"},
    )

    assert result["question"] == "模型生成的检索问题"


def test_knowledge_followup_without_previous_user_question_keeps_model_query():
    state = {
        "product_id": "agent",
        "question": "请核对刚才提到的金额门槛。",
        "routing_context": {"active_domain": "knowledge"},
        "messages": [AIMessage(content="只存在助手答复，不可当成证据")],
    }

    result = RAGSkill()._normalize_invocation_params(
        state, "rag.search", {"question": "金额门槛原始依据"},
    )

    assert result["question"] == "金额门槛原始依据"
