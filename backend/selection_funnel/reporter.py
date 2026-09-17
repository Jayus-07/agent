"""selection_funnel/reporter.py — 漏斗报告渲染

纪律：渲染 + 如实披露缺口，不补造事实。
每一层淘汰了谁、为什么淘汰，全部落进报告 —— 可解释性是选品建议的命根子。
"""
from __future__ import annotations

from backend.selection_funnel.graph_state import STATUS_EMPTY, STATUS_NEED_INFO
from backend.shared.logger import logger

_STAGE_LABELS = {
    "brief": "需求解析", "pool": "建池", "screen": "指标初筛",
    "verify": "竞品验证", "econ": "利润测算", "rank": "评分排序",
}


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _stage_table(stage_logs: list[dict]) -> list[str]:
    lines = ["| 层 | 保留 | 淘汰 |", "|---|---|---|"]
    for log in stage_logs:
        lines.append(f"| {_STAGE_LABELS.get(log.get('stage'), log.get('stage', ''))} "
                     f"| {log.get('kept', 0)} | {log.get('dropped', 0)} |")
    return lines


def _drop_details(stage_logs: list[dict], limit: int = 20) -> list[str]:
    lines: list[str] = []
    for log in stage_logs:
        reasons = log.get("reasons") or []
        if not reasons:
            continue
        label = _STAGE_LABELS.get(log.get("stage"), log.get("stage", ""))
        lines.append(f"- **{label}**：")
        for r in reasons[:limit]:
            lines.append(f"  - {r.get('title') or r.get('url', '')} "
                         f"[{r.get('rule', '')}] {_fmt(r.get('value'))}")
        if len(reasons) > limit:
            lines.append(f"  - （其余 {len(reasons) - limit} 条略）")
    return lines


def _recommend_table(candidates: list[dict]) -> list[str]:
    lines = ["| # | 商品 | 平台 | 售价 | 潜力分 | 贡献利润率 | 商品毛利率 | 理由 |",
             "|---|---|---|---|---|---|---|---|"]
    for c in candidates:
        econ = c.get("economics") or {}
        margin = econ.get("margin")
        gross = econ.get("gross_margin")
        lines.append(
            f"| {c.get('rank', '-')} | {(c.get('title') or c.get('url', ''))[:28]} "
            f"| {c.get('platform') or '-'} | {_fmt(c.get('price'))} "
            f"| {(c.get('score') or {}).get('total', '-')} "
            f"| {f'{margin:.1%}' if margin is not None else '-'} "
            f"| {f'{gross:.0%}' if gross is not None else '-'} "
            f"| {(c.get('reason') or '')[:120]} |")
    return lines


def _market_lines(category: str) -> list[str]:
    """赛道画像（关键词榜上传数据）；无数据/不可用 → 空（不渲染段）。"""
    try:
        from backend.selection_funnel.market_data import market_snapshot
        snap = market_snapshot(category)
    except Exception as e:
        logger.warning("[FunnelReport] 赛道画像不可用: %s", e)
        return []
    if not snap:
        return []
    lines = [f"- 关键词共 {snap['total']} 个，按搜索人气 Top 5："]
    for k in snap["top"][:5]:
        lines.append(f"  - {k['keyword']}：人气 {_fmt(k.get('search_pop'))}，"
                     f"竞争度 {_fmt(k.get('competition'))}")
    if snap.get("opportunities"):
        lines.append("- 机会词（搜索人气高、竞争度低，优先做标题覆盖）：")
        lines += [f"  - {k['keyword']}" for k in snap["opportunities"][:3]]
    return lines


# 痛点 → 对策映射（六桶；只给方向，不给臆造事实 —— 2026-09-17 六步法补缺）
PAIN_ACTIONS: dict[str, str] = {
    "质量做工": "要求供应商出质检报告并首批验货，详情页拍用料/做工细节图",
    "物流包装": "加硬纸盒或气泡柱加固包装，换时效更稳的快递，详情页明示发货时效",
    "尺寸规格": "详情页上实物对比图与克重标注，SKU 分大小档校准预期",
    "气味口感": "先索样试吃/试闻，优先无香精配方并在卖点中声明",
    "描述不符": "主图实拍不过度修图，加色差与规格声明压退货率",
    "售后服务": "建售后 SOP（退款时效承诺+标准话术），放售后卡引导直连客服",
}


def _pain_lines(candidates: list[dict], category: str) -> list[str]:
    """痛点机会（差评实证上传数据）；无数据/不可用 → 空。"""
    try:
        from backend.selection_funnel.market_data import pain_points_for
        rows = pain_points_for([(c.get("title") or "") for c in candidates], category)
    except Exception as e:
        logger.warning("[FunnelReport] 痛点分析不可用: %s", e)
        return []
    if not rows:
        return []
    lines = ["- 口径：评论按标题包含匹配；星级≤3 或无星级计差评；痛点为规则桶聚类，"
             "请人工抽评复核"]
    for r in rows:
        pains = "、".join(f"{p}({n})" for p, n in r["pains"]) or "（未聚类出高频桶）"
        lines.append(f"- 「{r['title']}」：差评 {r['negative']}/{r['review_total']} —— 痛点：{pains}")
        actions = [PAIN_ACTIONS[b] for b, _ in r["pains"] if b in PAIN_ACTIONS]
        if actions:
            lines.append(f"  - 对策：{'；'.join(actions[:2])}")
    return lines


def _competition_lines(pool: list[dict], candidates: list[dict]) -> list[str]:
    """竞争格局（商品榜结构化）；无池且无候选 → 空（不渲染段）。"""
    data = list(pool or [])
    fallback = False
    if not data:
        data = list(candidates or [])
        fallback = True
    if not data:
        return []
    try:
        from backend.selection_funnel.market_data import competition_structure
        comp = competition_structure(data)
    except Exception as e:
        logger.warning("[FunnelReport] 竞争格局不可用: %s", e)
        return []
    if not comp:
        return []
    lines = []
    if fallback:
        lines.append("- 口径：建池快照缺失，以存活候选近似全池（竞争格局看全池更准，"
                     "导池后重跑可得完整结构）")
    else:
        lines.append(f"- 口径：建池全量 {comp['total']} 款，评论数代销量")
    if comp.get("cr5") is not None:
        level = ("头部集中度高（CR5 ≥ 60%），新进者需差异化避开头部主力价格带"
                 if comp["cr5"] >= 0.6 else "头部集中度中等，格局未固化")
        lines.append(f"- CR5 = {comp['cr5']:.0%}：{level}")
    p25, p50, p75 = (comp.get("price_p25"), comp.get("price_p50"),
                     comp.get("price_p75"))
    if p25 is not None and p50 is not None and p75 is not None:
        lines.append(f"- 价格分位 P25/P50/P75 = {_fmt(p25)} / {_fmt(p50)} / {_fmt(p75)} 元"
                     "（贴 P25 定价是价格战，P50 以上需差异化支撑）")
    if comp.get("brand_like"):
        words = "、".join(f"{w}×{n}" for w, n in comp["brand_words_hit"].items())
        lines.append(f"- 疑似品牌款 {comp['brand_like']} 款（占 {comp['brand_ratio']:.0%}，"
                     f"命中词：{words}）")
        if comp["brand_ratio"] >= 0.5:
            lines.append("  - 品牌款占比过半：白牌新链建议避开品牌正面战场，"
                         "从规格/场景差异化切入")
    for a in comp.get("anomalies", []):
        lines.append(f"- ⚠「{a['title']}」评价比 {a['ratio']}：{a['flag']}")
    lines += [f"- {n}" for n in comp.get("notes", [])]
    return lines


def _test_plan_lines(candidates: list[dict], moq: float | None = None) -> list[str]:
    """测款计划卡（小额试错口径，2026-09-17 六步法补缺）。"""
    if not candidates:
        return []
    lines = ["- 口径：直通车/搜索推广 ¥300-500/天 × 14 天，先验证点击与加购，再谈放量"]
    for c in candidates[:2]:
        econ = c.get("economics") or {}
        price = econ.get("price") or c.get("price")
        unit_cost = econ.get("unit_cost")
        batch = round(moq * unit_cost, 2) if (moq and unit_cost) else None
        line = (f"- 「{(c.get('title') or '')[:24]}」（¥{_fmt(price)}）："
                "点击率 > 行业 1.2 倍且加购率 > 8% → 加预算；"
                "累计花费达 30% 预算仍无加购 → 停投止损；"
                "14 天 ROI < 1 → 复盘或放弃")
        if batch is not None:
            line += f"；按 MOQ {moq:g} 估首批投入约 ¥{_fmt(batch)}"
        lines.append(line)
    if moq is None:
        lines.append("- 首批投入未估：漏斗上下文未提供 MOQ（起订量），补齐后可算首批资金占用")
    return lines


def _decision_draft_lines(candidates: list[dict],
                          min_margin: float | None = None) -> list[str]:
    """决策草案（规则初判，供人工拍板）；依据全部来自漏斗已算数字。

    分层口径（P2：分层并入判据，不新增平行结构）——档位即本函数的
    做 / 条件做 / 放弃，依据 = 贡献利润率缓冲 + 成本口径 + 痛点 + 数据质量
    （完整度 <60% 或数据已过期 → 降级条件做，补数据后复测）。
    """
    if not candidates:
        return []
    if min_margin is None:
        from backend.config.selection_funnel import SELECTION_FUNNEL_MIN_MARGIN
        min_margin = SELECTION_FUNNEL_MIN_MARGIN
    lines = [f"- 口径：规则初判草案（贡献利润率线 {min_margin:.0%}），供人工拍板，"
             "依据全部来自漏斗已算数字"]
    tiers = {"做": 0, "条件做": 0, "放弃": 0}
    for c in candidates[:3]:
        econ = c.get("economics") or {}
        margin = econ.get("margin")
        title = (c.get("title") or c.get("url", ""))[:24]
        dq = c.get("data_quality") or {}
        if margin is None:
            tiers["条件做"] += 1
            lines.append(f"- 「{title}」→ **条件做**（贡献利润率缺失：补售价/成本后复测）")
            continue
        buffer_pp = (margin - min_margin) * 100
        if margin < min_margin:
            tiers["放弃"] += 1
            lines.append(f"- 「{title}」→ **放弃**（贡献利润率 {margin:.1%} 已低于 "
                         f"{min_margin:.0%} 线）")
            continue
        conditions: list[str] = []
        verdict = "做"
        if buffer_pp < 2:
            verdict = "放弃"
            conditions = [f"贡献利润率 {margin:.1%} 贴线（仅高 {buffer_pp:.1f}pp），无缓冲"]
        elif buffer_pp < 5:
            verdict = "条件做"
            conditions.append(f"贡献利润率缓冲 {buffer_pp:.1f}pp 不足 5pp，控制首单量")
        if verdict != "放弃":
            if econ.get("unit_cost_estimated"):
                verdict = "条件做"
                conditions.append("成本为估计值，先向供应商询价校准")
            if c.get("pain_points"):
                verdict = "条件做"
                conditions.append("存在差评实证痛点，对策落地后再上量")
            if dq.get("freshness") == "stale":
                verdict = "条件做"
                days = dq.get("age_days")
                day_txt = f"（{days:g} 天）" if isinstance(days, (int, float)) else ""
                conditions.append(f"数据已过期{day_txt}，重新抓取/导入后复测")
            elif (dq.get("completeness") is not None
                  and dq["completeness"] < 0.6):
                verdict = "条件做"
                conditions.append(f"数据完整度 {dq['completeness']:.0%} 偏低，"
                                  "补齐关键字段后复测")
        tiers[verdict] += 1
        cond = f"；条件：{'；'.join(conditions)}" if conditions else ""
        lines.append(f"- 「{title}」→ **{verdict}**（贡献利润率 {margin:.1%}，"
                     f"缓冲 {buffer_pp:+.1f}pp{cond}）")
    lines.append(f"- 分层汇总：做 {tiers['做']} 条 / 条件做 {tiers['条件做']} 条 / "
                 f"放弃 {tiers['放弃']} 条（依据：贡献利润率缓冲 / 成本口径 / "
                 "痛点 / 数据质量）")
    return lines


def _summary_lines(pool: list[dict], candidates: list[dict]) -> list[str]:
    """执行摘要（P1）：一句话结论 + Top-1 关键数字。"""
    lines = [f"- 建池 {len(pool or [])} 条 → 推荐 {len(candidates)} 条"]
    if candidates:
        top = candidates[0]
        econ = top.get("economics") or {}
        margin = econ.get("margin")
        head = (f"- Top-1「{(top.get('title') or '')[:24]}」"
                f"潜力分 {(top.get('score') or {}).get('total', '-')}")
        if margin is not None:
            head += f" / 贡献利润率 {margin:.1%}"
        lines.append(head)
        lines.append("- 结论：有可验证候选，按「测款计划」小额试错后再谈放量")
    else:
        lines.append("- 结论：本轮无可推荐候选，见淘汰明细与建议")
    return lines


def _evidence_lines(candidates: list[dict]) -> list[str]:
    """证据与假设（P1）：实际值 / 估算值 / 缺失数据 / 数据完整度，可追溯。"""
    if not candidates:
        return []
    actual: list[dict] = []
    estimated: list[dict] = []
    no_margin: list[dict] = []
    for c in candidates:
        econ = c.get("economics") or {}
        if econ.get("margin") is None:
            no_margin.append(c)
        elif c.get("unit_cost") is not None:
            actual.append(c)
        elif econ.get("unit_cost_estimated"):
            estimated.append(c)
        else:
            actual.append(c)
    lines = [f"- 实际值：{len(actual)} 条使用明确成本（来源：导入表「成本」列或对话提供）"
             if actual else "- 实际值：无——本轮全部成本为估算口径"]
    if estimated:
        names = "、".join((c.get("title") or c.get("url", ""))[:16]
                          for c in estimated[:5])
        more = f"（另 {len(estimated) - 5} 条略）" if len(estimated) > 5 else ""
        lines.append(f"- 估算值：{len(estimated)} 条成本按默认比例估计——{names}{more}；"
                     "决策草案已按「条件做」降级")
    if no_margin:
        lines.append(f"- 缺失数据：{len(no_margin)} 条缺售价无法测算利润（保留并披露）")
    comps = [(c.get("data_quality") or {}).get("completeness") for c in candidates]
    comps = [v for v in comps if v is not None]
    if comps:
        field_labels = {"title": "标题", "price": "售价", "rating": "评分",
                        "review_count": "评价数", "sales": "销量", "highlights": "卖点"}
        worst = min(candidates,
                    key=lambda c: (c.get("data_quality") or {}).get("completeness", 1))
        worst_dq = worst.get("data_quality") or {}
        missing = "、".join(field_labels.get(f, f)
                            for f in (worst_dq.get("missing") or []))
        lines.append(
            f"- 数据完整度：Top-N 均值 {sum(comps) / len(comps):.0%}，"
            f"最低「{(worst.get('title') or '')[:20]}」{worst_dq.get('completeness', 0):.0%}"
            f"（缺: {missing or '无'}）；完整度仅提示，不参与扣分")
    fr = [(c.get("data_quality") or {}).get("freshness") or "unknown"
          for c in candidates]
    stale_n, unknown_n = fr.count("stale"), fr.count("unknown")
    from backend.config.selection_funnel import SELECTION_FUNNEL_STALE_DAYS
    if stale_n:
        stale_names = "、".join((c.get("title") or c.get("url", ""))[:16]
                                for c, v in zip(candidates, fr) if v == "stale")
        lines.append(
            f"- 数据新鲜度：{stale_n} 条已过期（>{SELECTION_FUNNEL_STALE_DAYS:g} 天，"
            f"重新抓取/导入后结论更可靠）——{stale_names}；"
            f"新鲜 {len(fr) - stale_n - unknown_n} 条 / 无时间戳 {unknown_n} 条；"
            "仅提示不淘汰")
    elif unknown_n < len(fr):
        lines.append(f"- 数据新鲜度：全部 {len(fr)} 条在 {SELECTION_FUNNEL_STALE_DAYS:g} 天内，"
                     "无需重新抓取")
    else:
        lines.append("- 数据新鲜度：候选无抓取/导入时间戳，无法判断——"
                     "导入表格加「抓取时间」列或走监控池后启用")
    return lines


def _source_lines(stage_logs: list[dict]) -> list[str]:
    """来源健康度（P2）：pool 层各源 ok/empty/error，进漏斗计数段。"""
    for log in stage_logs:
        if log.get("stage") != "pool":
            continue
        sources = log.get("sources") or []
        if not sources:
            return []
        parts = [f"{s.get('source')} {s.get('status')}({s.get('count', 0)})"
                 for s in sources]
        if any(s.get("status") != "ok" for s in sources):
            parts.append("error=读取失败已跳过 / empty=空池")
        return ["- 来源健康：" + " / ".join(parts)]
    return []


def _trend_lines(candidates: list[dict]) -> list[str]:
    """多次快照趋势（P2 余量）：价格/评价/评分首末对比；单点如实披露。"""
    if not candidates:
        return []
    lines: list[str] = []
    no_history = 0
    for c in candidates:
        t = c.get("trend") or {}
        n = t.get("snapshots", 0)
        if not isinstance(n, int) or n < 2:
            no_history += 1
            continue
        parts = []
        p = t.get("price")
        if p and p.get("pct") is not None:
            parts.append(f"价格 {p['first']:g}→{p['last']:g}（{p['pct']:+.1%}）")
        r = t.get("reviews")
        if r and r.get("pct") is not None:
            parts.append(f"评价 {r['first']:g}→{r['last']:g}（{r['pct']:+.1%}）")
        rt = t.get("rating")
        if rt and rt.get("first") != rt.get("last"):
            parts.append(f"评分 {rt['first']:g}→{rt['last']:g}")
        lines.append(f"- 「{(c.get('title') or '')[:24]}」近 {n} 次快照："
                     + ("；".join(parts) if parts else "关键指标持平"))
    if lines and no_history:
        lines.append(f"- 其余 {no_history} 条为单点候选，无历史快照，趋势不可算")
    if not lines:
        lines.append("- 本轮候选均为单点数据（导入池无时间序列）：把核心竞品加入监控，"
                     "或同款多次导入后可看价格/热度走势")
    return lines


def _config_lines(brief, min_margin: float) -> list[str]:
    """运行配置快照渲染（与 build_config_snapshot 同源，报告可复现）。"""
    from backend.config.selection_funnel import build_config_snapshot
    snap = build_config_snapshot(brief.category, min_margin)
    lines = [
        f"- 候选池来源优先级：{' → '.join(snap['pool_sources'])}",
        f"- 初筛线：评分 ≥ {snap['min_rating']:g}，评价/销量线 ≥ {snap['min_reviews']:g}，"
        f"池上限 {snap['max_pool']:g}",
        f"- 利润口径：贡献利润率线 {snap['min_margin']:.0%}"
        f"（扣点 {snap['fee_rate']:.1%} / 物流 {snap['logistics_fee']:g} 元 / "
        f"推广 {snap['ads_ratio']:.0%} / 退款损耗 {snap['refund_ratio']:.0%}）；"
        f"商品毛利率仅展示不门控",
        f"- 输出 Top-N：{snap['top_n']:g}",
    ]
    if snap.get("category_rules"):
        lines.append(f"- 类目差异化规则：{snap['category_rules']}")
    lines.append(f"- 规则版本：`{snap.get('rules_version', '-')}`"
                 "（阈值集内容指纹，任何阈值变更即新版本）")
    return lines


def render_report(brief, stage_logs: list[dict], candidates: list[dict],
                  notes: list[str], knowledge: list[str] | None = None,
                  market: list[str] | None = None,
                  pains: list[str] | None = None,
                  pool: list[dict] | None = None,
                  moq: float | None = None,
                  min_margin: float | None = None,
                  config_lines: list[str] | None = None) -> str:
    """正常路径报告。knowledge = 合规提示；market/pains = 赛道画像 / 痛点机会；
    pool = 建池全量（竞争格局）；moq/min_margin = 测款卡首批投入 / 决策草案利润线；
    config_lines = 本次运行配置（阈值/口径快照，可复现）。"""
    lines = [
        f"## 智能选品漏斗报告（{brief.category}）", "",
        "### 执行摘要", "",
    ]
    lines += _summary_lines(pool or [], candidates)
    lines += [
        "",
        f"需求口径：平台 {brief.platform or '不限'}；"
        f"价格带 {f'{brief.price_min:g}-{brief.price_max:g}元' if brief.price_min is not None and brief.price_max is not None else '不限'}；"
        f"目标贡献利润率 {brief.target_margin:.0%}" if brief.target_margin else
        f"需求口径：平台 {brief.platform or '不限'}；价格带 不限；目标贡献利润率 用类目默认", "",
        "### 漏斗计数", "",
    ]
    lines += _stage_table(stage_logs)
    lines += _source_lines(stage_logs)
    if config_lines:
        lines += ["", "### 运行配置（本次口径）", ""] + config_lines
    lines += ["", "### 推荐 Top-N", ""]
    if candidates:
        lines += _recommend_table(candidates)
    else:
        lines.append("（无候选存活到排序层）")
    drop_details = _drop_details(stage_logs)
    if drop_details:
        lines += ["", "### 淘汰明细", ""] + drop_details
    if knowledge:
        lines += ["", "### 合规与知识层提示", ""] + knowledge
    if market:
        lines += ["", "### 赛道画像（关键词榜）", ""] + market
    competition = _competition_lines(pool or [], candidates)
    if competition:
        lines += ["", "### 竞争格局（商品榜）", ""] + competition
    trend = _trend_lines(candidates)
    if trend:
        lines += ["", "### 价格与热度趋势（多次快照）", ""] + trend
    if pains:
        lines += ["", "### 痛点机会（差评实证）", ""] + pains
    test_plan = _test_plan_lines(candidates, moq=moq)
    if test_plan:
        lines += ["", "### 测款计划（小额试错）", ""] + test_plan
    draft = _decision_draft_lines(candidates, min_margin=min_margin)
    if draft:
        lines += ["", "### 决策草案（规则初判）", ""] + draft
    evidence = _evidence_lines(candidates)
    if evidence:
        lines += ["", "### 证据与假设", ""] + evidence
    if notes:
        lines += ["", "### 数据缺口与说明", ""]
        lines += [f"- {n}" for n in dict.fromkeys(notes)]
    lines += ["", "### 下一步建议", ""]
    if candidates:
        lines.append("- 按「测款计划」小额验证 Top 1-2 款，验证通过再谈放量与供应链")
    else:
        lines.append("- 对 Top 1-2 款做小额测款（直通车/搜索推广 300-500 元），"
                     "点击率 > 行业 1.2 倍、加购率 > 8% 再放量")
    lines.append("- 需要判断「这个品类值不值得做」时，让我跑选品决策"
                 "（差异化 + 财务 + AI 评审团）")
    return "\n".join(lines)


def render_empty_pool(brief, stage_logs: list[dict], notes: list[str]) -> str:
    """漏斗中途淘空的报告：如实说明在哪一层、为什么。"""
    last = stage_logs[-1] if stage_logs else {}
    from backend.config.selection_funnel import (
        SELECTION_FUNNEL_MIN_MARGIN, rules_for)
    min_margin = float(brief.target_margin
                       or rules_for(brief.category).get("min_margin",
                                                        SELECTION_FUNNEL_MIN_MARGIN))
    lines = [
        f"## 智能选品漏斗报告（{brief.category}）", "",
        f"**候选在「{_STAGE_LABELS.get(last.get('stage'), last.get('stage', ''))}」层全部淘汰，"
        f"本轮无推荐。**", "",
        "### 漏斗计数", "",
    ]
    lines += _stage_table(stage_logs)
    lines += _source_lines(stage_logs)
    lines += ["", "### 运行配置（本次口径）", ""] + _config_lines(brief, min_margin)
    drop_details = _drop_details(stage_logs)
    if drop_details:
        lines += ["", "### 淘汰明细", ""] + drop_details
    if notes:
        lines += ["", "### 数据缺口与说明", ""]
        lines += [f"- {n}" for n in dict.fromkeys(notes)]
    lines += [
        "", "### 建议", "",
        "- 淘空是漏斗的诚实输出，不是故障：放宽对应阈值（见 config/selection_funnel.py），"
        "或扩充候选池后重跑",
    ]
    return "\n".join(lines)


def render_need_info(missing: list[str]) -> str:
    return (
        "要做智能选品，我还需要知道**品类**：你想在哪个品类里选品？\n"
        "（例如：宠物零食、保温杯、蓝牙耳机。也可以顺带说明平台 / 价格带 / 成本，"
        "我说不准的不会乱猜。）"
    )


def reporter_node(state: dict) -> dict:
    """出口节点：need_info / empty_pool 透传各层已渲染的如实报告；
    正常路径渲染完整报告。"""
    status = state.get("status") or "ok"
    if status in (STATUS_NEED_INFO, STATUS_EMPTY):
        return {"final_answer": state.get("final_answer")
                or render_need_info(state.get("brief_missing") or []),
                "status": status,
                "finished": True}
    from backend.selection_funnel.graph_state import load_brief
    from backend.selection_funnel.knowledge import compliance_review
    from backend.config.selection_funnel import SELECTION_FUNNEL_MIN_MARGIN, rules_for
    brief = load_brief(state)
    candidates = list(state.get("candidates") or [])
    knowledge, knotes = compliance_review(
        candidates, brief.platform, brief.category)
    notes = list(state.get("notes") or []) + knotes
    min_margin = float(brief.target_margin
                       or rules_for(brief.category).get("min_margin",
                                                        SELECTION_FUNNEL_MIN_MARGIN))
    ctx = state.get("funnel_context") or {}
    moq = (ctx.get("supply") or {}).get("moq") if isinstance(ctx, dict) else None
    answer = render_report(brief, list(state.get("stage_logs") or []),
                           candidates, notes, knowledge=knowledge,
                           market=_market_lines(brief.category),
                           pains=_pain_lines(candidates, brief.category),
                           pool=list(state.get("pool") or []),
                           moq=moq, min_margin=min_margin,
                           config_lines=_config_lines(brief, min_margin))
    from backend.config.selection_funnel import build_config_snapshot
    snap = build_config_snapshot(brief.category, min_margin)
    if state.get("run_id"):
        snap["run_id"] = state["run_id"]
    return {"final_answer": answer, "status": "ok",
            "config_snapshot": snap,
            "finished": True}
