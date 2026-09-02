"""trace_lint.py — Trace 数据质量巡检（2026-09-03 观测数据记录优化 P2-11）

扫描 data/trace_store.db 中的历史 trace，按已知数据异常模式逐项检查：
  1. span_id 重复（前端树构建/React key 冲突）
  2. end_time 为空的未关闭 span（埋点泄漏）
  3. 子 span 时长超过父 span（归因错误）
  4. 拒答但顶层 status != rejected（状态语义错误）
  5. llm_call span 无 token 用量（采集缺失）
  6. 无归因耗时占比 > 20%（埋点黑洞）

用法:
    python -m backend.scripts.trace_lint [--limit N] [--db PATH]

退出码: 0 = 全部通过, 1 = 发现问题（可挂 CI）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "trace_store.db"
)


def _span_map(trace: dict) -> dict[str, dict]:
    return {s.get("span_id"): s for s in trace.get("spans") or []}


def lint_trace(trace: dict) -> list[str]:
    """返回该 trace 的问题描述列表（空 = 通过）。"""
    issues: list[str] = []
    spans = trace.get("spans") or []
    tid = trace.get("id", "?")

    # 1. span_id 重复
    seen: set[str] = set()
    for s in spans:
        sid = s.get("span_id", "")
        if sid in seen:
            issues.append(f"[{tid}] span_id 重复: {sid}")
        seen.add(sid)

    by_id = _span_map(trace)
    for s in spans:
        sid = s.get("span_id", "")
        # 2. 未关闭 span
        if not s.get("end_time"):
            issues.append(f"[{tid}] span '{sid}' end_time 为空（未关闭）")
        # 3. 子超父
        pid = s.get("parent_id")
        if pid and pid in by_id:
            parent = by_id[pid]
            if s.get("duration_ms", 0) > parent.get("duration_ms", 0) + 5:
                issues.append(
                    f"[{tid}] span '{sid}' 时长 {s.get('duration_ms')}ms 超过"
                    f"父 '{pid}' {parent.get('duration_ms')}ms")

    # 4. 拒答与状态一致性
    rejection = (trace.get("metadata") or {}).get("rejection") or {}
    if rejection.get("rejected") and trace.get("status") != "rejected":
        issues.append(
            f"[{tid}] metadata.rejection.rejected=true 但 status={trace.get('status')}")

    # 5. llm_call token 缺失
    for s in spans:
        if s.get("type") == "llm_call" and s.get("status") == "success":
            m = s.get("metrics") or {}
            if not (m.get("total_tokens") or m.get("prompt_tokens")):
                issues.append(f"[{tid}] llm_call span '{s.get('span_id')}' 无 token 用量")

    # 6. 归因覆盖率（仅 root 直接子级）
    total = trace.get("duration_ms") or 0
    root = next((s for s in spans if not s.get("parent_id")), None)
    if root and total > 1000:
        covered = sum(s.get("duration_ms", 0) for s in spans
                      if s.get("parent_id") == root.get("span_id"))
        ratio = 1 - min(covered, total) / total
        if ratio > 0.2:
            issues.append(
                f"[{tid}] 无归因耗时占比 {ratio:.0%}（总 {total}ms，覆盖 {covered}ms）")

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace 数据质量巡检")
    parser.add_argument("--limit", type=int, default=200, help="最多检查最近 N 条")
    parser.add_argument("--db", default=os.path.abspath(DEFAULT_DB), help="trace_store.db 路径")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"数据库不存在: {args.db}")
        return 1

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT data FROM trace_store ORDER BY created_at DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    conn.close()

    all_issues: list[str] = []
    parsed = 0
    for (data_str,) in rows:
        try:
            trace = json.loads(data_str)
        except Exception:
            all_issues.append("[?] JSON 解析失败（脏数据行）")
            continue
        parsed += 1
        all_issues.extend(lint_trace(trace))

    print(f"巡检完成: {parsed} 条 trace, 发现 {len(all_issues)} 个问题")
    for line in all_issues[:100]:
        print(f"  - {line}")
    if len(all_issues) > 100:
        print(f"  ... 另有 {len(all_issues) - 100} 条未展示")
    return 1 if all_issues else 0


if __name__ == "__main__":
    sys.exit(main())
