"""evaluation/online_sampler.py — 在线流量采样评测器（eval-closure B2）。

为什么存在：评测体系此前只有离线 golden 评测——线上真实流量（含用户负反馈、
失败终态、降级请求）没有任何 LLM-as-Judge 质量采样，问题靠用户投诉才发现。
本模块按比例抽取近期 trace，复用 ``evaluation/judge.py`` 打分，结果写既有
``ai.eval_run_records`` + ``ai.eval_run_samples``（迁移 056/075，零新表），
``trigger='online'``（对应管理端 runs 列表的 online 来源口径）。

设计原则（对齐 run_records.py / failure-degradation.md）：
- **采样参数留环境变量与代码**（治理边界裁决），不建管理端配置页；
- **judge 失败不吞**：judge_answer 失败返回 total=0.0（judge.py 契约），
  此处记 status='error' + judge_failed_count 显式暴露，不进均值、不假装通过；
- **预览不冒充全文**：trace_store 仅存 answer_preview；answer_len 不匹配时记
  status='skip' 并计入 judge_skipped_count，不用截断文本打分；
- **幂等**：run_id 按天固定（``online-YYYYMMDD``），重跑覆盖同键
  （records ON CONFLICT run_id / samples UNIQUE (run_id, case_id, evaluator)）；
- **软失败分层**：trace 源/DB 不可达 → skipped/warning 返回，不炸 beat 调度；
  单条 judge 失败只影响该样本，不中断整批。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from backend.shared.logger import logger

# ── 配置（env 直读不缓存：测试可 monkeypatch.setenv，beat 每日重读）──────────


@contextmanager
def _pooled_connection(config: dict[str, Any]) -> Iterator[Any]:
    """获取池化 DBAPI 连接，并显式提交/回滚及归还连接。"""
    from backend.infra.db import engine_for

    conn = engine_for(config).raw_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _enabled() -> bool:
    """采样总开关：默认关——judge 每条一次 LLM 调用，未经评估消耗 eval_gen 配额。"""
    return os.getenv("EVAL_ONLINE_SAMPLER_ENABLED", "false").strip().lower() in (
        "1", "true", "yes",
    )


def _sample_max() -> int:
    """单次最大采样数（硬封顶控成本；按比例抽足后截断）。"""
    try:
        return max(1, int(os.getenv("EVAL_ONLINE_SAMPLE_MAX", "20")))
    except ValueError:
        return 20


def _window_hours() -> int:
    """采样回看窗口（小时）。"""
    try:
        return max(1, int(os.getenv("EVAL_ONLINE_SAMPLE_WINDOW_HOURS", "24")))
    except ValueError:
        return 24


def _cutoff_text(hours: int) -> str:
    """窗口起点，与 trace_store/feedback 的 UTC 文本口径同构（字典序可比）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - hours * 3600))


# ── judge 口径 ────────────────────────────────────────────────────────────────

# 在线流量无 golden rubric，用固定通用 4 维标准（与 judge.py 评分维度一一对应）。
# completeness 不引用参考答案（在线无真值），按覆盖度评。
ONLINE_JUDGE_RUBRIC: dict[str, str] = {
    "completeness": "回答完整覆盖用户问题的各个部分，无关键遗漏",
    "faithfulness": "所有数字与事实可追溯到可靠来源，无编造或幻觉",
    "conciseness": "表述精炼，无冗余重复或无关内容",
    "citation_quality": "引用标注准确且与来源一致；无显式引用场景按事实可追溯性评",
}

# judge_total ≥ 3.0（5 分制 60 分及格线）判 pass；< 3.0 判 fail；0.0 = judge 失败。
_JUDGE_PASS_TOTAL = 3.0


def _p95(values: list[float]) -> float | None:
    """P95；样本 < 20 时取最大值（口径对齐 report/builder.py）。"""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(len(ordered) * 0.95)] if len(ordered) >= 20 else ordered[-1]


# ── 采样信号 ──────────────────────────────────────────────────────────────────


def _negative_trace_ids(cutoff: str) -> set[str]:
    """窗口内用户负反馈的 trace_id 集合（feedback 表 agent_business 库，软失败返空）。"""
    try:
        from backend.config.database import FEEDBACK_PG_CONFIG
        from backend.feedback.pg import _table

        with _pooled_connection(FEEDBACK_PG_CONFIG) as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT DISTINCT trace_id FROM {_table()} "
                "WHERE vote = 'negative' AND COALESCE(trace_id, '') <> '' "
                "AND created_at >= %s",
                (cutoff,),
            )
            return {str(r[0]) for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001 — 负反馈信号不可得时降级为无优先样本
        logger.warning(f"[online_sampler] 负反馈集合查询失败（降级）: {e}")
        return set()


def _priority(trace: dict, negatives: set[str]) -> int:
    """采样优先级（越小越优先）：负反馈 > 失败终态 > 降级 > 拒答 > 普通。

    信号全部来自 trace 自身已有事实（status / error / metadata），不新增埋点。
    """
    trace_id = str(trace.get("id", ""))
    if trace_id and trace_id in negatives:
        return 0
    status = str(trace.get("status", ""))
    if status in ("error", "failed", "cancelled") or trace.get("error"):
        return 1
    metadata = trace.get("metadata") or {}
    if status == "degraded" or metadata.get("degraded"):
        return 2
    if status == "rejected" or (metadata.get("rejection") or {}).get("rejected"):
        return 3
    return 4


_PRIORITY_LABELS = {0: "negative_feedback", 1: "failed", 2: "degraded", 3: "rejected", 4: "normal"}


def _judgeable(trace: dict) -> bool:
    """采样池至少要有问题；答案完整性在评分时单独判定并记录 skip。"""
    return bool(str(trace.get("question", "")).strip())


def _answer_is_complete(trace: dict) -> bool:
    """trace_store 只保存 answer_preview；仅长度证明预览就是全文时可评分。"""
    answer = str(trace.get("answer_preview", "") or "")
    try:
        answer_len = int(trace["answer_len"])
    except (KeyError, TypeError, ValueError):
        return False
    return answer_len == len(answer)


# ── 主流程 ────────────────────────────────────────────────────────────────────


def _sample_traces(cutoff: str) -> list[tuple[int, dict]]:
    """取窗口内可评 trace，按优先级排序后按比例抽样。

    抽样口径：min(max(1, round(窗口可评量 × rate)), SAMPLE_MAX)——比例驱动、
    硬封顶兜底；负反馈/失败终态天然排前（优先级排序即优先采样）。
    """
    from backend.observability.trace_store import get_trace_store

    traces = get_trace_store().list_since(cutoff, limit=5000)
    candidates: list[tuple[int, int, dict]] = []  # (priority, -created 排序由列表序保证)
    negatives = _negative_trace_ids(cutoff)
    for trace in traces:
        if not _judgeable(trace):
            continue
        candidates.append((_priority(trace, negatives), len(candidates), trace))
    # list_since 已按 created_at DESC 返回（新→旧），稳定排序保持同优先级新旧序
    candidates.sort(key=lambda item: item[0])
    if not candidates:
        return []
    rate = min(1.0, max(0.01, float(os.getenv("EVAL_ONLINE_SAMPLE_RATE", "0.05"))))
    target = min(max(1, round(len(candidates) * rate)), _sample_max())
    return [(p, t) for p, _seq, t in candidates[:target]]


def _judge_all(samples: list[tuple[int, dict]]) -> list[dict]:
    """仅完整答案进入 judge；失败记 error，缺完整答案记 skip。"""
    from backend.evaluation.judge import judge_answer

    rows: list[dict] = []
    for priority, trace in samples:
        trace_id = str(trace.get("id", ""))
        answer = str(trace.get("answer_preview", "") or "")
        if not _answer_is_complete(trace):
            rows.append({
                "case_id": trace_id,
                "priority": _PRIORITY_LABELS.get(priority, "normal"),
                "status": "skip",
                "judge_total": None,
                "judge_failed": False,
                "answer_skipped": True,
                "confidence": "",
                "reasoning": "无法恢复完整答案，未对预览文本评分",
                "scores": {},
                "workflow_name": str(trace.get("workflow_name", "")),
                "trace_duration_ms": int(trace.get("duration_ms") or 0),
                "trace_cost_cny": float(trace.get("cost_cny") or 0.0),
                "trace_degraded": str(trace.get("status", "")) == "degraded"
                or bool((trace.get("metadata") or {}).get("degraded")),
            })
            continue
        result = judge_answer(
            str(trace.get("question", "")),
            ONLINE_JUDGE_RUBRIC,
            answer,
        )
        failed = result.total <= 0.0
        rows.append({
            "case_id": trace_id,
            "priority": _PRIORITY_LABELS.get(priority, "normal"),
            "status": "error" if failed else ("pass" if result.total >= _JUDGE_PASS_TOTAL else "fail"),
            "judge_total": round(result.total, 4),
            "judge_failed": failed,
            "answer_skipped": False,
            "confidence": result.confidence,
            "reasoning": result.reasoning[:500],
            "scores": result.scores,
            "workflow_name": str(trace.get("workflow_name", "")),
            "trace_duration_ms": int(trace.get("duration_ms") or 0),
            "trace_cost_cny": float(trace.get("cost_cny") or 0.0),
            "trace_degraded": str(trace.get("status", "")) == "degraded"
            or bool((trace.get("metadata") or {}).get("degraded")),
        })
    return rows


def _aggregate(rows: list[dict]) -> dict[str, Any]:
    """run 级聚合。表现指标与 release_gate B1 新增三项同键名（口径单一）。"""
    scored = [r for r in rows if r["status"] in ("pass", "fail")]
    passed = [r for r in scored if r["status"] == "pass"]
    judge_failed_count = sum(1 for r in rows if r["judge_failed"])
    judge_skipped_count = sum(1 for r in rows if r["answer_skipped"])
    durations = [r["trace_duration_ms"] for r in rows if r["trace_duration_ms"] > 0]
    costs = [r["trace_cost_cny"] for r in rows]
    degraded = sum(1 for r in rows if r["trace_degraded"])
    by_priority: dict[str, int] = {}
    for r in rows:
        by_priority[r["priority"]] = by_priority.get(r["priority"], 0) + 1
    return {
        "sampled_count": len(rows),
        "judge_failed_count": judge_failed_count,
        "judge_skipped_count": judge_skipped_count,
        "judge_avg_total": round(sum(r["judge_total"] for r in scored) / len(scored), 4) if scored else None,
        "judge_pass_rate": round(len(passed) / len(scored), 4) if scored else None,
        # 表现阈值三项（release_gate DEFAULT_THRESHOLDS 同键，越小越好）
        "p95_latency_ms": _p95([float(d) for d in durations]),
        "cost_per_case_cny": round(sum(costs) / len(costs), 6) if costs else None,
        "degradation_rate": round(degraded / len(rows), 4) if rows else None,
        "by_priority": by_priority,
    }


def _persist(run_id: str, rows: list[dict], agg: dict[str, Any]) -> bool:
    """写 ai.eval_run_records（一行）+ ai.eval_run_samples（每采样一行）。

    run 级台账 upsert；样本明细在同一事务里先清旧 judge 行再写当前行集，
    避免同日重跑后残留已不属于当前 run 的样本。独立实现因 record_run 入参是
    EvalReport（离线 report 对象），在线采样无 report——不硬造对象，直接 SQL。
    软失败：返回 False 不抛。
    """
    import json

    from backend.config.database import OBS_DB_PG_CONFIG
    try:
        from backend.evaluation.run_records import (
            collect_model_binding_fingerprint,
            collect_prompt_snapshot,
        )

        with _pooled_connection(OBS_DB_PG_CONFIG) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO ai.eval_run_records (
                    run_id, module, mode, smoke, dataset_version, git_sha,
                    prompt_snapshot, model_binding_fingerprint,
                    trigger, triggered_by, metrics,
                    case_count, pass_count, pass_rate
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    metrics = EXCLUDED.metrics,
                    case_count = EXCLUDED.case_count,
                    pass_count = EXCLUDED.pass_count,
                    pass_rate = EXCLUDED.pass_rate,
                    updated_at = now()
                """,
                (
                    run_id, "all", "online", False, "{}", "",
                    json.dumps(collect_prompt_snapshot(), ensure_ascii=False),
                    collect_model_binding_fingerprint(),
                    "online", "celery_beat",
                    json.dumps(agg, ensure_ascii=False),
                    agg["sampled_count"],
                    sum(1 for r in rows if r["status"] == "pass"),
                    agg["judge_pass_rate"],
                ),
            )
            cur.execute(
                "DELETE FROM ai.eval_run_samples "
                "WHERE run_id = %s AND evaluator = %s",
                (run_id, "judge"),
            )
            cur.executemany(
                """
                INSERT INTO ai.eval_run_samples
                    (run_id, case_id, evaluator, status, metrics, error_stage)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, case_id, evaluator) DO UPDATE SET
                    status = EXCLUDED.status,
                    metrics = EXCLUDED.metrics,
                    error_stage = EXCLUDED.error_stage,
                    updated_at = now()
                """,
                [
                    (
                        run_id, r["case_id"], "judge", r["status"],
                        json.dumps({
                            "judge_total": r["judge_total"],
                            "scores": r["scores"],
                            "confidence": r["confidence"],
                            "reasoning": r["reasoning"],
                            "priority": r["priority"],
                            "workflow_name": r["workflow_name"],
                            "trace_duration_ms": r["trace_duration_ms"],
                            "trace_cost_cny": r["trace_cost_cny"],
                        }, ensure_ascii=False),
                        "judge_failed" if r["judge_failed"] else (
                            "answer_preview_incomplete" if r["answer_skipped"] else ""
                        ),
                    )
                    for r in rows
                ],
            )
        return True
    except Exception as e:  # noqa: BLE001 — 采样台账软失败（不炸 beat）
        logger.warning(f"[online_sampler] eval_run_records/samples 写入失败: {e}")
        return False


def run_online_sampling() -> dict[str, Any]:
    """每日在线采样评测入口（B3 beat 调用；手动触发同入口）。

    返回摘要 dict（skipped/persisted 状态显式暴露，不抛异常——beat 调度
    靠返回值观察，失败语义见 failure-degradation.md 任务终态分级）。
    """
    if not _enabled():
        return {"skipped": "disabled", "run_id": ""}
    cutoff = _cutoff_text(_window_hours())
    try:
        sampled = _sample_traces(cutoff)
    except Exception as e:  # noqa: BLE001 — trace 源不可达：本轮放弃，不落脏数据
        logger.warning(f"[online_sampler] trace 采样失败（跳过本轮）: {e}")
        return {"skipped": "trace_store_unavailable", "run_id": ""}
    if not sampled:
        return {"skipped": "no_eligible_traces", "run_id": ""}

    run_id = f"online-{time.strftime('%Y%m%d')}"
    rows = _judge_all(sampled)
    agg = _aggregate(rows)
    agg["window_hours"] = _window_hours()
    agg["cutoff"] = cutoff
    persisted = _persist(run_id, rows, agg)
    logger.info(
        "[online_sampler] run=%s sampled=%s judge_failed=%s avg=%s persisted=%s",
        run_id, agg["sampled_count"], agg["judge_failed_count"],
        agg["judge_avg_total"], persisted,
    )
    return {"run_id": run_id, "skipped": "", "persisted": persisted, **agg}


__all__ = [
    "run_online_sampling",
    "ONLINE_JUDGE_RUBRIC",
]
