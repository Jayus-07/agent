"""market_research/pipeline.py — 证据管线纯函数层

设计约定（docs/superpowers/plans/2026-09-15-skill-integration-plan.md 批次 2）：
- 证据 schema: {evidence_id, source_type(official/media/ugc), url, fetched_at,
  published_at, raw_text 摘录, numbers[], title}
- 来源分级 official > media > ugc，参与结论置信度计算
- 事实锁定：type=="fact" 的结论必须 (a) 所引 evidence_id 存在 (b) 数字 ⊆ 所引
  证据 numbers，否则降级为"推断"——只防编造、不防错误来源（防错靠 Source Index 回溯）
- 本模块不含 IO / LLM / 工具调用，便于单测
"""
from __future__ import annotations

import re
from typing import Any

# ── 常量 ────────────────────────────────────────
EVIDENCE_PREFIX = "EV"

# 来源分级：官方 > 媒体 > UGC（参与置信度计算）
SOURCE_TIER = {"official": 3, "media": 2, "ugc": 1}

_OFFICIAL_HINTS = (".gov.cn", ".gov", "stats.", "mofcom", "customs.gov",
                   "miit", "caac", "statistics")
_UGC_HINTS = ("zhihu.com", "tieba.", "weibo.", "xiaohongshu.com", "bilibili.com",
              "douyin.com", "douban.com", "smzdm.com", "tieba.baidu")

# 数字提取：数值 + 可选中文单位/百分号（供事实锁定做子集校验）
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?(?:万亿元|亿美元|亿元|万亿|亿|万美元|万元|万|美元|元|%|％|倍)?")

# 分组关键词路由（按证据密度分组，不按章节 1:1）
GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "group_a": ("规模", "增长", "增速", "份额", "竞争", "格局", "品牌", "市占",
                "集中", "行业", "market"),
    "group_b": ("用户", "需求", "痛点", "画像", "价格", "消费", "决策", "评价",
                "商业", "盈利", "模式", "偏好"),
    "group_c": ("渠道", "流量", "供应链", "成本", "趋势", "风险", "壁垒",
                "机会", "物流", "供应", "出海"),
}


# ── 第 1→2 段：搜索结果解析 + 证据标准化 ─────────
def parse_search_results(md_text: str) -> list[dict[str, Any]]:
    """解析 web_search_tool 输出（`N. **标题**\\n 摘要\\n 链接` 格式）。

    返回 [{title, url, snippet}]；无链接或非 http 的条目跳过。
    """
    out: list[dict[str, Any]] = []
    if not md_text or md_text.startswith("[NO RESULTS]"):
        return out
    for block in re.split(r"\n\s*\n", md_text):
        m = re.search(r"\*\*(.+?)\*\*", block)
        if not m:
            continue
        title = m.group(1).strip()
        url = ""
        for line in block.splitlines():
            line = line.strip()
            if line.startswith(("http://", "https://")):
                url = line
                break
        if not url:
            continue
        snippet = ""
        for line in block.splitlines():
            s = line.strip()
            if s and not s.startswith(("http://", "https://")) and not s.startswith(f"{title}") \
                    and not re.match(r"^\d+\.\s", s):
                snippet = s
                break
        out.append({"title": title, "url": url, "snippet": snippet})
    return out


def classify_source_type(url: str) -> str:
    u = (url or "").lower()
    if any(h in u for h in _OFFICIAL_HINTS):
        return "official"
    if any(h in u for h in _UGC_HINTS):
        return "ugc"
    return "media"


def extract_numbers(text: str) -> list[str]:
    """提取文本中的数字（含中文单位/百分号），去重保序。"""
    seen, out = set(), []
    for m in _NUM_RE.findall(text or ""):
        n = m.replace(",", "").strip()
        if n and n not in seen and not re.fullmatch(r"\d{4}", n):  # 跳过纯年份
            seen.add(n)
            out.append(n)
    return out


def normalize_evidence(raw_items: list[dict[str, Any]], fetched_at: str) -> list[dict[str, Any]]:
    """去重（按 URL）、分级、抽数字、分配 evidence_id。"""
    evidence: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for i, item in enumerate(raw_items):
        url = item.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        raw_text = (item.get("content") or item.get("snippet") or "").strip()
        eid = f"{EVIDENCE_PREFIX}-{i:08x}"
        evidence.append({
            "evidence_id": eid,
            "title": item.get("title", ""),
            "url": url,
            "source_type": classify_source_type(url),
            "raw_text": raw_text[:2000],
            "numbers": extract_numbers(raw_text[:2000]),
            "search_query": item.get("search_query", ""),
            "fetched_at": fetched_at,
            "published_at": item.get("published_at"),
        })
    return evidence


def route_group(evidence: list[dict[str, Any]]) -> dict[str, list[str]]:
    """按关键词密度把证据分到组 A/B/C（每组独立分析，互不依赖）。

    命中得分 = 关键词在 title+raw_text 前 500 字的出现次数；平局归 group_a。
    """
    scores = {k: 0 for k in GROUP_KEYWORDS}
    text = (evidence.get("title", "") + evidence.get("raw_text", "")[:500]).lower()
    for g, kws in GROUP_KEYWORDS.items():
        scores[g] = sum(text.count(kw.lower()) for kw in kws)
    best = max(scores, key=lambda g: (scores[g], g == "group_a"))
    return best


def group_digest(evidence: list[dict[str, Any]], ids: list[str],
                 excerpt: int = 400) -> list[dict[str, Any]]:
    """为 LLM 生成某组的证据摘要（id/来源分级/标题/数字/原文摘录）。"""
    by_id = {e["evidence_id"]: e for e in evidence}
    return [{
        "evidence_id": eid,
        "source_type": by_id[eid]["source_type"],
        "title": by_id[eid]["title"],
        "numbers": by_id[eid]["numbers"][:20],
        "excerpt": by_id[eid]["raw_text"][:excerpt],
    } for eid in ids if eid in by_id]


# ── 第 4 段：统一事实锁定 + 引用校验 ─────────────
def _norm_num(v: Any) -> str:
    return re.sub(r"[,\s，]", "", str(v)).strip().lower()


def lock_facts(groups_out: dict[str, dict[str, Any]],
               evidence_index: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """校验各组结论：fact 级必须证据存在且数字 ⊆ 所引证据，否则降级为推断。

    返回 (locked_groups, downgrades)；confidence 由所引证据的来源分级计算。
    """
    downgrades: list[dict[str, Any]] = []

    def _confidence(ids: list[str]) -> str:
        tiers = [SOURCE_TIER.get((evidence_index[eid] or {}).get("source_type"), 1)
                 for eid in ids if eid in evidence_index]
        if 3 in tiers or len(tiers) >= 3:
            return "高"
        if 2 in tiers or len(tiers) >= 2:
            return "中"
        return "低"

    locked: dict[str, dict[str, Any]] = {}
    for gname, gout in groups_out.items():
        sections = []
        for sec in gout.get("sections", []):
            claims = []
            for c in sec.get("claims", []):
                claim = dict(c)
                ids = [str(x) for x in claim.get("evidence_ids", [])]
                valid_ids = [x for x in ids if x in evidence_index]
                claim_nums = {_norm_num(n) for n in claim.get("numbers", []) if str(n).strip()}
                ev_nums: set[str] = set()
                for eid in valid_ids:
                    ev_nums |= {_norm_num(n) for n in evidence_index[eid].get("numbers", [])}
                bad = []
                if claim.get("type") == "fact":
                    if len(valid_ids) < len(ids):
                        bad.append("引用证据不存在")
                    unsupported = claim_nums - ev_nums
                    if unsupported:
                        bad.append(f"数字无证据支撑: {sorted(unsupported)[:3]}")
                    if bad:
                        claim["type"] = "inference"
                        claim["note"] = "；".join(bad)
                        downgrades.append({"group": gname, "claim": claim.get("claim", ""),
                                           "reason": "；".join(bad)})
                claim["evidence_ids"] = valid_ids
                claim["confidence"] = _confidence(valid_ids) if valid_ids else "低"
                claims.append(claim)
            sections.append({**sec, "claims": claims})
        locked[gname] = {**gout, "sections": sections}
    return locked, downgrades


# ── 第 5 段：报告组装 ───────────────────────────
def build_report_md(category: str, locked_groups: dict[str, dict[str, Any]],
                    evidence: list[dict[str, Any]], downgrades: list[dict[str, Any]],
                    coverage_gaps: list[str], degraded_groups: list[str]) -> str:
    """组装 12 章节 Markdown 报告（执行摘要 + 三组 12 节 + 进入建议 + Source Index）。"""
    ev_by_type: dict[str, int] = {}
    for e in evidence:
        ev_by_type[e["source_type"]] = ev_by_type.get(e["source_type"], 0) + 1

    high_claims = [
        c for g in locked_groups.values() for s in g.get("sections", [])
        for c in s.get("claims", []) if c.get("confidence") == "高" and c.get("type") == "fact"
    ]
    lines: list[str] = [f"# 品类市场调研报告：{category}", ""]
    lines += [f"> 证据 {len(evidence)} 条（官方 {ev_by_type.get('official', 0)} / "
              f"媒体 {ev_by_type.get('media', 0)} / UGC {ev_by_type.get('ugc', 0)}），"
              f"事实级结论 {len(high_claims)} 条，降级为推断 {len(downgrades)} 条", ""]

    # 1. 执行摘要
    lines += ["## 1. 执行摘要", ""]
    if high_claims:
        for c in high_claims[:5]:
            lines.append(f"- {c['claim']}（证据: {', '.join(c['evidence_ids'])}）")
    else:
        lines.append("- 高置信事实结论不足，请结合下方各节与 Source Index 谨慎决策")
    lines.append("")

    # 2-11. 三组章节（LLM 失败降级的组标注骨架）
    seq = 2
    for gname in ("group_a", "group_b", "group_c"):
        g = locked_groups.get(gname) or {}
        for sec in g.get("sections", []):
            lines += [f"## {seq}. {sec.get('title', '未命名章节')}", ""]
            if gname in degraded_groups:
                lines += ["⚠️ 本节 LLM 分析失败，以下为模板骨架与原始证据列表。", ""]
            lines += [sec.get("content", ""), ""]
            for c in sec.get("claims", []):
                tag = "事实" if c.get("type") == "fact" else "推断"
                lines.append(f"- [{tag}|置信度 {c.get('confidence', '低')}] "
                             f"{c.get('claim', '')}（{', '.join(c.get('evidence_ids', [])) or '无引用'}）")
            lines.append("")
            seq += 1

    # 12. 进入建议（唯一汇合点，确定性组装）
    lines += ["## 12. 进入建议", ""]
    if coverage_gaps:
        lines.append("**数据覆盖缺口**：")
        for gap in coverage_gaps[:8]:
            lines.append(f"- {gap}")
        lines.append("")
    lines.append(f"- 本报告事实级结论 {len(high_claims)} 条、推断级 "
                 f"{len(downgrades) + sum(1 for g in locked_groups.values() for s in g.get('sections', []) for c in s.get('claims', []) if c.get('type') == 'inference')} 条；"
                 f"来源分级（官方>媒体>UGC）已参与置信度计算")
    lines.append("- 如需 Go/No-Go 决策，请运行选品决策工作流（selection_decision），"
                 "本报告证据可作为其输入")
    lines.append("- 注意：事实锁定只防编造、不防错误来源，关键数字请经 Source Index 回溯原文")
    lines.append("")

    # Source Index
    lines += ["## Source Index", ""]
    for e in evidence:
        lines.append(f"- `{e['evidence_id']}` [{e['source_type']}] "
                     f"{e['title']} — {e['url']}（采集于 {e['fetched_at']}）")
    lines.append("")
    return "\n".join(lines)
