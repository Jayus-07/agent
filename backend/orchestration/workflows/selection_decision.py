"""workflows/selection_decision.py — 选品决策 Workflow（批次3 v2：评估/决策职责分离）

架构（v2 计划书批次3）：
- Layer 0: competitor_data（watchlist 快照）
- Layer 1（并行）: market_evidence_assess / competitor_profile / review_pain
- Layer 2: selection_decision_gate（市场门控；insufficient 硬禁 go）
- Layer 3: differentiation（run_if 门控 go）→ finance_model（run_if 差异化 go）
- Layer 4: review_panel（run_if 财务达标）
- Layer 5: decision_report（恒定执行，组装决策包 + decision_log 留痕）

职责分离（评审定稿）：market_evidence_assess 只判"证据够不够格"
（sufficient/partial/insufficient），selection_decision_gate 才做决策——
证据不足时禁止输出"推荐"，只能产出"证据不足，无法决策"。

Phase 1 限制（报告中如实标注）：
- 无新数据源：证据评估用代理指标；痛点为 LLM 推断而非评论实证
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

# ── 门控阈值常量（市场门控代理指标）──────────────
MIN_CANDIDATES = 3       # 证据/门控：候选竞品数下限
MIN_TOTAL_REVIEWS = 100  # 证据/门控：评价总量下限

# 候选字段 → 决策工作流候选格式（漏斗 Top-N 衔接与 watchlist 共用）
_CANDIDATE_FIELDS = ("url", "title", "platform", "price", "rating",
                     "review_count", "highlights")


def candidates_from_funnel(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """漏斗 Top-N → 决策候选（纯函数；P2 选项 A：漏斗产候选、决策做单品裁决）。

    输入为 funnel_context.top（漏斗域图轻量摘要，含 rating/review_count/highlights）。
    规矩：只归一化不补造——缺字段保留 None，证据评估如实降级（partial/insufficient）；
    空/缺输入返回 []，调用方回落 watchlist，不改变原入口行为。
    """
    raw = (inputs or {}).get("funnel_candidates") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not (item.get("title") or item.get("url")):
            continue
        cand = {k: item.get(k) for k in _CANDIDATE_FIELDS}
        cand["title"] = cand["title"] or cand["url"]
        out.append(cand)
    return out

# ── run_if 谓词（Decision 分支，spec §4.3）──────────────


def _market_go(out: dict[str, Any]) -> bool:
    return ((out.get("selection_decision_gate") or {}).get("verdict")) == "go"


def _diff_go(out: dict[str, Any]) -> bool:
    return (out.get("differentiation") or {}).get("verdict") == "go"


def _finance_pass(out: dict[str, Any]) -> bool:
    return (out.get("finance_model") or {}).get("verdict") == "pass"


# ── 证据资格与市场门控（批次3：评估职责 ≠ 决策职责）──────────


def _evidence_verdict(candidate_count: int, total_reviews: int,
                      has_prices: bool) -> str:
    """市场证据资格判定（纯函数）：sufficient | partial | insufficient。

    只回答"证据够不够格"——数据量与维度覆盖，不含任何决策倾向。
    """
    if candidate_count < 2 or total_reviews <= 0 or not has_prices:
        return "insufficient"
    if candidate_count < MIN_CANDIDATES or total_reviews < MIN_TOTAL_REVIEWS:
        return "partial"
    return "sufficient"


def evaluate_gate(evidence_verdict: str, metrics: dict[str, Any],
                  data_gaps: list[str]) -> dict[str, Any]:
    """市场决策门控（纯函数）：证据资格 + 代理指标 → go/no_go + 推荐上限。

    硬规则（计划书批次3）：evidence_verdict == insufficient 时禁止 go，
    只能产出"证据不足，无法决策"——防止凭一份内容完整但证据不足的报告
    直接给出"推荐进入"。partial 证据下推荐上限为"谨慎"（cautious）。
    """
    proxy_go = (metrics.get("candidate_count", 0) >= MIN_CANDIDATES
                and metrics.get("total_reviews", 0) >= MIN_TOTAL_REVIEWS)
    if evidence_verdict == "insufficient":
        return {"verdict": "no_go", "evidence_verdict": evidence_verdict,
                "blocked_by_evidence": True, "recommendation_cap": "reject",
                "metrics": metrics, "data_gaps": data_gaps,
                "reason": "证据不足，无法决策（候选/评价/价格维度缺失）"}
    cap = None if evidence_verdict == "sufficient" else "cautious"
    return {"verdict": "go" if proxy_go else "no_go",
            "evidence_verdict": evidence_verdict,
            "blocked_by_evidence": False,
            "recommendation_cap": cap,
            "metrics": metrics, "data_gaps": data_gaps,
            "reason": "" if proxy_go else "代理指标未达门控阈值"}


def _recommendation_of(verdict: str, recommendation_cap: str | None) -> str:
    """最终推荐档位：recommend | cautious | reject（decision_log 用）。"""
    if verdict != "go":
        return "reject"
    return "recommend" if recommendation_cap in (None, "recommend") else "cautious"


def _llm_json(messages) -> Any:
    resp = llm.invoke(messages)
    return json.loads(resp.content.strip().strip("`").removeprefix("json").strip())


@workflow(
    name="selection_decision",
    description="选品决策 Go/No-Go — 市场评估/差异化/财务测算/AI评审团五层流水线",
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
        # P2 选项 A：漏斗 Top-N 优先（漏斗产候选、决策做单品裁决）；
        # 无漏斗输入或为空 → 回落 watchlist，原入口行为不变。
        funnel_cands = candidates_from_funnel(ctx.inputs)
        if funnel_cands:
            return {"candidates": funnel_cands, "count": len(funnel_cands),
                    "source": "funnel_topn",
                    "note": f"候选来自选品漏斗 Top-{len(funnel_cands)}（承接漏斗推荐单）；"
                            "缺字段如实降级进证据评估，不补造"}
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
        return {"candidates": candidates, "count": len(candidates),
                "source": "watchlist"}

    # ── Layer 1 分析层（并行）──────────────────
    @step(depends_on=["competitor_data"], name="市场证据评估", timeout_sec=60)
    async def market_evidence_assess(self, ctx):
        """证据资格评估（只判数据够不够格，不做决策；免费数据源代理指标）"""
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
        verdict = _evidence_verdict(len(cands), total_reviews, bool(prices))
        data_gaps = [
            "市场体量/增长率/季节性无免费数据源，以候选数与评价量作代理指标",
            "搜索趋势/供需比缺失（Phase 2 接入下拉词采集）",
        ]
        if verdict != "sufficient":
            data_gaps.append("证据未达充分标准：结构化维度覆盖不足，结论按推断级处理")
        return {"evidence_verdict": verdict, "metrics": metrics,
                "data_gaps": data_gaps}

    # ── Layer 2 市场门控（评估与决策分离）──────────
    @step(depends_on=["market_evidence_assess"], name="市场门控", timeout_sec=30)
    async def selection_decision_gate(self, ctx):
        """决策门控：证据资格 + 代理指标 → go/no_go + 推荐上限。

        insufficient 硬禁 go（run_if 谓词层强制，见 _market_go）。
        """
        ev = ctx.outputs["market_evidence_assess"]
        return evaluate_gate(ev["evidence_verdict"], ev["metrics"], ev["data_gaps"])

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
            from backend.prompts.service import prompt_service
            data = _llm_json([
                SystemMessage(content=prompt_service.get_template_sync("selection_decision.review_pain")),
                HumanMessage(content=material),
            ])
            pains = [str(x) for x in data][:5]
        except Exception as e:
            logger.warning(f"[SelectionDecision] 痛点推断失败，降级为空痛点: {e}")
            return fallback
        return {"pain_points": pains, "source": "inferred",
                "note": "非评论实证，基于卖点/评分的 LLM 推断（Phase 1 降级）"}

    # ── Layer 3 决策层 ──────────────────────────
    @step(depends_on=["selection_decision_gate", "competitor_profile", "review_pain"],
          name="差异化分析", timeout_sec=180, run_if=_market_go)
    async def differentiation(self, ctx):
        """Decision1：是否存在差异化切入点（LLM 推理 + 保守兜底）"""
        from langchain_core.messages import HumanMessage, SystemMessage
        gate = ctx.outputs["selection_decision_gate"]
        material = {
            "market": gate["metrics"],
            "evidence_verdict": gate.get("evidence_verdict"),
            "profiles": ctx.outputs["competitor_profile"]["profiles"],
            "pain_points": (ctx.outputs.get("review_pain") or {}).get("pain_points", []),
        }
        conservative = {"verdict": "no_go", "gaps": [], "heatmap": [],
                        "reason": "差异化分析不可用（LLM 失败），保守拒绝"}
        try:
            from backend.prompts.service import prompt_service
            data = _llm_json([
                SystemMessage(content=prompt_service.get_template_sync("selection_decision.differentiation")),
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

    # ── Layer 4 验证层 ──────────────────────────
    @step(depends_on=["finance_model"], name="AI评审团",
          timeout_sec=300, run_if=_finance_pass)
    async def review_panel(self, ctx):
        """Decision3：N 角色独立投票（人数来自任务参数）"""
        summary = {
            "category": ctx.inputs.get("category"),
            "platforms": ctx.inputs.get("platforms"),
            "market": ctx.outputs["selection_decision_gate"]["metrics"],
            "evidence_verdict": ctx.outputs["selection_decision_gate"].get("evidence_verdict"),
            "differentiation": ctx.outputs["differentiation"],
            "finance": ctx.outputs["finance_model"]["final_model"],
        }
        return await run_panel(summary, size=int(ctx.inputs.get("panel_size", 7)))

    # ── Layer 5 产出 ────────────────────────────
    @step(depends_on=["selection_decision_gate", "differentiation",
                       "finance_model", "review_panel"],
          name="决策报告", timeout_sec=60)
    async def decision_report(self, ctx):
        outputs = ctx.outputs
        gate = outputs.get("selection_decision_gate") or {}
        checks = {
            "market": gate.get("verdict") == "go",
            "differentiation": (outputs.get("differentiation") or {}).get("verdict") == "go",
            "finance": (outputs.get("finance_model") or {}).get("verdict") == "pass",
            "panel": (outputs.get("review_panel") or {}).get("verdict") == "pass",
        }
        failed = [k for k, ok in checks.items() if not ok]
        verdict = "go" if not failed else "no_go"
        recommendation = _recommendation_of(verdict, gate.get("recommendation_cap"))
        report_md = build_report(ctx.inputs, outputs, verdict=verdict, failed_gates=failed)
        task_id = ctx.inputs.get("task_id")
        decision_id = None
        if task_id:
            sd_store = get_selection_decision_store()
            # 直跑/测试场景无 API 预建行：用公共接口补建后回写
            sd_store.ensure_task(task_id, {"category": ctx.inputs.get("category")})
            sd_store.update_result(
                task_id, status="success", verdict=verdict,
                report_md=report_md, trace_id=ctx.trace_id or "")
            # decision_log 留痕（批次3）：快照不可变；失败不阻塞报告产出
            try:
                decision_id = sd_store.record_decision(
                    task_id=task_id,
                    candidate_id=f"category:{ctx.inputs.get('category') or 'unknown'}",
                    category=ctx.inputs.get("category"),
                    evidence_snapshot={
                        "evidence_verdict": gate.get("evidence_verdict"),
                        "metrics": gate.get("metrics") or {},
                        "data_gaps": gate.get("data_gaps") or [],
                    },
                    score_snapshot={
                        "differentiation": (outputs.get("differentiation") or {}).get("verdict"),
                        "finance": (outputs.get("finance_model") or {}).get("final_model") or {},
                        "panel": (outputs.get("review_panel") or {}).get("verdict"),
                        "recommendation_cap": gate.get("recommendation_cap"),
                        "monitor_keywords": (outputs.get("review_pain") or {}).get("pain_points", []),
                    },
                    recommendation=recommendation,
                )
            except Exception as e:  # noqa: BLE001 — 留痕失败仅告警
                logger.warning(f"[SelectionDecision] decision_log 留痕失败: {e}")
        return {"verdict": verdict, "recommendation": recommendation,
                "evidence_verdict": gate.get("evidence_verdict"),
                "decision_id": decision_id,
                "failed_gates": failed, "report_md": report_md}


__all__ = ["SelectionDecision"]
