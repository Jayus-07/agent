"""selection_funnel/reporter.py — 漏斗报告渲染

纪律：渲染 + 如实披露缺口，不补造事实。
每一层淘汰了谁、为什么淘汰，全部落进报告 —— 可解释性是选品建议的命根子。
"""
from __future__ import annotations

from backend.selection_funnel.graph_state import STATUS_EMPTY, STATUS_NEED_INFO

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
    lines = ["| # | 商品 | 平台 | 售价 | 潜力分 | 毛利率 | 理由 |",
             "|---|---|---|---|---|---|---|"]
    for c in candidates:
        econ = c.get("economics") or {}
        margin = econ.get("margin")
        lines.append(
            f"| {c.get('rank', '-')} | {(c.get('title') or c.get('url', ''))[:28]} "
            f"| {c.get('platform') or '-'} | {_fmt(c.get('price'))} "
            f"| {(c.get('score') or {}).get('total', '-')} "
            f"| {f'{margin:.1%}' if margin is not None else '-'} "
            f"| {(c.get('reason') or '')[:120]} |")
    return lines


def render_report(brief, stage_logs: list[dict], candidates: list[dict],
                  notes: list[str]) -> str:
    """正常路径报告。"""
    lines = [
        f"## 智能选品漏斗报告（{brief.category}）", "",
        f"需求口径：平台 {brief.platform or '不限'}；"
        f"价格带 {f'{brief.price_min:g}-{brief.price_max:g}元' if brief.price_min is not None and brief.price_max is not None else '不限'}；"
        f"目标毛利率 {brief.target_margin:.0%}" if brief.target_margin else
        f"需求口径：平台 {brief.platform or '不限'}；价格带 不限；目标毛利率 用类目默认", "",
        "### 漏斗计数", "",
    ]
    lines += _stage_table(stage_logs)
    lines += ["", "### 推荐 Top-N", ""]
    if candidates:
        lines += _recommend_table(candidates)
    else:
        lines.append("（无候选存活到排序层）")
    drop_details = _drop_details(stage_logs)
    if drop_details:
        lines += ["", "### 淘汰明细", ""] + drop_details
    if notes:
        lines += ["", "### 数据缺口与说明", ""]
        lines += [f"- {n}" for n in dict.fromkeys(notes)]
    lines += [
        "", "### 下一步建议", "",
        "- 对 Top 1-2 款做小额测款（直通车/搜索推广 300-500 元），"
        "点击率 > 行业 1.2 倍、加购率 > 8% 再放量",
        "- 需要判断「这个品类值不值得做」时，让我跑选品决策（差异化 + 财务 + AI 评审团）",
    ]
    return "\n".join(lines)


def render_empty_pool(brief, stage_logs: list[dict], notes: list[str]) -> str:
    """漏斗中途淘空的报告：如实说明在哪一层、为什么。"""
    last = stage_logs[-1] if stage_logs else {}
    lines = [
        f"## 智能选品漏斗报告（{brief.category}）", "",
        f"**候选在「{_STAGE_LABELS.get(last.get('stage'), last.get('stage', ''))}」层全部淘汰，"
        f"本轮无推荐。**", "",
        "### 漏斗计数", "",
    ]
    lines += _stage_table(stage_logs)
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
    brief = load_brief(state)
    answer = render_report(brief, list(state.get("stage_logs") or []),
                           list(state.get("candidates") or []),
                           list(state.get("notes") or []))
    return {"final_answer": answer, "status": "ok",
            "finished": True}
