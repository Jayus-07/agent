"""unanswered.py — 业务拒答的未答问题旁路登记（2026-10-03 企业口径）

为什么存在：主图 RAG 未命中 / SQL 空结果此前只给用户一句模板拒答，系统侧
零留痕——同类问题永远重复拒答，知识缺口不可统计、不可运营。企业知识库
标准闭环是「未答问题落库 → 聚类 → 补内容 → 回流评测」；本模块是闭环的
第一环（落库），聚类与补录属知识运营侧，不进请求路径。

设计原则（与 security/events.py M9 同一口径）：
- **旁路软失败**：写入失败只记 warning 不抛错，登记永远不影响主流程；
- 上下文（user/tenant/trace）从 request_context / trace_collector 权威
  读取，不接受调用方自报身份；
- question 存原文（知识运营的输入就是原始问法；不含凭据类字段）。

不登记的范围：客服域拒答（CS 域有自己的知识运营闭环设计）、技术性错误
（服务不可用不是知识缺口）、安全拦截（归 ai.security_events）。
"""
from __future__ import annotations

import hashlib
import json

from backend.shared.logger import logger

# 登记来源（source 列 + Prometheus label 共用词表，禁止第四处手抄）
UNANSWERED_SOURCES = (
    "rag_miss",    # 知识库检索未命中 / EvidenceGate 拒答
    "sql_empty",   # SQL 查得到语法但查不到数据（业务性空结果）
)


def _question_hash(question: str) -> str:
    """归一化问题哈希（去空白 + 小写），供运营侧聚类去重。"""
    normalized = "".join((question or "").split()).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:32]


def current_request_context() -> dict[str, str]:
    """从权威上下文拼装身份（全部软失败，缺项空串）。

    旁路写入模块的公共 helper（unanswered / clarify_funnel 共用），
    与 security/events.py 的安全事件上下文同口径——上下文从
    request_context / trace_collector 读取，不接受调用方自报身份。
    """
    ctx = {"user_id": "", "tenant_id": "", "trace_id": "", "request_id": "",
           "session_id": ""}
    try:
        from backend.core.request_context import get_tool_tenant_id, get_tool_user_id

        ctx["user_id"] = get_tool_user_id() or ""
        ctx["tenant_id"] = get_tool_tenant_id() or ""
    except Exception:
        pass
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is not None:
            ctx["trace_id"] = str(getattr(trace, "id", "") or "")
            ctx["session_id"] = str(getattr(trace, "session_id", "") or "")
            ctx["request_id"] = str(getattr(trace, "request_id", "") or ctx["trace_id"])
    except Exception:
        pass
    return ctx


def _insert_row(row: dict) -> bool:
    """PG 写入（收口成独立函数：conftest autouse 在测试会话内替换，
    防止单测拒答路径向 ai.unanswered_questions 写垃圾行污染运营台账）。"""
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        conn.cursor().execute(
            """
            INSERT INTO ai.unanswered_questions (
                tenant_id, user_id, session_id, department, kb_id,
                trace_id, request_id, question, question_hash, source, detail
            ) VALUES (%(tenant_id)s, %(user_id)s, %(session_id)s,
                      %(department)s, %(kb_id)s, %(trace_id)s,
                      %(request_id)s, %(question)s, %(question_hash)s,
                      %(source)s, %(detail)s)
            """,
            row,
        )
        conn.commit()
    return True


def record_unanswered_question(
    question: str,
    *,
    source: str,
    detail: dict | None = None,
    department: str = "",
    kb_id: str = "",
) -> bool:
    """旁路登记一条未答问题（软失败，永不抛错）。

    Prometheus 计数在入口即增（漏斗反映拒答量本身，不因 PG 不可用失真）；
    PG 行是运营明细，写失败降级为只有计数。
    """
    if source not in UNANSWERED_SOURCES:
        logger.warning(f"[unanswered] 未知登记来源: {source}")
        return False
    if not (question or "").strip():
        return False

    try:
        from backend.observability.metrics import agent_unanswered_total

        agent_unanswered_total.labels(source=source).inc()
    except Exception:  # noqa: BLE001 — 指标旁路不阻断
        pass

    ctx = current_request_context()
    row = {
        "tenant_id": ctx["tenant_id"][:128],
        "user_id": ctx["user_id"][:128],
        "session_id": ctx["session_id"][:128],
        "department": (department or "")[:128],
        "kb_id": (kb_id or "")[:128],
        "trace_id": ctx["trace_id"],
        "request_id": ctx["request_id"],
        "question": (question or "")[:2000],
        "question_hash": _question_hash(question),
        "source": source,
        "detail": json.dumps(detail or {}, ensure_ascii=False, default=str),
    }
    try:
        return _insert_row(row)
    except Exception as e:  # noqa: BLE001 — 旁路软失败
        logger.warning(f"[unanswered] 登记写入失败（不影响主流程）: {e}")
        return False


__all__ = ["UNANSWERED_SOURCES", "record_unanswered_question",
           "current_request_context"]
