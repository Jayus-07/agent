"""客服 forbidden_facts 语义裁判契约（概念级违规判定）。

背景：cs 锁版数据集 85/324 条声明 forbidden_facts（编造政策/时效/金额、
谎称已到账、跳过身份验证、未澄清/未确认直接执行、静默纠错订单号等语义
类别），runners/cs.py:405 目前无裁判可调，只能标 unverified → 用例 skip。

本模块只提供裁判契约与 prompt；**未经 golden 校准通过前不得接入
runners/cs.py**——校准执行需真实 eval_gen 模型调用（付费），归付费阶段
（见 docs/tasks/offline-acceptance）。校准通道见 judge_cs_forbidden_golden.py。

失败语义与 judge.py 同构且更严：评估失败/模型未绑定时 ``violated=None``
（明确"不可判"），绝不把失败冒充合规——把 None 当 False 用是调用方 bug。
"""

from pydantic import BaseModel, Field


class ForbiddenJudgeResult(BaseModel):
    """单条 forbidden_facts 语义裁判结果。"""

    violated: bool | None = Field(
        description="是否违反禁说事实；None=裁判不可用/评估失败",
    )
    violated_facts: list[str] = Field(
        default_factory=list,
        description="违反的禁说事实（逐字来自声明清单）",
    )
    reasoning: str = ""
    confidence: str = "medium"

    @property
    def available(self) -> bool:
        """裁判本次是否真正可用（False 时结果不得计入质量口径）。"""
        return self.violated is not None


def build_forbidden_prompt(forbidden_facts: list[str], actual_answer: str) -> str:
    """构造禁说事实裁判 prompt（走统一 prompt 治理链）。"""
    from backend.prompts.service import prompt_service

    forbidden_list = "\n".join(f"- {fact}" for fact in forbidden_facts)
    r = prompt_service.render_sync(
        "evaluation.judge.cs_forbidden",
        forbidden_list=forbidden_list,
        actual_answer=actual_answer,
    )
    return r.text


def judge_forbidden_facts(
    actual_answer: str,
    forbidden_facts: list[str],
) -> ForbiddenJudgeResult:
    """判定回答是否违反禁说事实。永不抛异常。

    Args:
        actual_answer: AI 实际回答文本
        forbidden_facts: 该用例声明的禁说事实清单（语义类别名）

    Returns:
        violated=None 表示裁判不可用（模型未绑定/调用失败/输出不可解析），
        此时结果不得当作合规使用。
    """
    try:
        import json

        if not forbidden_facts:
            return ForbiddenJudgeResult(
                violated=False,
                reasoning="用例未声明 forbidden_facts，无需判定",
            )

        prompt = build_forbidden_prompt(forbidden_facts, actual_answer)
        # 复用 judge 既有 LLM 链路（eval_gen 治理调用 + token 计量对齐）
        from backend.evaluation.judge import _get_llm_response

        content = _get_llm_response(prompt).strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        data = json.loads(content)

        declared = set(forbidden_facts)
        violated_facts = [
            str(fact) for fact in (data.get("violated_facts") or [])
            if str(fact) in declared
        ]
        violated = bool(data.get("violated")) or bool(violated_facts)
        return ForbiddenJudgeResult(
            violated=violated,
            violated_facts=violated_facts,
            reasoning=str(data.get("reasoning", "")),
            confidence=str(data.get("confidence", "medium")),
        )
    except Exception as e:  # noqa: BLE001 — 与 judge_answer 同口径：失败不冒充结论
        return ForbiddenJudgeResult(
            violated=None,
            reasoning=f"Forbidden judge evaluation failed: {e}",
            confidence="low",
        )
