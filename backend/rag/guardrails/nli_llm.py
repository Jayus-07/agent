"""nli_llm.py — LLM-as-Judge 整体评估（2026-08-11 替代 mDeBERTa 拆 claim）

优势 vs mDeBERTa：
  - 1 次 LLM 调用（不是 5-10 次 mDeBERTa 推理）
  - 5-10s 完成（vs 30s+）
  - 中文能力强（Qwen 原生）
  - 给出 reasoning（可解释）

用法:
    from backend.rag.guardrails.nli_llm import evaluate_with_llm
    result = evaluate_with_llm(answer, context_docs)
    # result.score (0-1), result.unsupported_claims (list), result.reason (str)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from backend.shared.logger import logger

# ── 配置（可从 .env 覆盖）──
NLI_LLM_TIMEOUT = 30  # 秒
NLI_LLM_MAX_CONTEXT_CHARS = 3000  # 喂给 Judge 的文档字符数上限
NLI_LLM_TEMPERATURE = 0.0  # 评估需要确定性


@dataclass
class LLMVerdict:
    """LLM-as-Judge 评估结果。"""

    score: float = 1.0  # 0-1 整体支撑度
    reason: str = ""  # 推理说明
    unsupported_claims: list[str] = field(default_factory=list)
    raw_output: str = ""  # 原始 LLM 输出（debug 用）
    fallback: bool = False  # 解析失败时 True（视为 fallback）
    fallback_reason: str = ""


# ── Prompt（few-shot 引导 JSON 输出）──
JUDGE_PROMPT = """你是一个 RAG 质量评估专家。

判断"LLM 回答"是否完全由"文档"支撑。

规则:
1. 逐句核对：回答中每个事实/数字/政策是否都能在文档中找到对应支撑
2. 文档没有提到但回答里有 → 视为 unsupported claim
3. 回答比文档保守（少说）→ 不算 unsupported
4. 整体评分 (0-1): 1.0=完全支撑, 0.5=部分支持, 0.0=完全不支持

【文档】
{context}

【LLM 回答】
{answer}

请输出 JSON（只输出 JSON，不要其他文字）:
{{
  "score": 0.0 到 1.0,
  "reason": "一句话说明判断依据",
  "unsupported_claims": ["未被支撑的句子1", "未被支撑的句子2"]
}}"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON（容忍 markdown 包裹 / 前缀文字）。"""
    # 尝试直接 parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # 尝试找 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 尝试找第一个 { ... } 块
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _build_context(context_docs: list) -> str:
    """拼接前 N 篇文档为单字符串（限制字符数）。"""
    parts = []
    total = 0
    for d in context_docs:
        content = d.page_content if hasattr(d, "page_content") else str(d)
        if total + len(content) > NLI_LLM_MAX_CONTEXT_CHARS:
            break
        parts.append(content)
        total += len(content)
    return "\n\n---\n\n".join(parts) if parts else ""


def evaluate_with_llm(answer: str, context_docs: list) -> LLMVerdict:
    """用 Qwen 整体评估 Faithfulness（替代 mDeBERTa）。

    Returns:
        LLMVerdict 包含 score / reason / unsupported_claims
        fallback=True 表示 LLM 推理失败（如超时/解析失败），视为全部支持
    """
    import time
    from backend.infra.timeout import safe_call_with_timeout
    from backend.infra.llm import llm

    context = _build_context(context_docs)
    if not context:
        return LLMVerdict(score=1.0, reason="no_context", fallback=True, fallback_reason="no_context")
    if not answer or not answer.strip():
        return LLMVerdict(score=1.0, reason="no_answer", fallback=True, fallback_reason="no_answer")

    prompt = JUDGE_PROMPT.format(context=context[:NLI_LLM_MAX_CONTEXT_CHARS], answer=answer[:2000])

    t0 = time.time()
    try:
        raw = safe_call_with_timeout(
            llm.invoke,
            timeout=NLI_LLM_TIMEOUT,
            default_value=None,
            error_message=f"[NLI-LLM] 推理超时 ({NLI_LLM_TIMEOUT}s)",
            input=[{"role": "user", "content": prompt}],
        )
        latency_ms = int((time.time() - t0) * 1000)
    except Exception as e:
        logger.warning(f"[NLI-LLM] 推理异常: {e}")
        return LLMVerdict(score=1.0, reason=str(e), fallback=True, fallback_reason="nli_error")

    if raw is None:
        logger.warning(f"[NLI-LLM] 推理超时（{NLI_LLM_TIMEOUT}s）")
        try:
            from backend.observability.metrics import nli_timeout_total
            nli_timeout_total.inc()
        except Exception:
            pass
        return LLMVerdict(
            score=1.0, reason="timeout", fallback=True, fallback_reason="nli_timeout"
        )

    # 提取 content
    content = raw.content if hasattr(raw, "content") else str(raw)

    # 解析 JSON
    parsed = _extract_json(content)
    if not parsed:
        logger.warning(f"[NLI-LLM] JSON 解析失败: {content[:200]}")
        try:
            from backend.observability.metrics import nli_timeout_total
            nli_timeout_total.inc()
        except Exception:
            pass
        return LLMVerdict(
            score=1.0, reason="parse_failed", fallback=True,
            fallback_reason="nli_parse_failed", raw_output=content[:500]
        )

    score = float(parsed.get("score", 0.5))
    # 截断到 [0, 1]
    score = max(0.0, min(1.0, score))
    reason = str(parsed.get("reason", ""))
    unsupported = parsed.get("unsupported_claims", []) or []

    logger.info(
        f"[NLI-LLM] 评估完成: score={score:.2f} unsupported={len(unsupported)} latency={latency_ms}ms"
    )
    return LLMVerdict(
        score=score,
        reason=reason,
        unsupported_claims=[str(c) for c in unsupported],
        raw_output=content[:500],
    )
