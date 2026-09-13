"""tool_approval.py — 写操作工具审批门（human-in-the-loop）

问题背景:
  send_email / export_csv / data_collection / competitor(write) 等工具会
  产生对外副作用（发邮件/写库/改监控列表），但此前 LLM 传参即可直接执行，
  无权限校验、无审批 —— 企业环境不可接受。

方案（审批单模型，不依赖 LangGraph interrupt）:
  1. 写操作工具执行前调用 ensure_approved()；
  2. TOOL_APPROVAL_MODE=auto → 直接放行（仅本地调试）；
  3. 存在 TTL 内已批准的同指纹审批单 → 标记已执行并放行（审批后重试）；
  4. 否则创建 pending 审批单，工具返回待审批提示字符串（不抛异常，
     Planner/Reporter 能自然把"等待审批"转述给用户）。
  管理端: /api/approvals 列表/批准/驳回（app/api/routes/approvals.py）。

指纹（fingerprint）: sha256(tool|action|detail)。detail 必须只含稳定字段
（不含时间戳），保证"批准后重试相同操作"命中同指纹。

存储: PostgreSQL ai.tool_approval_requests（agent_business 库）。
连接失败降级进程内存存储（单进程审批可用；多 worker 部署需修复 DB）。
"""
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

from backend.config import TOOL_APPROVAL_MODE, TOOL_APPROVAL_TTL_SECONDS
from backend.shared.logger import logger

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTED = "executed"


# =====================================================
# 审批单存储（PG 主 + 内存降级）
# =====================================================

class _MemoryApprovalStore:
    """进程内存存储 — DB 不可用时的降级实现。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}

    def create(self, tool_name: str, action: str, fingerprint: str,
               detail: dict, user_id: str) -> dict:
        rid = str(uuid.uuid4())
        rec = {
            "id": rid, "tool_name": tool_name, "action": action,
            "fingerprint": fingerprint, "detail": detail, "user_id": user_id,
            "status": STATUS_PENDING, "reviewer": None, "reason": None,
            "created_at": _now_iso(), "decided_at": None, "executed_at": None,
        }
        with self._lock:
            self._records[rid] = rec
        return dict(rec)

    def find_pending_by_fingerprint(self, fingerprint: str) -> dict | None:
        with self._lock:
            for rec in self._records.values():
                if rec["fingerprint"] == fingerprint and rec["status"] == STATUS_PENDING:
                    return dict(rec)
        return None

    def consume_approved(self, fingerprint: str, ttl_seconds: int) -> dict | None:
        """取 TTL 内已批准的同指纹审批单并标记已执行；无则 None。"""
        now = datetime.now(timezone.utc)
        with self._lock:
            candidates = [
                r for r in self._records.values()
                if r["fingerprint"] == fingerprint and r["status"] == STATUS_APPROVED
                and r["decided_at"] and _parse_iso(r["decided_at"])
                and now - _parse_iso(r["decided_at"]) < timedelta(seconds=ttl_seconds)
            ]
            if not candidates:
                return None
            rec = max(candidates, key=lambda r: r["decided_at"])
            rec["status"] = STATUS_EXECUTED
            rec["executed_at"] = _now_iso()
            return dict(rec)

    def decide(self, request_id: str, approve: bool, reviewer: str,
               reason: str) -> dict | None:
        with self._lock:
            rec = self._records.get(request_id)
            if rec is None or rec["status"] != STATUS_PENDING:
                return None
            rec["status"] = STATUS_APPROVED if approve else STATUS_REJECTED
            rec["reviewer"] = reviewer
            rec["reason"] = reason
            rec["decided_at"] = _now_iso()
            return dict(rec)

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            recs = [dict(r) for r in self._records.values()]
        recs.sort(key=lambda r: r["created_at"], reverse=True)
        if status:
            recs = [r for r in recs if r["status"] == status]
        return recs[:limit]


class _PgApprovalStore:
    """PostgreSQL 存储（ai.tool_approval_requests，agent_business 库）。"""

    def __init__(self):
        import psycopg
        from backend.config.database import BUSINESS_DB_CONFIG
        c = BUSINESS_DB_CONFIG
        dsn = (f"postgresql://{c['user']}:{c['password']}"
               f"@{c['host']}:{c['port']}/{c['dbname']}")
        # autocommit：审批决策需即时可见（官方建议，同 checkpointer）
        self._conn = psycopg.Connection.connect(dsn, autocommit=True)
        self._ensure_table()

    def _ensure_table(self):
        """建表幂等自愈（生产仍以 migrations/007_tool_approval.sql 为准）。"""
        with self._conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ai.tool_approval_requests (
                    id UUID PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL,
                    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                    user_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer TEXT,
                    reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    decided_at TIMESTAMPTZ,
                    executed_at TIMESTAMPTZ
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_approval_status
                ON ai.tool_approval_requests(status, created_at DESC)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_tool_approval_fingerprint
                ON ai.tool_approval_requests(fingerprint)
            """)
            # 同指纹仅一张 pending 单（幂等建单）；partial index，executed/rejected
            # 历史不受限。并发建单冲突由 create() 捕获后复用现有单兜底。
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tool_approval_fingerprint_pending
                ON ai.tool_approval_requests(fingerprint) WHERE status = 'pending'
            """)

    def create(self, tool_name, action, fingerprint, detail, user_id) -> dict:
        rid = str(uuid.uuid4())
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai.tool_approval_requests
                       (id, tool_name, action, fingerprint, detail, user_id, status, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, now())""",
                    (rid, tool_name, action, fingerprint,
                     json.dumps(detail, ensure_ascii=False), user_id, STATUS_PENDING),
                )
        except Exception:
            # 并发下同指纹 pending 已存在（uq_tool_approval_fingerprint_pending）：
            # 复用现有单，不重复建单
            existing = self.find_pending_by_fingerprint(fingerprint)
            if existing is not None:
                return existing
            raise
        return self.get(rid) or {
            "id": rid, "tool_name": tool_name, "action": action,
            "fingerprint": fingerprint, "detail": detail, "user_id": user_id,
            "status": STATUS_PENDING, "reviewer": None, "reason": None,
            "created_at": _now_iso(), "decided_at": None, "executed_at": None,
        }

    def get(self, rid: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT * FROM ai.tool_approval_requests WHERE id = %s", (rid,))
            row = cur.fetchone()
            # cols 必须在 with 块内读取：块外游标已关闭，description 在
            # 连接池/游标回收场景下为 None（2026-09-13 全量测试暴露；
            # 同文件 find_pending_by_fingerprint 是块内读取的正确写法）
            cols = [d[0] for d in cur.description] if row is not None else []
        if row is None:
            return None
        return _row_to_record(dict(zip(cols, row)))

    def find_pending_by_fingerprint(self, fingerprint: str) -> dict | None:
        """同指纹 pending 去重：返回最新一条，避免重复审批单。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM ai.tool_approval_requests
                   WHERE fingerprint = %s AND status = %s
                   ORDER BY created_at DESC LIMIT 1""",
                (fingerprint, STATUS_PENDING),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
        return _row_to_record(dict(zip(cols, row)))

    def consume_approved(self, fingerprint: str, ttl_seconds: int) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE ai.tool_approval_requests
                   SET status = %s, executed_at = now()
                   WHERE id = (
                       SELECT id FROM ai.tool_approval_requests
                       WHERE fingerprint = %s AND status = %s
                         AND decided_at > now() - (%s || ' seconds')::interval
                       ORDER BY decided_at DESC LIMIT 1
                       FOR UPDATE SKIP LOCKED
                   )
                   RETURNING *""",
                (STATUS_EXECUTED, fingerprint, STATUS_APPROVED, str(ttl_seconds)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
        return _row_to_record(dict(zip(cols, row)))

    def decide(self, request_id: str, approve: bool, reviewer: str,
               reason: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE ai.tool_approval_requests
                   SET status = %s, reviewer = %s, reason = %s, decided_at = now()
                   WHERE id = %s AND status = %s
                   RETURNING *""",
                (STATUS_APPROVED if approve else STATUS_REJECTED,
                 reviewer, reason, request_id, STATUS_PENDING),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
        return _row_to_record(dict(zip(cols, row)))

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self._conn.cursor() as cur:
            if status:
                cur.execute(
                    """SELECT * FROM ai.tool_approval_requests WHERE status = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (status, limit),
                )
            else:
                cur.execute(
                    """SELECT * FROM ai.tool_approval_requests
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [_row_to_record(dict(zip(cols, r))) for r in rows]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _row_to_record(row: dict) -> dict:
    """PG 行 → API dict（UUID/时间统一字符串，detail 解析 JSON）。"""
    rec = dict(row)
    rec["id"] = str(rec.get("id"))
    for k in ("created_at", "decided_at", "executed_at"):
        if rec.get(k) is not None and not isinstance(rec[k], str):
            rec[k] = rec[k].isoformat()
    if isinstance(rec.get("detail"), str):
        try:
            rec["detail"] = json.loads(rec["detail"])
        except (json.JSONDecodeError, TypeError):
            pass
    return rec


_store = None
_store_lock = threading.Lock()
_store_degraded = False


def get_store():
    """审批存储单例：PG 优先，连接失败降级内存（只告警一次）。"""
    global _store, _store_degraded
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        try:
            _store = _PgApprovalStore()
            logger.info("[ToolApproval] 存储就绪 (PostgreSQL ai.tool_approval_requests)")
        except Exception as e:
            _store = _MemoryApprovalStore()
            _store_degraded = True
            logger.warning(f"[ToolApproval] PG 存储初始化失败,降级内存存储: {e}")
        return _store


def is_degraded() -> bool:
    """存储是否处于内存降级态（供 /health 或审批 API 暴露）。"""
    return _store_degraded


# =====================================================
# 工具层入口：ensure_approved
# =====================================================

def _fingerprint(tool_name: str, action: str, detail: dict) -> str:
    canonical = json.dumps(
        {"tool": tool_name, "action": action, "detail": detail},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_approved(tool_name: str, action: str,
                    user_id: str = "", detail: dict | None = None) -> str | None:
    """写操作执行前的审批门。

    Returns:
        None — 放行（auto 模式或命中 TTL 内已批准审批单）；
        str  — 待审批提示（工具直接把它作为返回值，Planner/Reporter 会转述给用户）。
    """
    detail = detail or {}

    if TOOL_APPROVAL_MODE == "auto":
        logger.info(f"[ToolApproval] auto 模式放行: {tool_name}.{action} (user={user_id})")
        return None

    fp = _fingerprint(tool_name, action, detail)
    store = get_store()

    # 1) 审批通过后的重试：消费 TTL 内 approved 单 → 放行
    consumed = store.consume_approved(fp, TOOL_APPROVAL_TTL_SECONDS)
    if consumed is not None:
        logger.info(f"[ToolApproval] 审批单已批准,放行执行: {tool_name}.{action} "
                    f"(id={consumed['id']}, user={user_id})")
        return None

    # 2) 幂等：同指纹 pending 已存在则复用单号，不重复建单
    existing = store.find_pending_by_fingerprint(fp)
    if existing is not None:
        rid = existing["id"]
    else:
        rec = store.create(tool_name, action, fp, detail, user_id or "default")
        rid = rec["id"]
        logger.warning(f"[ToolApproval] 写操作待审批: {tool_name}.{action} "
                       f"(id={rid}, user={user_id or 'default'})")

    return (
        f"⏸ 该操作需要人工审批后执行（写操作安全策略）。\n\n"
        f"- 操作: `{tool_name}.{action}`\n"
        f"- 审批单号: `{rid}`\n"
        f"- 详情: {json.dumps(detail, ensure_ascii=False)}\n\n"
        f"管理员在审批管理页（或 `POST /api/approvals/{rid}/approve`）批准后，"
        f"重新发起相同操作即可执行（批准单 {TOOL_APPROVAL_TTL_SECONDS} 秒内有效）。"
    )


def list_requests(status: str | None = None, limit: int = 50) -> list[dict]:
    return get_store().list(status, limit)


def decide_request(request_id: str, approve: bool,
                   reviewer: str = "admin", reason: str = "") -> dict | None:
    """批准/驳回审批单。返回更新后的单据；单不存在或非 pending 返回 None。"""
    rec = get_store().decide(request_id, approve, reviewer, reason)
    if rec is not None:
        verb = "批准" if approve else "驳回"
        logger.warning(f"[ToolApproval] 审批{verb}: id={request_id} reviewer={reviewer}")
    return rec
