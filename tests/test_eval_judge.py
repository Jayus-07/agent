"""测试 evaluation/judge.py 的 LLM-as-Judge 逻辑。"""
import pytest
from evaluation.judge import JudgeResult, judge_answer, build_judge_prompt


class TestBuildJudgePrompt:
    def test_prompt_contains_all_elements(self):
        prompt = build_judge_prompt(
            question="冷藏肉类的保质期是多久？",
            rubric={"completeness": "必须给出48小时", "faithfulness": "数字必须来自手册"},
            actual_answer="冷藏肉类的保质期是48小时。根据《生鲜营运标准手册》..."
        )
        assert "冷藏肉类的保质期是多久" in prompt
        assert "48小时" in prompt
        assert "生鲜营运标准手册" in prompt
        assert "完整性" in prompt
        assert "忠实性" in prompt
        assert "简洁性" in prompt
        assert "引用质量" in prompt
        assert "1-5" in prompt


class TestJudgeResult:
    def test_valid_result(self):
        r = JudgeResult(
            scores={"completeness": 5, "faithfulness": 4, "conciseness": 3, "citation_quality": 4},
            total=4.15,
            reasoning="各方面表现良好",
            confidence="medium",
        )
        assert r.total == pytest.approx(4.15)
        assert r.confidence == "medium"

    def test_scores_must_be_1_to_5(self):
        with pytest.raises(Exception):
            JudgeResult(
                scores={"completeness": 6, "faithfulness": 0, "conciseness": 3, "citation_quality": 4},
                total=3.25,
                reasoning="",
                confidence="low",
            )
