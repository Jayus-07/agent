"""CS QA Score（质检中心抽检评分）ORM model

Maps to ``customer_service.cs_qa_scores`` table (created by SQL migration
backend/sql/migrations/128_cs_qa_scores.sql，经 scripts/init_db.py 执行).

状态语义（cs-workstation-v2 P6）：
  status        pending（待评分）→ draft（草稿）→ scored（已提交，终态）
  source        ai（抽样+AI 预填创建）| human（人工提交评分）
  appeal_status none → appealing → upheld|overturned（不可回退，终态锁定）

查询/变更谓词收口在路由层（cs_qa.py）；UNIQUE(tenant_id, conversation_id)
是抽样去重的唯一事实源。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from backend.customer_service.models.conversation import CSBase


def _now():
    return datetime.now(timezone.utc)


class CSQAScore(CSBase):
    __tablename__ = "cs_qa_scores"
    __table_args__ = (
        Index(
            "idx_qa_score_tenant_status",
            "tenant_id",
            "status",
            "batch_date",
        ),
        Index(
            "idx_qa_score_agent",
            "tenant_id",
            "agent_id",
            "batch_date",
        ),
        {"schema": "customer_service"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), nullable=False)
    conversation_id = Column(String(64), nullable=False)
    agent_id = Column(String(64), nullable=False, comment="被检坐席")
    batch_date = Column(Date, nullable=False, comment="抽样批次（UTC 日期）")

    # ── 评分内容 ──
    source = Column(
        String(16), nullable=False, default="ai",
        comment="ai=抽样 AI 预填创建 | human=人工提交",
    )
    score_service = Column(SmallInteger, nullable=True, comment="服务规范 0-25")
    score_accuracy = Column(SmallInteger, nullable=True, comment="准确性 0-25")
    score_compliance = Column(SmallInteger, nullable=True, comment="合规 0-25")
    score_timeliness = Column(SmallInteger, nullable=True, comment="处理时效 0-25")
    total_score = Column(SmallInteger, nullable=True, comment="合计 0-100")
    verdict = Column(String(16), nullable=True, comment="pass|fail")
    note = Column(Text, nullable=True, comment="评语（扣分原因/改进建议）")
    status = Column(
        String(16), nullable=False, default="pending",
        comment="pending|draft|scored",
    )
    scored_by = Column(String(64), nullable=True)
    scored_at = Column(DateTime(timezone=True), nullable=True)

    # ── AI 预填建议（source=ai，人工终审）──
    ai_prefill = Column(
        JSONB, nullable=True,
        comment="AI 预填建议 {scores, total, verdict, note}",
    )

    # ── 申诉状态机（不可回退）──
    appeal_status = Column(
        String(16), nullable=False, default="none",
        comment="none|appealing|upheld|overturned",
    )
    appeal_reason = Column(Text, nullable=True)
    appeal_requested_by = Column(String(64), nullable=True)
    appeal_requested_at = Column(DateTime(timezone=True), nullable=True)
    appeal_decision = Column(String(16), nullable=True, comment="upheld|overturned")
    appeal_opinion = Column(Text, nullable=True, comment="终审意见")
    appeal_decided_by = Column(String(64), nullable=True)
    appeal_decided_at = Column(DateTime(timezone=True), nullable=True)
    final_score = Column(SmallInteger, nullable=True, comment="改判终审分")
    final_verdict = Column(String(16), nullable=True, comment="改判终审结论")

    # ── timestamps ──
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
