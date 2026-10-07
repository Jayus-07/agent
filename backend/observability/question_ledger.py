"""observability/question_ledger.py — 线上问题台账（2026-10-08 #13）

候选评测集的供给侧：各域用户问题统一落 ``ai.question_ledger``（迁移 080），
管理端 data-explorer「问题收集」tab 浏览、一键转候选评测集（复用
evaluation_datasets 的 register→approve→不可变版本管道，source_type=
"online_ledger"）。

写入范式照抄 security/events.py 的**旁路软失败**：同步函数、直插 PG、
except 全吞只 warning——台账断流不能影响任何业务主链路。不用 Celery 队列
（写入量 = 每请求 1 行；队列要在 queue_router 三张表登记且多一层 broker
耦合，收益为负，2026-10-08 拍板）。

domain 枚举与三分类的关系：travel/cs 直接来自 #12 的
``classify_trace_source``；planner = 主图 plan 支线（runtime_execution_mode
= plan 的 AI 助手流量）细分；sql 来自 NL2SQL 端点；rag 预留（独立链挂点
P2 接 trace_writer）。映射只在本模块 ``_domain_for_trace``，禁止第二份。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.shared.logger import logger

QUESTION_MAX_LEN = 500
ANSWER_MAX_LEN = 300
DEDUP_WINDOW_HOURS = 24

#: 台账域枚举（与迁移 080 的 CHECK 约束一致；增长 = 新迁移）
DOMAINS = ("travel", "sql", "planner", "cs", "rag", "ai_assistant")

STATUS_VALUES = ("pending", "accepted", "dismissed")

#: trace 三分类 → 台账域（None = 不收：rag 独立链 P2、selection_funnel 出域）
_SOURCE_TO_DOMAIN = {
    "travel": "travel",
    "cs": "cs",
    "ai_assistant": "ai_assistant",
}


def question_hash(question: str) -> str:
    """规范化问题的稳定哈希（去重键；截断在哈希之前保证语义一致）。"""
    normalized = " ".join((question or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _connect():
    """PG 连接入口（测试在此打桩；生产 = OBS 库，与 trace_summary 同层）。"""
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    return engine_for(OBS_DB_PG_CONFIG).raw_connection()


def record_question(
    *,
    domain: str,
    question: str,
    answer_summary: str = "",
    user_id: str = "",
    session_id: str = "",
    tenant_id: str = "default",
    trace_id: str = "",
    source: str = "chat",
) -> bool:
    """旁路记一条线上问题（软失败，永不抛错）。

    question 必须是**调用方已做 PII 掩码**的文本——本模块只做截断与
    哈希，不做第二次脱敏口径（G2：掩码唯一出口在 shared/pii_mask）。
    """
    if domain not in DOMAINS:
        logger.warning(f"[question_ledger] 未知 domain={domain!r}，丢弃")
        return False
    text = (question or "").strip()
    if not text:
        return False
    text = text[:QUESTION_MAX_LEN]
    summary = (answer_summary or "").strip()[:ANSWER_MAX_LEN]
    qhash = question_hash(text)

    try:
        with _connect() as conn:
            cur = conn.cursor()
            # 同域同问题 24h 只记一条：压测/重复提问不刷屏（去重键=哈希）
            cur.execute(
                """
                SELECT 1 FROM ai.question_ledger
                WHERE question_hash = %s AND domain = %s
                  AND created_at > NOW() - make_interval(hours => %s)
                LIMIT 1
                """,
                (qhash, domain, DEDUP_WINDOW_HOURS),
            )
            if cur.fetchone():
                return False
            cur.execute(
                """
                INSERT INTO ai.question_ledger (
                    tenant_id, user_id, session_id, domain, question,
                    question_hash, answer_summary, trace_id, source
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    (tenant_id or "default")[:128],
                    (user_id or "")[:128],
                    (session_id or "")[:128],
                    domain,
                    text,
                    qhash,
                    summary,
                    (trace_id or "")[:64],
                    (source or "chat")[:32],
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — 旁路软失败
        logger.warning(f"[question_ledger] 写入失败（不影响主流程）: {e}")
        return False


def record_question_from_trace(
    trace: Any,
    *,
    domain: str | None = None,
    question: str | None = None,
    answer_summary: str | None = None,
    source: str = "chat",
) -> bool:
    """trace 收口处的台账适配器（runner/travel 挂点共用）。

    - domain 缺省时由 #12 的三分类推导（selection_funnel/rag_agent 不收）；
    - 主图 plan 支线细分为 planner（runtime_execution_mode=plan）；
    - 合成问题（守卫拦截的占位文本 `[xxx]`）不收——那不是用户原话；
    - question/answer_summary 覆盖参数给**入口未掩码**的挂点用（主图
      trace.question 是明文，挂点必须先过 shared/pii_mask 再传进来——
      travel 入口已掩码，走默认读取）。
    """
    text = question if question is not None else str(getattr(trace, "question", "") or "")
    text = text.strip()
    if not text or text.startswith("["):
        return False
    resolved = domain
    if resolved is None:
        from backend.observability.trace_source import classify_trace_source

        tags = getattr(trace, "tags", None) or {}
        src = classify_trace_source(
            str(getattr(trace, "workflow_name", "") or ""), tags)
        resolved = _SOURCE_TO_DOMAIN.get(src)
        if resolved is None:
            return False
        if (resolved == "ai_assistant"
                and str(tags.get("runtime_execution_mode") or "") == "plan"):
            resolved = "planner"
    tags = getattr(trace, "tags", None) or {}
    if answer_summary is None:
        answer_summary = str(getattr(trace, "answer_preview", "") or "")
    return record_question(
        domain=resolved,
        question=text,
        answer_summary=answer_summary,
        user_id=str(tags.get("user_id") or ""),
        session_id=str(getattr(trace, "session_id", "") or ""),
        tenant_id=str(tags.get("tenant_id") or "default"),
        trace_id=str(getattr(trace, "id", "") or ""),
        source=source,
    )


# ── 管理端读/处置（路由层薄封装，逻辑在此便于单测）──────────────────


def list_questions(
    *,
    domain: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页列表（管理端「问题收集」tab 数据源）。"""
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    where: list[str] = []
    params: list[Any] = []
    if domain:
        where.append("domain = %s")
        params.append(domain)
    if status:
        where.append("status = %s")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) FROM ai.question_ledger {where_sql}", params)
            total = int((cur.fetchone() or [0])[0])
            cur.execute(
                f"""
                SELECT id, tenant_id, user_id, session_id, domain, question,
                       answer_summary, trace_id, source, status, created_at
                FROM ai.question_ledger {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, page_size, (page - 1) * page_size),
            )
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, row)) for row in cur.fetchall()]
        return {"items": items, "total": total, "page": page,
                "page_size": page_size}
    except Exception as e:  # noqa: BLE001 — 读取失败按空页降级
        logger.warning(f"[question_ledger] 列表读取失败: {e}")
        return {"items": [], "total": 0, "page": page, "page_size": page_size}


def set_status(question_id: int, status: str) -> bool:
    """运营状态机：pending → accepted / dismissed（幂等，可重复执行）。"""
    if status not in ("accepted", "dismissed"):
        return False
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE ai.question_ledger SET status = %s WHERE id = %s",
                (status, int(question_id)),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[question_ledger] 状态更新失败: {e}")
        return False


def to_candidates(
    ids: list[int],
    *,
    reviewer: str = "evaluation-platform",
) -> dict[str, Any]:
    """批量转候选评测集：accepted 化 + register_dataset_candidate。

    复用既有候选管道（source_type="online_ledger"），approve 后由
    _stage_candidate 落不可变版本目录——评测集侧零新建。
    """
    from backend.app.api.routes.evaluation_datasets import (
        register_dataset_candidate,
    )

    converted: list[dict[str, Any]] = []
    skipped: list[int] = []
    for qid in ids:
        try:
            with _connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT domain, question, answer_summary, user_id, trace_id
                    FROM ai.question_ledger WHERE id = %s
                    """,
                    (int(qid),),
                )
                row = cur.fetchone()
            if not row:
                skipped.append(int(qid))
                continue
            domain, question, summary, user_id, trace_id = (
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]))
            candidate = register_dataset_candidate(
                candidate_id=f"online-ledger-{qid}",
                module=domain,
                question=question,
                expected={},
                metadata={
                    "ledger_id": int(qid),
                    "domain": domain,
                    "answer_summary": summary,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "origin": "online_ledger",
                },
                source_type="online_ledger",
                redacted=True,  # 台账存的是掩码后文本，approve 流程要求该标记
                owner=reviewer,
            )
            set_status(int(qid), "accepted")
            converted.append({
                "ledger_id": int(qid),
                "candidate_id": candidate.candidate_id,
            })
        except Exception as e:  # noqa: BLE001 — 单条失败不阻断批量
            logger.warning(f"[question_ledger] 转候选失败 id={qid}: {e}")
            skipped.append(int(qid))
    return {"converted": converted, "skipped": skipped}


def _stats_by_domain(hours: int = 24) -> dict[str, int]:
    """近 N 小时各域收集量（管理端 tab 头计数）。"""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT domain, COUNT(*) FROM ai.question_ledger
                WHERE created_at > NOW() - make_interval(hours => %s)
                GROUP BY domain
                """,
                (max(1, int(hours)),),
            )
            return {str(r[0]): int(r[1]) for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[question_ledger] 统计失败: {e}")
        return {}


__all__ = [
    "DOMAINS",
    "STATUS_VALUES",
    "QUESTION_MAX_LEN",
    "question_hash",
    "record_question",
    "record_question_from_trace",
    "list_questions",
    "set_status",
    "to_candidates",
    "_stats_by_domain",
]
