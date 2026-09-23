"""usage_parse.py — Provider token usage 统一解析层（Model Governance STOP C / C4）

背景：proxy._record_tokens 与 tracer.parse_tokens 各自维护一份几乎逐行相同
的 usage 解析（STOP A 审计 P1-13，2026-09-22 的 billable_input 口径演进就
只改了 proxy 侧造成漂移）。本模块是唯一解析实现，双方只做委托。

口径（与 LangChain 三家 Provider 的透传约定一致）：
  - token 总量：response_metadata.token_usage > usage_metadata > llm_output.token_usage
    （ChatOpenAI 用 prompt_tokens/completion_tokens；ChatAnthropic 用
    input_tokens/output_tokens，两者这里统一兼容）
  - 细粒度：input_token_details.cache_read（缓存命中，⊆input）、
    output_token_details.reasoning（推理）
  - 上游未返回 usage 时返回空 dict —— 调用方按各自语义处理
    （proxy 记缺失打点，tracer 记 0），本层不猜不补。

⚠️ 本模块只解析「数量」，不解析「身份」：model/provider 一律来自
ResolvedModelContext / Registry（C4 红线：identity 不由 parser 猜）。
"""
from __future__ import annotations

from typing import Any


def extract_usage_payload(result: Any) -> dict:
    """从 LLM 返回值提取原始 usage 字典（未命中返回空 dict）。"""
    tu: dict = {}
    if hasattr(result, "response_metadata") and result.response_metadata:
        tu = result.response_metadata.get("token_usage", {}) or {}
    if not tu and hasattr(result, "usage_metadata") and result.usage_metadata:
        tu = result.usage_metadata
    # LLMResult 聚合结果（generate/agenerate 返回）的兼容路径
    if not tu and hasattr(result, "llm_output") and result.llm_output:
        tu = result.llm_output.get("token_usage", {}) or {}
    return tu if isinstance(tu, dict) else {}


def parse_provider_usage(result: Any) -> dict:
    """统一 usage 解析：数量字段齐全的 dict；无 usage 返回空 dict。"""
    try:
        tu = extract_usage_payload(result)
        if not tu:
            return {}
        p = tu.get("prompt_tokens", tu.get("input_tokens", 0))
        c = tu.get("completion_tokens", tu.get("output_tokens", 0))
        t = tu.get("total_tokens", p + c)
        if not t:
            return {}
        in_details = tu.get("input_token_details") or {}
        out_details = tu.get("output_token_details") or {}
        return {
            "prompt_tokens": int(p or 0),
            "completion_tokens": int(c or 0),
            "total_tokens": int(t or 0),
            "cached_tokens": int(in_details.get("cache_read", 0) or 0),
            "reasoning_tokens": int(out_details.get("reasoning", 0) or 0),
        }
    except Exception:
        # 解析失败等价于无 usage（调用方按缺失语义处理），不向上抛
        return {}
