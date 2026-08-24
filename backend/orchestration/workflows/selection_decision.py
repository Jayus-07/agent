"""workflows/selection_decision.py — 选品决策 Workflow（Phase 1 决策闭环 MVP）

架构（spec §4.2）：
- Layer 0: competitor_data（watchlist 快照）
- Layer 1（并行）: market_assess / competitor_profile / review_pain
- Layer 2: differentiation（run_if 市场 go）→ finance_model（run_if 差异化 go，内部≤3轮循环）
- Layer 3: review_panel（run_if 财务达标）
- Layer 4: decision_report（恒定执行，组装 Go/No-Go 决策包）

Phase 1 限制（报告中如实标注）：
- 无新数据源：市场评估用代理指标；痛点为 LLM 推断而非评论实证
"""
from __future__ import annotations

import json
from statistics import median
from typing import Any

from backend.competitor.store import get_store
from backend.infra.llm import llm
from backend.orchestration.workflow import workflow, step
from backend.selection.recommender import batch_scores
from backend.selection_decision.finance import run_finance
from backend.selection_decision.panel import run_panel
from backend.selection_decision.report import build_report
from backend.selection_decision.store import get_selection_decision_store
from backend.shared.logger import logger

# ── 门控阈值常量（market_assess 规则门控）──────────────
MIN_CANDIDATES = 3       # 市场门控：候选竞品数下限
MIN_TOTAL_REVIEWS = 100  # 市场门控：评价总量下限

# ── run_if 谓词（Decision 分支，spec §4.3）──────────────


def _market_go(out: dict[str, Any]) -> bool:
    return (out.get("market_assess") or {}).get("verdict") == "go"


def _diff_go(out: dict[str, Any]) -> bool:
    return (out.get("differentiation") or {}).get("verdict") == "go"


def _finance_pass(out: dict[str, Any]) -> bool:
    return (out.get("finance_model") or {}).get("verdict") == "pass"


def _llm_json(messages) -> Any:
    resp = llm.invoke(messages)
    return json.loads(resp.content.strip().strip("`").removeprefix("json").strip())


@workflow(
    name="selection_decision",
    description="选品决策 Go/No-Go — 市场评估/差异化/财务测算/AI评审团四层流水线",
    objects=["选品", "决策", "入场", "品类"],
    actions=["评估", "分析", "决策"],
    examples=["评估蓝牙耳机品类值不值得做", "帮我做一次选品决策"],
    category="selection",
)
class SelectionDecision:
    """选品决策 Workflow — 8 个 step，5 层 DAG

    失败语义：competitor_data 无候选、finance 参数非法等输入性错误走 abort（fail-fast，
    不产出 No-Go 报告）；只有上游 gate 判定 no_go/fail 才走 run_if 短路并产出 No-Go 报告。
    """

    # ── Layer 0 感知层 ──────────────────────────
    @step(name="竞品数据采集", timeout_sec=120)
    async def competitor_data(self, ctx):
        store = get_store()
        candidates = []
        for item in store.list_watch(enabled_only=True):
            snap = store.latest_snapshot(item["url"])
            if snap and (snap.get("price") is not None or snap.get("title")):
                candidates.append({
                    "url": item["url"], "title": snap.get("title") or item["url"],
                    "platform": snap.get("platform") or "generic",
                    "price": snap.get("price"), "rating": snap.get("rating"),
                    "review_count": snap.get("review_count"),
                    "highlights": snap.get("highlights") or "",
                })
        if not candidates:
            # 输入性错误 → 默认 on_error="abort" fail-fast（不产出 No-Go 报告，
            # 用户应先修复 watchlist；Task 7 API 层会预校验）
            raise ValueError("watchlist 为空或无快照，请先在竞品监控添加商品 URL")
        return {"candidates": candidates, "count": len(candidates)}

    # ── Layer 1 分析层（并行）──────────────────
    @step(depends_on=["competitor_data"], name="市场评估(Q1)", timeout_sec=60)
    async def market_assess(self, ctx):
        """代理指标评估（免费数据源限制，spec R2：如实标注缺口）"""
        cands = ctx.outputs["competitor_data"]["candidates"]
        prices = [c["price"] for c in cands if c.get("price") is not None]
        reviews = sorted([c["review_count"] for c in cands if c.get("review_count")],
                         reverse=True)
        total_reviews = sum(reviews)
        top3_share = round(sum(reviews[:3]) / total_reviews, 3) if total_reviews else 0
        metrics = {
            "candidate_count": len(cands),
            "price_min": min(prices) if prices else None,
            "price_max": max(prices) if prices else None,
            "price_median": round(median(prices), 2) if prices else None,
            "total_reviews": total_reviews,
            "top3_review_share": top3_share,
        }
        # 规则门控：候选≥MIN_CANDIDATES 且评价总量≥MIN_TOTAL_REVIEWS → 视为存在需求（代理判断）
        verdict = "go" if (len(cands) >= MIN_CANDIDATES
                           and total_reviews >= MIN_TOTAL_REVIEWS) else "no_go"
        return {
            "verdict": verdict,
            "metrics": metrics,
            "data_gaps": [
                "市场体量/增长率/季节性无免费数据源，以候选数与评价量作代理指标",
                "搜索趋势/供需比缺失（Phase 2 接入下拉词采集）",
            ],
        }

    @step(depends_on=["competitor_data"], name="竞品画像", timeout_sec=60)
    async def competitor_profile(self, ctx):
        cands = ctx.outputs["competitor_data"]["candidates"]
        urls = [c["url"] for c in cands]
        scores = batch_scores(urls).get("scores", {})
        profiles = []
        for c in cands:
            breakdown = (scores.get(c["url"]) or {}).get("breakdown") or {}
            profiles.append({**c, "radar": breakdown})
        return {"profiles": profiles}

    @step(depends_on=["competitor_data"], name="痛点推断",
          timeout_sec=180)
    async def review_pain(self, ctx):
        """Phase 1 降级：无评论数据，LLM 基于卖点/评分推断痛点（spec R3 ②）

        内部 catch-all fallback 已吞掉一切异常并返回降级结果，无需 on_error="skip"。
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        cands = ctx.outputs["competitor_data"]["candidates"]
        material = "\n".join(
            f"- {c['title']}（评分{c.get('rating')}）卖点: {c['highlights']}"
            for c in cands)
        fallback = {"pain_points": [], "source": "none",
                    "note": "痛点推断失败，差异化分析将仅基于结构化数据"}
        try:
            data = _llm_json([
                SystemMessage(content=(
                    "你是电商用户研究员。基于给定商品的标题/卖点/评分，推断该品类"
                    "用户最可能的痛点。只回复 JSON 数组（字符串列表，最多5项）。")),
                HumanMessage(content=material),
            ])
            pains = [str(x) for x in data][:5]
        except Exception as e:
            logger.warning(f"[SelectionDecision] 痛点推断失败，降级为空痛点: {e}")
            return fallback
        return {"pain_points": pains, "source": "inferred",
                "note": "非评论实证，基于卖点/评分的 LLM 推断（Phase 1 降级）"}

    # ── Layer 2 决策层 ──────────────────────────
    @step(depends_on=["market_assess", "competitor_profile", "review_pain"],
          name="差异化分析", timeout_sec=180, run_if=_market_go)
    async def differentiation(self, ctx):
        """Decision1：是否存在差异化切入点（LLM 推理 + 保守兜底）"""
        from langchain_core.messages import HumanMessage, SystemMessage
        material = {
            "market": ctx.outputs["market_assess"]["metrics"],
            "profiles": ctx.outputs["competitor_profile"]["profiles"],
            "pain_points": (ctx.outputs.get("review_pain") or {}).get("pain_points", []),
        }
        conservative = {"verdict": "no_go", "gaps": [], "heatmap": [],
                        "reason": "差异化分析不可用（LLM 失败），保守拒绝"}
        try:
            data = _llm_json([
                SystemMessage(content=(
                    "你是选品差异化分析师。基于市场指标、竞品画像与痛点列表，判断是否"
                    "存在差异化切入点。只回复 JSON："
                    '{"verdict": "go"或"no_go", "gaps": ["未被满足的需求"], '
                    '"heatmap": [{"pain": "痛点", "severity": 1到5}], "reason": "50字以内"}')),
                HumanMessage(content=json.dumps(material, ensure_ascii=False, default=str)),
            ])
            if data.get("verdict") not in ("go", "no_go"):
                raise ValueError(f"非法 verdict: {data.get('verdict')}")
            return {"verdict": data["verdict"],
                    "gaps": [str(g) for g in data.get("gaps", [])],
                    "heatmap": data.get("heatmap", []),
                    "reason": str(data.get("reason", ""))[:200]}
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning(f"[SelectionDecision] 差异化分析失败，保守拒绝: {e}")
            return conservative
        except Exception as e:
            logger.warning(f"[SelectionDecision] 差异化分析调用失败: {e}")
            return conservative

    @step(depends_on=["differentiation"], name="财务测算",
          timeout_sec=60, run_if=_diff_go)
    async def finance_model(self, ctx):
        """Decision2：规则测算 + 内部有界优化循环（≤3 轮）

        参数非法时 run_finance 抛 ValueError → 默认 on_error="abort" fail-fast
        （输入性错误 ≠ 决策 No-Go；Task 7 API 层会预校验参数）。
        """
        params = ctx.inputs.get("finance") or {}
        return run_finance(params)

    # ── Layer 3 验证层 ──────────────────────────
    @step(depends_on=["finance_model"], name="AI评审团",
          timeout_sec=300, run_if=_finance_pass)
    async def review_panel(self, ctx):
        """Decision3：N 角色独立投票（人数来自任务参数）"""
        summary = {
            "category": ctx.inputs.get("category"),
            "platforms": ctx.inputs.get("platforms"),
            "market": ctx.outputs["market_assess"]["metrics"],
            "differentiation": ctx.outputs["differentiation"],
            "finance": ctx.outputs["finance_model"]["final_model"],
        }
        return await run_panel(summary, size=int(ctx.inputs.get("panel_size", 7)))

    # ── Layer 4 产出 ────────────────────────────
    @step(depends_on=["market_assess", "differentiation",
                       "finance_model", "review_panel"],
          name="决策报告", timeout_sec=60)
    async def decision_report(self, ctx):
        outputs = ctx.outputs
        checks = {
            "market": (outputs.get("market_assess") or {}).get("verdict") == "go",
            "differentiation": (outputs.get("differentiation") or {}).get("verdict") == "go",
            "finance": (outputs.get("finance_model") or {}).get("verdict") == "pass",
            "panel": (outputs.get("review_panel") or {}).get("verdict") == "pass",
        }
        failed = [k for k, ok in checks.items() if not ok]
        verdict = "go" if not failed else "no_go"
        report_md = build_report(ctx.inputs, outputs, verdict=verdict, failed_gates=failed)
        task_id = ctx.inputs.get("task_id")
        if task_id:
            sd_store = get_selection_decision_store()
            # 直跑/测试场景无 API 预建行：用公共接口补建后回写
            sd_store.ensure_task(task_id, {"category": ctx.inputs.get("category")})
            sd_store.update_result(
                task_id, status="success", verdict=verdict,
                report_md=report_md, trace_id=ctx.trace_id or "")
        return {"verdict": verdict, "failed_gates": failed, "report_md": report_md}


__all__ = ["SelectionDecision"]
