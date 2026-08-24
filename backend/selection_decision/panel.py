"""selection_decision/panel.py — N 人 AI 评审团独立投票（spec §4 验证层）

设计：
- 7 个预置角色（独立 System Prompt 视角），按 size 截取
- 每个评审独立调用 LLM（asyncio.to_thread 并行），输出结构化 JSON 票
- 单个评审失败不崩溃：该票记 no_go/0 分并带 error 标记
- 聚合规则：多数票 go 且均分 ≥ 60 → pass
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from backend.infra.llm import llm
from backend.shared.logger import logger

PASS_AVG_SCORE = 60

PERSONAS: list[dict[str, str]] = [
    {"role": "风控官", "focus": "平台风控、封号风险、资金安全"},
    {"role": "供应链专家", "focus": "采购成本、备货周期、断货风险"},
    {"role": "流量操盘手", "focus": "获客成本、流量结构、推广ROI"},
    {"role": "用户研究员", "focus": "用户痛点真实性、需求频次、复购意愿"},
    {"role": "财务分析师", "focus": "利润模型、现金流、盈亏平衡"},
    {"role": "品类战略师", "focus": "竞争格局、差异化空间、品类生命周期"},
    {"role": "合规顾问", "focus": "平台规则、知识产权、资质要求"},
]

_VOTE_SYSTEM = (
    "你是{role}，专长领域：{focus}。基于给定的选品决策材料独立评审，"
    "不受他人意见影响。只回复一个 JSON 对象，不要任何其他文字："
    '{{"score": 0到100的整数, "verdict": "go"或"no_go", "reason": "50字以内理由"}}'
)


def _single_review(persona: dict[str, str], summary: dict[str, Any]) -> dict[str, Any]:
    """单个评审投票（同步，运行在 to_thread 中）"""
    from langchain_core.messages import HumanMessage, SystemMessage
    vote = {"role": persona["role"], "focus": persona["focus"],
            "score": 0, "verdict": "no_go", "reason": "", "error": False}
    try:
        resp = llm.invoke([
            SystemMessage(content=_VOTE_SYSTEM.format(**persona)),
            HumanMessage(content=json.dumps(summary, ensure_ascii=False, default=str)),
        ])
        data = json.loads(resp.content.strip().strip("`").removeprefix("json").strip())
        vote["score"] = int(data["score"])
        vote["verdict"] = "go" if data["verdict"] == "go" else "no_go"
        vote["reason"] = str(data.get("reason", ""))[:200]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"[Panel] {persona['role']} 评审解析失败，记 no_go: {e}")
        vote.update(reason="评审输出解析失败", error=True)
    except Exception as e:
        logger.warning(f"[Panel] {persona['role']} 评审调用失败，记 no_go: {e}")
        vote.update(reason=f"评审调用失败: {e}", error=True)
    return vote


def aggregate_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    """投票聚合：多数 go 且均分 ≥ 60 → pass"""
    size = len(votes)
    go_count = sum(1 for v in votes if v["verdict"] == "go")
    avg = sum(v["score"] for v in votes) / size if size else 0.0
    passed = go_count * 2 > size and avg >= PASS_AVG_SCORE
    return {"verdict": "pass" if passed else "fail",
            "go_count": go_count, "avg_score": round(avg, 1), "size": size}


async def run_panel(summary: dict[str, Any], size: int = 7) -> dict[str, Any]:
    """并行执行 N 个独立评审并聚合"""
    size = max(1, min(size, len(PERSONAS)))
    votes = await asyncio.gather(*[
        asyncio.to_thread(_single_review, p, summary) for p in PERSONAS[:size]
    ])
    result = aggregate_votes(list(votes))
    result["votes"] = list(votes)
    logger.info(f"[Panel] 评审完成: {result['verdict']} "
                f"(go {result['go_count']}/{size}, 均分 {result['avg_score']})")
    return result
