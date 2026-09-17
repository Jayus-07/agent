"""文档版本裁决（§6 版本检索消费方，2026-09-17 R4）。

与 backend/rag/permissions.py（文档级权限）同构的设计原则：
  - 裁决是确定性计算（本模块），路由/LLM 只能在裁决通过的候选里挑选；
  - 请求侧的版本要求（version_requirement）在入口解析一次，下游只读；
  - 版本元数据来自 registry 行声明 → doc_meta → chunk metadata（R4-P1
    贯通链路），缺失 = 非版本链文档（无时效约束，恒可见——存量语料行为
    完全保留，与 permission_scope 缺省 general 同哲学）。

语义约定（与 rag_100_docs 版本链标注一致，TRAVEL_VERSIONS 为基准）：
  - 文档侧：version_id（v1/v2/v3）+ effective_from/effective_to（ISO date，
    effective_to 空 = 现行版本，尚未被取代）+ supersedes_version_id
    （被本版本取代的前版 doc_id，冲突溯源用）。
  - 请求侧：version_requirement = {"type": any|as_of|current|all_versions,
    "date": "YYYY-MM-DD"（as_of 必填）}。
  - 裁决：
      any          → 不约束（默认，行为与 R3 前完全一致）
      current      → 仅保留现行版本（已生效 from <= 今天，且未被取代
                     to 空或未到期）
      as_of(date)  → 仅保留生效窗口覆盖 date 的版本（from <= date <= to，
                     边界含端点——生效日当天/失效日当天均视为有效）
      all_versions → 全部放行（跨版本链查询，如「V1 至今调整过几次」）
  - 非版本链文档（version_id 与 effective_from 均空）：视为无时效文档，
    current/as_of 查询下恒可见。版本约束只裁剪版本链文档之间的选择，
    不误伤普通语料。

版本检索 5 条的落点（任务书 §6）：
  1. 日期/版本筛选 → as_of(date) 窗口裁决（本模块）
  2. 当前版本     → current 裁决（本模块）
  3. 缺失不猜     → 版本链文档被裁剪干净后不留残余候选（is_visible 对
     链内非命中版本返回 False），上游据此确定性拒答，不猜邻近版本
  4. 冲突留证     → version_conflict_info() 汇报窗口重叠/链分支冲突，
     供 trace 与 rejection 留痕
  5. 跨文档多候选 → all_versions 放行 + candidate_versions() 汇报
     全链候选，供引用层并列展示
"""
from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Iterable

VALID_REQUIREMENT_TYPES = ("any", "as_of", "current", "all_versions")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _today() -> str:
    return _date.today().isoformat()


def normalize_requirement(value: Any) -> dict:
    """version_requirement 字段值 → 规范化结构。

    兼容 None / {} / 非法 type（回退 any 并保留 raw 供留痕）。
    返回 {"type": ..., "date": ..., "raw": ...}。
    """
    raw = value if isinstance(value, dict) else None
    rtype = str((raw or {}).get("type") or "any").strip().lower()
    if rtype not in VALID_REQUIREMENT_TYPES:
        rtype = "any"
    rdate = str((raw or {}).get("date") or "").strip()
    if rtype == "as_of" and not _ISO_DATE_RE.match(rdate):
        # as_of 缺合法日期 → 无法约束，退化为 any（留痕由调用方做）
        return {"type": "any", "date": "", "raw": raw}
    return {"type": rtype, "date": rdate, "raw": raw}


def _is_versioned(meta: dict | None) -> bool:
    """chunk/doc metadata 是否携带版本链声明（version_id 或生效窗口非空）。"""
    if not meta:
        return False
    return bool(
        str(meta.get("version_id") or "").strip()
        or str(meta.get("effective_from") or "").strip()
    )


def _window(meta: dict) -> tuple[str, str]:
    """metadata → (effective_from, effective_to)，空串表示无界。"""
    return (
        str(meta.get("effective_from") or "").strip(),
        str(meta.get("effective_to") or "").strip(),
    )


def _in_window(meta: dict, point: str) -> bool:
    """生效窗口是否覆盖时间点 point（ISO date 文本比较；边界含端点）。"""
    start, end = _window(meta)
    if start and point < start:
        return False
    if end and point > end:
        return False
    return True


def partition_by_version(
    metas: list[dict], requirement: dict | None, today: str | None = None,
) -> tuple[list[int], list[int]]:
    """按下标把 metadata 列表分为 (可见, 版本不匹配) 两组，保持原有顺序。

    today 参数仅供测试注入 current 的"今天"；生产路径不传用系统日期。
    """
    req = normalize_requirement(requirement)
    allowed: list[int] = []
    denied: list[int] = []
    for i, m in enumerate(metas):
        if _visible(m, req, today):
            allowed.append(i)
        else:
            denied.append(i)
    return allowed, denied


def _visible(meta: dict | None, req: dict, today: str | None) -> bool:
    """可见性裁决主体（today=None 用系统日期）。"""
    if req["type"] == "any":
        return True
    if not _is_versioned(meta):
        return True
    if req["type"] == "all_versions":
        return True
    if req["type"] == "current":
        start, end = _window(meta or {})
        point = today or _today()
        # 现行 = 已生效（from <= 今天）且未被取代（to 空 或 >= 今天）
        return (not start or start <= point) and ((not end) or end >= point)
    if req["type"] == "as_of":
        return _in_window(meta or {}, req["date"])
    return True


def is_version_visible(meta: dict | None, requirement: dict | None) -> bool:
    """单条 chunk/doc metadata 的版本可见性裁决（生产入口）。"""
    return _visible(meta, normalize_requirement(requirement), None)


def candidate_versions(metas: Iterable[dict | None]) -> list[dict]:
    """候选里的版本链文档清单（跨文档多候选汇报用，去重保序）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for m in metas:
        if not _is_versioned(m):
            continue
        vid = str(m.get("doc_id") or "")
        key = f"{vid}:{m.get('version_id', '')}"
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "doc_id": vid,
            "version_id": str(m.get("version_id") or ""),
            "effective_from": str(m.get("effective_from") or ""),
            "effective_to": str(m.get("effective_to") or ""),
            "supersedes_version_id": str(m.get("supersedes_version_id") or ""),
        })
    return out


def version_conflict_info(metas: Iterable[dict | None]) -> list[dict]:
    """版本冲突留证（任务书 §6 第 4 条）。

    检测两类冲突（同一批候选内）：
      a) 窗口重叠：两个版本链文档的生效窗口在同一天都覆盖（多来源同时
         有效，答案可能互相矛盾）
      b) supersedes 双向冲突：A 声明取代 B 而 B 也声明取代 A（链坏）
    返回冲突描述列表（空 = 无冲突）。
    """
    versions = candidate_versions(metas)
    conflicts: list[dict] = []
    for i in range(len(versions)):
        for j in range(i + 1, len(versions)):
            a, b = versions[i], versions[j]
            if _windows_intersect(a["effective_from"], a["effective_to"],
                                  b["effective_from"], b["effective_to"]):
                conflicts.append({
                    "type": "window_overlap",
                    "docs": [a["doc_id"], b["doc_id"]],
                    "windows": [[a["effective_from"], a["effective_to"]],
                                [b["effective_from"], b["effective_to"]]],
                })
            if a["supersedes_version_id"] and a["supersedes_version_id"] == b["doc_id"] \
                    and b["supersedes_version_id"] == a["doc_id"]:
                conflicts.append({
                    "type": "supersedes_cycle",
                    "docs": [a["doc_id"], b["doc_id"]],
                })
    return conflicts


def _windows_intersect(s_a: str, e_a: str, s_b: str, e_b: str) -> bool:
    """两个 [start, end] 窗口（空串 = 无界）是否相交。"""
    lo_a, hi_a = s_a or "0000-01-01", e_a or "9999-12-31"
    lo_b, hi_b = s_b or "0000-01-01", e_b or "9999-12-31"
    return lo_a <= hi_b and lo_b <= hi_a
