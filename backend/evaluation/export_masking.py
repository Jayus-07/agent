"""评测报告展示/导出层 PII 脱敏（C7-1/REL-06/EVD-10）。

口径（施工书 C7-1）：
- 评测原始文件（data/eval_runs/**）**不改动**——明细权威保留原文；
- 脱敏发生在**展示/导出层**：API 返回 report 前对样本级文本字段套
  shared/pii_mask 掩码器（手机号/身份证/邮箱等确定性 PII）；
- 默认开启（安全默认），``EVAL_EXPORT_PII_MASKING=false`` 显式关闭
  （仅供本地调试，生产保持开）。

掩码字段范围：question / generated_answer / contexts（含 details.page_content
与 ragas_input contexts）/ expected_answer / ground_truth 等文本。
指标数值、doc_id、状态等非文本字段不受影响。
"""
from __future__ import annotations

import os
from typing import Any

from backend.shared.logger import logger
from backend.shared.pii_mask import mask_pii

# 安全默认开：展示层脱敏是权限姿态的一部分（REL-06），关闭必须显式
MASKING_ENABLED = os.getenv("EVAL_EXPORT_PII_MASKING", "true").strip().lower() not in (
    "0", "false", "no",
)

# EvalResult.actual 内需要掩码的文本列表键
_ACTUAL_LIST_TEXT_KEYS = ("contexts",)
# EvalResult.actual 内需要掩码的文本标量键
_ACTUAL_TEXT_KEYS = ("question", "generated_answer", "final_answer")
# details（检索命中表）内需要掩码的文本键
_DETAIL_TEXT_KEYS = ("page_content", "snippet", "title")
# metrics 里仅 ragas_reason/错误消息是自由文本，其余为数值（不动）


def _mask_text(value: str) -> str:
    masked, _vault = mask_pii(value)
    return masked


def _mask_detail(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    for key in _DETAIL_TEXT_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = _mask_text(value)
    return out


def _mask_ragas_input(ragas_input: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ragas_input, dict):
        return ragas_input
    out = dict(ragas_input)
    if isinstance(out.get("question"), str):
        out["question"] = _mask_text(out["question"])
    if isinstance(out.get("answer"), str):
        out["answer"] = _mask_text(out["answer"])
    if isinstance(out.get("ground_truth"), str) and out["ground_truth"]:
        out["ground_truth"] = _mask_text(out["ground_truth"])
    contexts = out.get("contexts")
    if isinstance(contexts, list):
        out["contexts"] = [
            _mask_text(c) if isinstance(c, str) else c for c in contexts
        ]
    return out


def mask_result_for_viewer(result: dict[str, Any]) -> dict[str, Any]:
    """掩码单条样本结果（就地构造新 dict，不改入参）。"""
    out = dict(result)
    expected = out.get("expected")
    if isinstance(expected, dict):
        exp_out = dict(expected)
        for key in ("expected_answer", "ground_truth", "answer"):
            value = exp_out.get(key)
            if isinstance(value, str) and value:
                exp_out[key] = _mask_text(value)
        gt_contexts = exp_out.get("ground_truth_context")
        if isinstance(gt_contexts, list):
            exp_out["ground_truth_context"] = [
                (
                    {**c, "text": _mask_text(c["text"])}
                    if isinstance(c, dict) and isinstance(c.get("text"), str)
                    else (_mask_text(c) if isinstance(c, str) else c)
                )
                for c in gt_contexts
            ]
        out["expected"] = exp_out
    actual = out.get("actual")
    if isinstance(actual, dict):
        act_out = dict(actual)
        for key in _ACTUAL_TEXT_KEYS:
            value = act_out.get(key)
            if isinstance(value, str) and value:
                act_out[key] = _mask_text(value)
        for key in _ACTUAL_LIST_TEXT_KEYS:
            value = act_out.get(key)
            if isinstance(value, list):
                act_out[key] = [
                    _mask_text(v) if isinstance(v, str) else v for v in value
                ]
        details = act_out.get("details")
        if isinstance(details, list):
            act_out["details"] = [
                _mask_detail(d) if isinstance(d, dict) else d for d in details
            ]
        rejection = act_out.get("rejection")
        if isinstance(rejection, dict) and isinstance(rejection.get("query_entities"), str):
            act_out["rejection"] = {
                **rejection,
                "query_entities": _mask_text(rejection["query_entities"]),
            }
        act_out["ragas_input"] = _mask_ragas_input(act_out.get("ragas_input"))
        out["actual"] = act_out
    if isinstance(out.get("error_msg"), str) and out["error_msg"]:
        out["error_msg"] = _mask_text(out["error_msg"])
    return out


def mask_report_for_viewer(report: dict[str, Any]) -> dict[str, Any]:
    """掩码整份 report（API 详情/导出出口）；关闭开关时原样返回。"""
    if not MASKING_ENABLED:
        return report
    try:
        out = dict(report)
        results = out.get("results")
        if isinstance(results, list):
            out["results"] = [
                mask_result_for_viewer(r) if isinstance(r, dict) else r
                for r in results
            ]
        return out
    except Exception as e:  # noqa: BLE001 — 脱敏失败宁可少暴露：整份降级为骨架
        logger.warning(f"[eval-mask] 报告脱敏失败，降级返回摘要: {e}")
        skeleton = dict(report)
        skeleton.pop("results", None)
        return skeleton


__all__ = ["mask_report_for_viewer", "mask_result_for_viewer", "MASKING_ENABLED"]
