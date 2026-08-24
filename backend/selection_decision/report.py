"""selection_decision/report.py — Go/No-Go 决策包 Markdown 组装

所有数字直接取自 workflow outputs（事实锁定：报告层不做任何推算）。
被 run_if 跳过的 step（outputs 含 skipped=True）渲染为「未执行」。
约定：failed_gates 传 step key（market/differentiation/finance/panel），
经 GATE_LABELS 映射渲染。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

GATE_LABELS = {
    "market": "市场评估（Q1）",
    "differentiation": "差异化分析",
    "finance": "财务测算",
    "panel": "AI 评审团",
}


def _skipped(out: dict[str, Any] | None) -> bool:
    return bool(out and out.get("skipped"))


def build_report(inputs: dict[str, Any], outputs: dict[str, Any],
                 verdict: str, failed_gates: list[str]) -> str:
    lines: list[str] = [
        "# 选品决策报告（Go/No-Go 决策包）",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 品类关键词：{inputs.get('category', '-')}",
        f"- 目标平台：{'、'.join(inputs.get('platforms') or []) or '-'}",
        f"- **最终决策：{'🚀 Go — 建议入场' if verdict == 'go' else '❌ No-Go — 不建议入场'}**",
    ]
    if failed_gates:
        lines += ["- 未通过环节：" + "、".join(
            GATE_LABELS.get(g, g) for g in failed_gates), ""]

    # ── 市场评估 ──
    market = outputs.get("market_assess")
    lines += ["", "## 一、市场评估（Q1：这个市场要不要做）", ""]
    if _skipped(market):
        lines.append(f"本环节未执行（{market.get('reason', '前置条件未满足')}）。")
    elif market:
        m = market.get("metrics") or {}
        lines += [
            f"- 结论：**{'建议继续' if market.get('verdict') == 'go' else '不建议'}**",
            f"- 候选竞品数：{m.get('candidate_count', '-')}（代理指标，非真实市场体量）",
            f"- 价格带：{m.get('price_min', '-')} ~ {m.get('price_max', '-')}",
            f"- 评价总量：{m.get('total_reviews', '-')}（需求热度代理）",
            f"- TOP3 评价集中度：{m.get('top3_review_share', '-')}",
        ]
        if market.get("data_gaps"):
            lines.append("- ⚠️ 数据缺口：" + "；".join(market["data_gaps"]))
    else:
        lines.append("本环节未执行（前置条件未满足）。")

    # ── 差异化 ──
    diff = outputs.get("differentiation")
    lines += ["", "## 二、差异化分析（Decision1：是否存在切入点）", ""]
    if _skipped(diff):
        lines.append(f"本环节未执行（{diff.get('reason', '市场评估未通过')}）。")
    elif diff:
        lines.append(f"- 结论：**{'存在切入点' if diff.get('verdict') == 'go' else '无明显切入点'}**")
        if diff.get("gaps"):
            lines.append("- 需求缺口：" + "、".join(diff["gaps"]))
        if diff.get("reason"):
            lines.append(f"- 依据：{diff['reason']}")
        lines.append("- ⚠️ Phase 1 痛点来源为 LLM 推断（非评论实证），仅供参考。")
    else:
        lines.append("本环节未执行（前置条件未满足）。")

    # ── 财务 ──
    fin = outputs.get("finance_model")
    lines += ["", "## 三、财务测算（Decision2：模型是否达标）", ""]
    if _skipped(fin):
        lines.append(f"本环节未执行（{fin.get('reason', '差异化分析未通过')}）。")
    elif fin:
        fm = fin.get("final_model") or {}
        lines += [
            f"- 结论：**{'达标' if fin.get('verdict') == 'pass' else '不达标'}**"
            f"（共 {len(fin.get('rounds') or [])} 轮测算）",
            "",
            "| 指标 | 数值 |", "|---|---|",
            f"| 单件利润 | {fm.get('unit_margin', '-')} |",
            f"| 利润率 | {fm.get('margin_rate', '-')} |",
            f"| 盈亏平衡销量 | {fm.get('break_even_units', '-')} 件/月 |",
            f"| 首批投入 | {fm.get('first_batch_investment', '-')} |",
            f"| 风险缓冲金 | {fm.get('risk_buffer', '-')} |",
        ]
        for s in fin.get("suggestions", []):
            lines.append(f"- 优化记录：{s}")
    else:
        lines.append("本环节未执行（前置条件未满足）。")

    # ── 评审团 ──
    panel = outputs.get("review_panel")
    lines += ["", "## 四、AI 评审团（Decision3：独立投票）", ""]
    if _skipped(panel):
        lines.append(f"本环节未执行（{panel.get('reason', '财务测算未达标')}）。")
    elif panel:
        go_count = panel.get("go_count") if panel.get("go_count") is not None else "-"
        size = panel.get("size") if panel.get("size") is not None else "-"
        avg = panel.get("avg_score") if panel.get("avg_score") is not None else "-"
        lines += [
            f"- 结论：**{'通过' if panel.get('verdict') == 'pass' else '未通过'}**"
            f"（{go_count}/{size} 票 Go，均分 {avg}）",
            "",
            "| 角色 | 评分 | 投票 | 理由 |", "|---|---|---|---|",
        ]
        for v in panel.get("votes", []):
            lines.append(f"| {v.get('role')} | {v.get('score')} | "
                         f"{v.get('verdict')} | {v.get('reason', '')} |")
    else:
        lines.append("本环节未执行（前置条件未满足）。")

    lines += ["", "---", "*本报告由选品决策 Workflow 自动生成；"
              "代理指标与推断结论已如实标注，请结合人工判断使用。*"]
    return "\n".join(lines)
