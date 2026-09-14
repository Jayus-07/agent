"""workflows/market_research.py — 品类市场调研 Workflow（Skill 集成计划批次 2）

五段式证据管线（不是逐章四层 DAG，12 章节不每章独立调 LLM）：
- Layer 0: collect      采集公开数据（web_search + web_crawl，每条落 evidence）
- Layer 1: normalize    清洗/去重/来源分级/抽数字 → 标准化 evidence 表
- Layer 2（并行）: group_a/b/c  按证据密度分组的章节分析（LLM 只允许引用 evidence_id）
- Layer 3: fact_lock    统一事实锁定 + 引用校验（数字 ⊆ 证据，违规降级为推断）
- Layer 4: report       12 章节 Markdown 报告 + Source Index（进入建议 = 唯一汇合点）

失败语义：
- 采集失败语义：有效证据 < MIN_EVIDENCE → abort（fail-fast 不产残缺报告）；
  仅个别来源失败 → 降级继续，报告标注覆盖缺口
- LLM 失败语义：组任务重试 2 次（指数退避）→ 仍失败降级为"模板骨架 + 原始证据列表"，
  不中止整体报告；只有采集阶段失败才 abort
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from backend.infra.llm import llm
from backend.market_research.pipeline import (
    build_report_md,
    group_digest,
    lock_facts,
    normalize_evidence,
    parse_search_results,
    route_group,
)
from backend.market_research.store import get_market_research_store
from backend.orchestration.workflow import step, workflow
from backend.shared.logger import logger
from backend.tools.web import web_crawl_tool, web_search_tool

# ── 门控阈值与采集参数 ──────────────────────────
MIN_EVIDENCE = 5            # 有效证据下限，不足 abort
SEARCH_ASPECTS = (          # 采集维度 → 搜索词
    "市场规模 行业报告",
    "竞争格局 头部品牌",
    "用户需求 痛点 评价",
    "价格 价格带",
    "趋势 机会 风险",
)
SEARCH_RESULTS_PER_QUERY = 4
MAX_CRAWL = 6               # 抓取上限（控制耗时与封禁风险）
RAW_TEXT_LIMIT = 2000       # 证据原文摘录上限

# ── 分组章任务（12 章节模板：三组各 4 节 + 执行摘要/进入建议在 report 组装）──
GROUPS: dict[str, dict[str, Any]] = {
    "group_a": {
        "label": "市场规模与竞争格局",
        "sections": ["行业概览与市场定义", "市场规模与增速",
                     "竞争格局与头部玩家", "市场集中度与新进入者"],
    },
    "group_b": {
        "label": "用户需求与商业模式",
        "sections": ["用户画像与核心需求", "购买决策与消费行为",
                     "价格带分析", "商业模式与盈利路径"],
    },
    "group_c": {
        "label": "渠道供应链与趋势风险",
        "sections": ["渠道结构与流量分布", "供应链与成本结构",
                     "趋势与机会", "风险与进入壁垒"],
    },
}


def _llm_json(messages) -> Any:
    resp = llm.invoke(messages)
    return json.loads(resp.content.strip().strip("`").removeprefix("json").strip())


@workflow(
    name="market_research",
    description="品类市场调研 — 采集/证据标准化/分组分析/事实锁定/12章节报告五段式证据管线",
    objects=["品类", "市场", "调研", "报告"],
    actions=["调研", "分析", "报告"],
    examples=["调研一下蓝牙耳机市场", "帮我做宠物零食的品类市场调研报告", "分析这个品类的市场情况"],
    category="selection",
)
class MarketResearch:
    """品类市场调研 Workflow — 7 个 step，5 层 DAG。"""

    # ── 第 1 段：采集公开数据 ────────────────────
    @step(name="公开数据采集", timeout_sec=600)
    async def collect(self, ctx):
        category = str(ctx.inputs.get("category") or "").strip()
        if not category:
            raise ValueError("缺少 category 输入（要调研的品类名称）")
        queries = [f"{category} {aspect}" for aspect in SEARCH_ASPECTS]

        # 逐查询搜索：个别查询失败不中止（降级继续），全失败则无证据走 abort
        found: list[dict[str, Any]] = []
        search_failures = 0
        for q in queries:
            try:
                md = web_search_tool.invoke({"query": q,
                                             "num_results": SEARCH_RESULTS_PER_QUERY})
                for r in parse_search_results(md):
                    r["search_query"] = q
                    found.append(r)
            except Exception as e:
                search_failures += 1
                logger.warning(f"[MarketResearch] 搜索失败 {q!r}: {e}")
        if not found:
            raise ValueError(
                f"公开数据采集失败：{len(queries)} 个搜索查询均无结果（网络或搜索源不可用）")

        # 逐 URL 抓取：个别失败 → 降级继续 + 覆盖缺口
        seen_urls: set[str] = set()
        raw: list[dict[str, Any]] = []
        crawl_failures = 0
        for r in found:
            if r["url"] in seen_urls:
                continue
            seen_urls.add(r["url"])
            if len(raw) >= MAX_CRAWL:
                break
            try:
                content = web_crawl_tool.invoke({"url": r["url"]})
                raw.append({**r, "content": str(content)[:RAW_TEXT_LIMIT]})
            except Exception as e:
                crawl_failures += 1
                logger.warning(f"[MarketResearch] 抓取失败 {r['url']}: {e}")

        coverage_gaps = []
        if search_failures:
            coverage_gaps.append(f"{search_failures} 个搜索查询失败")
        if crawl_failures:
            coverage_gaps.append(f"{crawl_failures} 个来源抓取失败")
        return {"category": category, "raw": raw,
                "search_failures": search_failures, "crawl_failures": crawl_failures,
                "coverage_gaps": coverage_gaps}

    # ── 第 2 段：清洗、去重、证据标准化 ───────────
    @step(depends_on=["collect"], name="证据清洗标准化", timeout_sec=60)
    async def normalize(self, ctx):
        coll = ctx.outputs["collect"]
        fetched_at = datetime.now().isoformat(timespec="seconds")
        evidence = normalize_evidence(coll["raw"], fetched_at)
        if len(evidence) < MIN_EVIDENCE:
            # fail-fast：不产残缺报告（计划书批次2 采集失败语义）
            raise ValueError(
                f"有效证据 {len(evidence)} 条 < 最低阈值 {MIN_EVIDENCE} 条，"
                f"中止调研（覆盖缺口: {coll['coverage_gaps'] or '证据密度不足'}）")
        task_id = ctx.inputs.get("task_id")
        store = get_market_research_store()
        if task_id:
            store.ensure_task(task_id, {"category": coll["category"]})
            store.add_evidence(str(task_id), evidence)
        groups: dict[str, list[str]] = {g: [] for g in GROUPS}
        for e in evidence:
            groups[route_group(e)].append(e["evidence_id"])
        return {"evidence": evidence, "groups": groups,
                "coverage_gaps": coll["coverage_gaps"]}

    # ── 第 3 段：分组章任务（并行，LLM 只许引用 evidence_id）──
    @staticmethod
    def _make_group_step(group_key: str):
        """工厂：为 group_a/b/c 生成同构的 step 函数（签名 (self, ctx)）。"""
        spec = GROUPS[group_key]

        async def _run(self, ctx):
            from langchain_core.messages import HumanMessage, SystemMessage

            from backend.prompts.service import prompt_service
            norm = ctx.outputs["normalize"]
            ids = norm["groups"].get(group_key) or []
            # 空组也要产出骨架，避免 fact_lock/report 缺 key
            digest = group_digest(norm["evidence"], ids)
            material = json.dumps({
                "category": ctx.outputs["collect"]["category"],
                "group": spec["label"],
                "sections": spec["sections"],
                "evidence": digest,
            }, ensure_ascii=False)
            system = prompt_service.get_template_sync("market_research.analyzer")
            last_err: Exception | None = None
            for attempt in range(3):  # 重试 2 次（指数退避）
                try:
                    data = _llm_json([
                        SystemMessage(content=system),
                        HumanMessage(content=material),
                    ])
                    sections = self._validate_sections(data, spec["sections"])
                    return {"sections": sections, "degraded": False}
                except Exception as e:  # noqa: BLE001 — LLM/JSON/网络异常统一重试
                    last_err = e
                    logger.warning(f"[MarketResearch] {group_key} 分析第 {attempt + 1} 次失败: {e}")
                    await asyncio.sleep(0.5 * (2 ** attempt))
            # 降级：模板骨架 + 原始证据列表，不中止整体报告
            logger.warning(f"[MarketResearch] {group_key} LLM 分析失败，降级骨架: {last_err}")
            skeleton = []
            for title in spec["sections"]:
                ev_lines = "\n".join(
                    f"  - `{e['evidence_id']}` [{e['source_type']}] {e['title']}"
                    for e in group_digest(norm["evidence"], ids, excerpt=120))
                skeleton.append({
                    "title": title,
                    "content": f"（LLM 分析失败，本节为模板骨架）\n\n相关证据：\n{ev_lines or '  - （无分组证据）'}",
                    "claims": [],
                })
            return {"sections": skeleton, "degraded": True}

        return _run

    @staticmethod
    def _validate_sections(data: Any, expected: list[str]) -> list[dict[str, Any]]:
        """校验 LLM 输出结构：sections 列表，每节 title/content/claims。"""
        if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
            raise ValueError("LLM 输出缺少 sections 列表")
        sections = []
        for i, sec in enumerate(data["sections"]):
            if not isinstance(sec, dict) or not str(sec.get("content", "")).strip():
                continue
            claims = []
            for c in sec.get("claims", []) or []:
                if not isinstance(c, dict) or not str(c.get("claim", "")).strip():
                    continue
                claims.append({
                    "claim": str(c["claim"])[:200],
                    "type": "fact" if c.get("type") == "fact" else "inference",
                    "evidence_ids": [str(x) for x in c.get("evidence_ids", [])][:10],
                    "numbers": [str(x) for x in c.get("numbers", [])][:20],
                })
            sections.append({
                "title": str(sec.get("title") or
                             (expected[i] if i < len(expected) else "章节")).strip()[:60],
                "content": str(sec["content"]),
                "claims": claims,
            })
        if not sections:
            raise ValueError("LLM 输出无有效章节")
        return sections

    group_a = step(depends_on=["normalize"], name="组A·市场规模与竞争",
                   timeout_sec=300)(_make_group_step("group_a"))
    group_b = step(depends_on=["normalize"], name="组B·需求与商业模式",
                   timeout_sec=300)(_make_group_step("group_b"))
    group_c = step(depends_on=["normalize"], name="组C·渠道供应链与趋势",
                   timeout_sec=300)(_make_group_step("group_c"))

    # ── 第 4 段：统一事实锁定 + 引用校验 ──────────
    @step(depends_on=["group_a", "group_b", "group_c"],
          name="事实锁定与引用校验", timeout_sec=60)
    async def fact_lock(self, ctx):
        evidence_index = {e["evidence_id"]: e for e in ctx.outputs["normalize"]["evidence"]}
        groups_out = {k: ctx.outputs[k] for k in GROUPS}
        locked, downgrades = lock_facts(groups_out, evidence_index)
        degraded = [k for k in GROUPS if (ctx.outputs[k] or {}).get("degraded")]
        return {"groups": locked, "downgrades": downgrades, "degraded_groups": degraded}

    # ── 第 5 段：报告组装（进入建议 = 唯一汇合点）──
    @step(depends_on=["fact_lock"], name="调研报告组装", timeout_sec=60)
    async def report(self, ctx):
        norm = ctx.outputs["normalize"]
        locked = ctx.outputs["fact_lock"]
        report_md = build_report_md(
            category=ctx.outputs["collect"]["category"],
            locked_groups=locked["groups"],
            evidence=norm["evidence"],
            downgrades=locked["downgrades"],
            coverage_gaps=norm["coverage_gaps"],
            degraded_groups=locked["degraded_groups"],
        )
        task_id = ctx.inputs.get("task_id")
        if task_id:
            store = get_market_research_store()
            store.ensure_task(str(task_id), {"category": ctx.outputs["collect"]["category"]})
            store.update_result(str(task_id), status="success", report_md=report_md,
                                trace_id=ctx.trace_id or "")
        n_claims = sum(len(s.get("claims", []))
                       for g in locked["groups"].values()
                       for s in g.get("sections", []))
        return {"report_md": report_md, "evidence_count": len(norm["evidence"]),
                "claim_count": n_claims, "downgrade_count": len(locked["downgrades"])}


__all__ = ["MarketResearch", "GROUPS", "MIN_EVIDENCE"]
