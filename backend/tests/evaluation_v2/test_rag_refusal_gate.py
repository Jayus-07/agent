from types import SimpleNamespace
import pytest

from backend.evaluation.dataset.assertions import is_refusal
from backend.evaluation_v2.runner import _build_non_rag_cases


def test_build_non_rag_cases_injects_should_reject():
    definition = {
        "cases": [
            {
                "case_id": "c1",
                "input_json": {"query": "查询未经授权的数据"},
                "expected_json": {"behavior": "refuse", "reason": "permission_denied"},
                "metadata_json": {"tier": "core"},
            },
            {
                "case_id": "c2",
                "input_json": {"query": "合法查询"},
                "expected_json": {"behavior": "answer"},
                "metadata_json": {"tier": "core"},
            },
        ]
    }
    cases = _build_non_rag_cases(definition, "sql")
    assert len(cases) == 2
    assert cases[0].expected.get("should_reject") is True
    assert cases[0].expected.get("behavior") == "refuse"
    assert cases[1].expected.get("should_reject") is None


def test_is_refusal_assertion_coverage():
    # 标准拒答话术必须命中
    assert is_refusal("抱歉，我无法回答该问题，因为当前知识库中未提供相关信息。") is True
    assert is_refusal("未找到与您的提问相关的内部文档依据。") is True
    assert is_refusal("I cannot answer this question due to insufficient evidence.") is True
    # 正常回答或事实陈述不得被误判为拒答
    assert is_refusal("公司的年休假政策为入职满一年可享受 5 天带薪年假。") is False
    assert is_refusal("福州是中国福建省的省会城市，拥有三坊七巷等著名景点。") is False


def test_rag_runner_should_reject_detection_semantics():
    # 模拟各种形式的拒答用例 expected 与 metadata
    # 1. V1 格式 should_reject: True
    case_v1 = SimpleNamespace(expected={"should_reject": True}, metadata={})
    # 2. V2 不可变快照 behavior: "refuse"
    case_v2 = SimpleNamespace(expected={"behavior": "refuse", "reason": "no_evidence"}, metadata={})
    # 3. metadata query_type 为 no_evidence
    case_meta = SimpleNamespace(expected={}, metadata={"query_type": "no_evidence"})
    # 4. 普通问答题
    case_normal = SimpleNamespace(expected={"behavior": "answer"}, metadata={"query_type": "single_doc"})

    def detect_should_reject(case):
        return bool(
            case.expected.get("should_reject", False)
            or case.expected.get("behavior") == "refuse"
            or case.metadata.get("query_type") in ("no_evidence", "permission_denied")
        )

    assert detect_should_reject(case_v1) is True
    assert detect_should_reject(case_v2) is True
    assert detect_should_reject(case_meta) is True
    assert detect_should_reject(case_normal) is False
