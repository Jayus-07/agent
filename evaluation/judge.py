"""LLM-as-Judge 评分器 — 用 LLM 对端到端答案进行 4 维质量评分。"""

from pydantic import BaseModel, Field, model_validator


class JudgeResult(BaseModel):
    """LLM 裁判的评分结果。"""
    scores: dict[str, int] = Field(description="4维评分: completeness/faithfulness/conciseness/citation_quality")
    total: float = Field(ge=1.0, le=5.0, description="加权综合分")
    reasoning: str = Field(description="评分理由")
    confidence: str = Field(default="medium", description="裁判置信度: low/medium/high")

    @model_validator(mode="after")
    def validate_scores_range(self):
        """校验每个评分维度必须在 1-5 范围内。"""
        for key, value in self.scores.items():
            if not (1 <= value <= 5):
                raise ValueError(f"Score out of range [1,5]: {key}={value}")
        return self


# JUDGE_SYSTEM_PROMPT 是文档/参考常量，定义 4 维评分标准和加权公式。
# 注意：此常量不直接发送给 LLM。由于 ChatOllama 不支持 system 消息角色，
# 评分维度以 inline 方式嵌入在 build_judge_prompt() 的 user prompt 中。
# 保留此常量便于维护者理解评分框架，修改评分维度时需同步更新 build_judge_prompt()。
JUDGE_SYSTEM_PROMPT = """你是一个严格但公正的评估裁判。你的任务是评估 AI 助手对用户问题的回答质量。

请从以下 4 个维度评分（每个维度 1-5 分）：

1. **完整性** (completeness): 是否回答了问题的所有部分？遗漏了关键信息吗？
2. **忠实性** (faithfulness): 所有数字、事实是否能追溯到数据源？有没有编造或幻觉？
3. **简洁性** (conciseness): 有没有冗余、重复或无关内容？表述是否精炼？
4. **引用质量** (citation_quality): 引用标注是否准确、充分？文档来源是否正确？

评分标准：
- 5: 优秀，无明显缺陷
- 4: 良好，有微小瑕疵
- 3: 及格，有可改进的空间
- 2: 较差，有明显错误或遗漏
- 1: 很差，基本不可用

综合分 = 完整性×0.35 + 忠实性×0.30 + 简洁性×0.15 + 引用质量×0.20

请输出以下格式的 JSON：
{
  "scores": {"completeness": 4, "faithfulness": 5, "conciseness": 3, "citation_quality": 4},
  "total": 4.15,
  "reasoning": "各维度评分说明...",
  "confidence": "medium"
}
"""


def build_judge_prompt(question: str, rubric: dict[str, str], actual_answer: str) -> str:
    """构造裁判 prompt。rubric 包含各维度的具体要求。"""
    rubric_lines = "\n".join(f"- {k}: {v}" for k, v in rubric.items())
    return f"""请评估以下 AI 助手对用户问题的回答。

请从以下 4 个维度评分（每个维度 1-5 分）：

1. 完整性: 是否回答了问题的所有部分？遗漏了关键信息吗？
2. 忠实性: 所有数字、事实是否能追溯到数据源？有没有编造或幻觉？
3. 简洁性: 有没有冗余、重复或无关内容？表述是否精炼？
4. 引用质量: 引用标注是否准确、充分？文档来源是否正确？

## 用户问题
{question}

## 评估标准
{rubric_lines}

## AI 回答
{actual_answer}

请输出以下格式的 JSON：
{{
  "scores": {{"completeness": 4, "faithfulness": 5, "conciseness": 3, "citation_quality": 4}},
  "total": 4.15,
  "reasoning": "各维度评分说明...",
  "confidence": "medium"
}}"""


def judge_answer(
    question: str,
    expected_rubric: dict[str, str],
    actual_answer: str,
) -> JudgeResult:
    """调用 LLM 对答案进行 4 维评分。

    Args:
        question: 用户原始问题
        expected_rubric: 评估标准, 如 {"completeness": "必须包含X和Y", ...}
        actual_answer: AI 的实际回答文本

    Returns:
        JudgeResult: 包含各维度分数、综合分、理由和置信度
    """
    try:
        from llm.llm_factory import get_llm
        import json

        llm = get_llm()
        prompt = build_judge_prompt(question, expected_rubric, actual_answer)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)

        # 尝试从 LLM 输出中提取 JSON
        content = content.strip()
        if content.startswith("```"):
            # 去掉 markdown 代码块包裹
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(content)

        # 校验分数范围
        for key in data.get("scores", {}):
            score = data["scores"][key]
            if not (1 <= score <= 5):
                raise ValueError(f"Score out of range [1,5]: {key}={score}")

        return JudgeResult(
            scores=data["scores"],
            total=round(data["total"], 2),
            reasoning=data.get("reasoning", ""),
            confidence=data.get("confidence", "medium"),
        )
    except Exception as e:
        # LLM 调用失败时返回默认低分
        return JudgeResult(
            scores={"completeness": 3, "faithfulness": 3, "conciseness": 3, "citation_quality": 3},
            total=3.0,
            reasoning=f"Judge evaluation failed: {e}",
            confidence="low",
        )
