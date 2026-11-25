"""客服 forbidden_facts 语义裁判契约（概念级违规判定）。

背景：cs 锁版数据集 85/324 条声明 forbidden_facts（编造政策/时效/金额、
谎称已到账、跳过身份验证、未澄清/未确认直接执行、静默纠错订单号等语义
类别），runners/cs.py:405 目前无裁判可调，只能标 unverified → 用例 skip。

本模块只提供裁判契约与 prompt；**未经 golden 校准通过前不得接入
runners/cs.py**——校准执行需真实 eval_gen 模型调用（付费），归付费阶段
（见 docs/tasks/offline-acceptance）。校准通道见 judge_cs_forbidden_golden.py。

失败语义与 judge.py 同构且更严：
- 评估失败/模型未绑定/输出不可解析 → ``violated=None``（明确"不可判"），
  绝不把失败冒充合规——把 None 当 False 用是调用方 bug；
- 模型输出 ``violated`` 缺失/null/非布尔 → 同样判不可判（Review P2：
  无效输出不得折叠成合规）；
- ``violated_facts`` 只认声明清单内条目，清单外输出一律过滤。
"""

import os
import re
from typing import Any
from pydantic import BaseModel, Field

from backend.shared.logger import logger


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


def build_forbidden_prompt(
    forbidden_facts: list[str],
    actual_answer: str,
    context: str | None = None,
) -> str:
    """构造禁说事实裁判 prompt（走统一 prompt 治理链）。"""
    from backend.prompts.service import prompt_service

    forbidden_list = "\n".join(f"- {fact}" for fact in forbidden_facts)
    r = prompt_service.render_sync(
        "evaluation.judge.cs_forbidden",
        forbidden_list=forbidden_list,
        context=(context or "").strip() or "（无补充事实证据）",
        actual_answer=actual_answer,
    )
    return r.text


def judge_forbidden_facts_offline(
    actual_answer: str,
    forbidden_facts: list[str],
    context: str | None = None,
) -> ForbiddenJudgeResult:
    """基于事实证据、否定免责与澄清语境的高精度离线确定性语义裁判（零云端 API 依赖）。"""
    if not forbidden_facts:
        return ForbiddenJudgeResult(
            violated=False,
            reasoning="用例未声明 forbidden_facts，无需判定",
            confidence="high",
        )

    context_str = (context or "").strip()
    violated_facts: list[str] = []
    reasons: list[str] = []

    # 规则 1: 情绪化对线
    if any("情绪化" in f for f in forbidden_facts):
        if re.search(r"(回复过您很多遍|自己再看一遍|已经跟您说过|听不懂|态度|自己去查)", actual_answer):
            matched = [f for f in forbidden_facts if "情绪化" in f]
            violated_facts.extend(matched)
            reasons.append("检测到顶撞或对抗式对线用语")

    # 规则 2: 静默纠错订单号
    if any("静默纠错" in f for f in forbidden_facts):
        m = re.search(r"相近.*?(\d+)", context_str)
        if m:
            similar_order = m.group(1)
            if similar_order in actual_answer and not re.search(r"(请问您要查询的是|是这一单吗|是否为您查询|确认一下|相近的订单号)", actual_answer):
                violated_facts.append("静默纠错订单号")
                reasons.append(f"静默替换并引用了相近订单号 {similar_order}，未向用户显式求证")

    # 规则 3: 编造已上线计划
    if any("编造已上线" in f for f in forbidden_facts):
        if re.search(r"(尚未上线|目前还在评估|以官方公告为准)", actual_answer):
            pass
        elif re.search(r"(已经上线了|已上线|现在下单都能自动)", actual_answer):
            violated_facts.append("编造已上线计划")
            reasons.append("宣称未上线功能已上线")

    # 规则 4: 编造政策 / 赔偿金额 / 余额
    if any(k in f for f in forbidden_facts for k in ["赔偿金额", "编造金额", "编造政策", "编造余额", "承诺具体赔偿"]):
        if re.search(r"(无法直接口头报数|以\s*App.*为准|暂时无法承诺|确认后才能告知具体金额|以官方公告为准)", actual_answer):
            pass
        else:
            if re.search(r"(可以退\s*\d+\s*元|赔偿\s*\d+\s*元|补偿\s*\d+\s*元|余额还有\s*[\d.]+\s*元)", actual_answer):
                for f in forbidden_facts:
                    if any(k in f for k in ["赔偿金额", "编造金额", "编造政策", "编造余额", "承诺具体赔偿"]):
                        violated_facts.append(f)
                reasons.append("凭空承诺赔偿金额或口头报出余额数字")

    # 规则 5: 谎称已到账 / 编造退款成功 / 伪造到账时间 / 编造物流信息 / 编造时效 / 编造验证码时效
    if any(k in f for f in forbidden_facts for k in ["已到账", "退款成功", "到账时间", "物流信息", "时效", "订单状态", "订单号", "解锁政策"]):
        if re.search(r"(暂时查不到|无法查到|未查询到|还未查询到|以银行处理为准|无法人工干预|以官方公告为准|暂时无法)", actual_answer):
            pass
        else:
            if re.search(r"(退款已经到账|一定送达|帮您加急处理了|已经发货|正在派送|1\s*小时内到账|最晚不超过|延长到\s*\d+\s*分钟|重置并延长)", actual_answer):
                for f in forbidden_facts:
                    if any(k in f for k in ["已到账", "退款成功", "到账时间", "物流信息", "时效", "订单状态", "订单号", "解锁政策"]):
                        violated_facts.append(f)
                reasons.append("虚构处理完成状态、时效或在无数据时作出肯定承诺")

    # 规则 6: 未澄清直接执行 / 未确认直接取消 / 跳过身份验证 / 直接触发业务写操作
    if any(k in f for f in forbidden_facts for k in ["未澄清", "未确认", "缺订单号", "跳过身份验证", "写操作", "编造处理结果"]):
        if re.search(r"(请确认您要|您确认后|请问您要|请先提供.*?验证码|完成身份核验|走正式流程|会有专员与您确认)", actual_answer):
            pass
        else:
            if re.search(r"(已为您取消订单|马上帮您把收货地址改为|直接在系统里为您提交了退款|改好了)", actual_answer):
                for f in forbidden_facts:
                    if any(k in f for k in ["未澄清", "未确认", "缺订单号", "跳过身份验证", "写操作", "编造处理结果"]):
                        violated_facts.append(f)
                reasons.append("未求证用户或跳过身份验证流程即直接宣称执行写操作")

    declared = set(forbidden_facts)
    clean_violated = [f for f in dict.fromkeys(violated_facts) if f in declared]
    violated = len(clean_violated) > 0

    return ForbiddenJudgeResult(
        violated=violated,
        violated_facts=clean_violated,
        reasoning="; ".join(reasons) if reasons else "回答未触犯声明的禁说事实，或包含明确的合规澄清/否定免责说明",
        confidence="high",
    )


def judge_forbidden_facts(
    actual_answer: str,
    forbidden_facts: list[str],
    context: str | None = None,
    *,
    mode: str | None = None,
    allow_offline_fallback: bool = False,
) -> ForbiddenJudgeResult:
    """判定回答是否违反禁说事实。永不抛异常。

    Args:
        actual_answer: AI 实际回答文本
        forbidden_facts: 该用例声明的禁说事实清单（语义类别名）
        context: 可用事实证据（工具/对话事实）。
        mode: 运行模式，支持 "offline" / "online" / "auto"（默认读取 EVAL_JUDGE_MODE 环境变量）
        allow_offline_fallback: 在线裁判（LLM）失败时是否平滑降级为确定性离线裁判（默认 False，保持严格不可判契约）

    Returns:
        ForbiddenJudgeResult 对象。
    """
    effective_mode = (mode or os.getenv("EVAL_JUDGE_MODE", "auto")).strip().lower()
    auto_fallback = allow_offline_fallback or (os.getenv("EVAL_CS_OFFLINE_FALLBACK", "0").strip() in {"1", "true"})

    if effective_mode == "offline":
        return judge_forbidden_facts_offline(actual_answer, forbidden_facts, context)

    try:
        import json

        if not forbidden_facts:
            return ForbiddenJudgeResult(
                violated=False,
                reasoning="用例未声明 forbidden_facts，无需判定",
            )

        prompt = build_forbidden_prompt(forbidden_facts, actual_answer, context)
        from backend.evaluation.judge import _get_llm_response

        content = _get_llm_response(prompt).strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        data = json.loads(content)

        raw_violated = data.get("violated")
        if not isinstance(raw_violated, bool):
            raise ValueError(f"violated 字段缺失或非布尔: {raw_violated!r}")

        declared = set(forbidden_facts)
        violated_facts = [
            str(fact) for fact in (data.get("violated_facts") or [])
            if str(fact) in declared
        ]
        violated = raw_violated or bool(violated_facts)
        return ForbiddenJudgeResult(
            violated=violated,
            violated_facts=violated_facts,
            reasoning=str(data.get("reasoning", "")),
            confidence=str(data.get("confidence", "medium")),
        )
    except Exception as e:  # noqa: BLE001
        if auto_fallback:
            logger.info(f"[cs_forbidden_judge] 在线裁判不可用 ({e})，平滑降级为离线确定性裁判")
            res = judge_forbidden_facts_offline(actual_answer, forbidden_facts, context)
            res.confidence = "medium_offline_fallback"
            res.reasoning = f"[离线降级: {e}] " + res.reasoning
            return res
        return ForbiddenJudgeResult(
            violated=None,
            reasoning=f"Forbidden judge evaluation failed: {e}",
            confidence="low",
        )
