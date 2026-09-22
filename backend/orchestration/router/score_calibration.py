"""score_calibration.py — 工具路由分数校准（2026-09-22 D6 修复）

问题（D6 路由能力漂移）：「统计本月订单金额」在向量召回里被 data.collect
的采集类 examples（"采集最近一个月的订单数据"语义过近）拉走 top1
（实测 data.collect 0.697 > sql.query），灰区交 FC 后跟随错误候选执行失败。

方案（用户规格：规则强信号 → 候选召回 → score calibration → FC，
不扩大关键词硬路由、不把 sql.query 固定最高优先级）：

  1. 能力特征声明在 capabilities.yaml（唯一事实源）：
     sql.query  = 统计/汇总/金额/数量/同比/环比/TopN/各部门…
     data.collect = 采集/抓取/同步/导入/获取外部数据…
  2. 向量召回完成后，按**查询命中的特征数**校准各候选分数：
     - 命中 ≥1 个特征 → 加分（每命中 +0.08，封顶 3 个）
     - 零命中且有其他候选命中 ≥2 → 降权（-0.05，采集类 examples 语义
       搭便车惩罚）
  3. 校准是**逐能力独立**的加分/减分，不引入任何「永久优先级」；
     只有显式 opt-in（calibrate_signals: true）的能力参与，
     RAG/Travel/CS/Selection 等其他域分数零变化。

纯函数、零 IO、零 LLM、确定性（同 query 同输入必同输出）。
"""
from __future__ import annotations

import re

# ── 校准参数（集中声明，评测数据说话后再调）─────────────────────
# 每命中 1 个特征词的加分幅度。0.08 × 3 命中 = +0.24，足以翻转 D6 观测到的
# 0.697 vs ~0.55 量级的向量分差，又不至于把 0.3 的保底分抬进 fast path。
BONUS_PER_HIT = 0.08
# 计入加分的最大命中数（防长 query 关键词堆砌冲分）。
MAX_COUNTED_HITS = 3
# 零命中降权幅度：仅当存在 ≥2 命中的竞争者时生效（语义搭便车惩罚）。
ZERO_HIT_DEMOTION = 0.05
# 强信号阈值：唯一达到该命中数的候选 → 细路由直通（hierarchical 消费）。
STRONG_SIGNAL_HITS = 2


def _calibration_groups() -> dict[str, tuple[str, ...]]:
    """opt-in 校准的关键词组（capabilities.yaml 唯一事实源）。"""
    from backend.orchestration.router.manifest import load_manifest

    return load_manifest().calibration_keyword_groups


def compute_signal(query: str, candidate_names: list[str] | tuple[str, ...]) -> dict[str, int]:
    """计算各候选能力在 query 上命中的特征词数（只统计 opt-in 能力）。

    Returns:
        {capability: 命中数}；未 opt-in 的能力不出现（命中数视为 0）。
    """
    groups = _calibration_groups()
    if not groups:
        return {}
    query_lower = query.lower()
    hits: dict[str, int] = {}
    for cap in candidate_names:
        kws = groups.get(cap)
        if not kws:
            continue
        hits[cap] = sum(1 for k in kws if re.search(k, query_lower))
    return hits


def calibrate_scores(
    query: str, scores: dict[str, float]
) -> tuple[dict[str, float], dict]:
    """对向量召回分数做规则特征校准。

    Args:
        query: 用户问题原文
        scores: {capability: 向量召回分数}

    Returns:
        (calibrated_scores, info)
        - calibrated_scores: 校准后的分数（键与输入一致，clamp 到 [0,1]）
        - info: 校准明细（hits/strong/adjusted），空 dict 表示未触发校准
         （无 opt-in 能力或全部零命中——此时分数原样返回）
    """
    groups = _calibration_groups()
    involved = {cap for cap in scores if cap in groups}
    if not groups or not involved:
        return scores, {}

    hits = compute_signal(query, list(scores.keys()))
    if not hits or all(h == 0 for h in hits.values()):
        return scores, {}

    max_hits = max(hits.values())
    calibrated: dict[str, float] = {}
    adjusted: dict[str, float] = {}
    for cap, score in scores.items():
        adj = score
        n = hits.get(cap, 0)
        if n > 0:
            adj += BONUS_PER_HIT * min(n, MAX_COUNTED_HITS)
        elif max_hits >= STRONG_SIGNAL_HITS:
            adj -= ZERO_HIT_DEMOTION
        adj = round(min(max(adj, 0.0), 1.0), 3)
        calibrated[cap] = adj
        if abs(adj - score) > 1e-9:
            adjusted[cap] = round(adj - score, 3)

    info = {
        "hits": hits,
        "strong": sorted(c for c, h in hits.items() if h >= STRONG_SIGNAL_HITS),
        "adjusted": adjusted,
    }
    return calibrated, info
