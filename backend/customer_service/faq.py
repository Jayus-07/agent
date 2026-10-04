"""customer_service/faq.py — FAQ 双轨精准匹配层（C3/C4/C5，2026-10-04）。

三段式检索的第一段（演进分析 §4.5b）：FAQ 精准命中 → 直返答案，**不进
RAG/LLM**（TTFT 从秒级降到百毫级、成本≈0）；未命中落回既有文档 RAG 链。

企业做法取舍（明日上线口径）：
  - 匹配 = 规范化精确（question/variants 完全命中）+ 字符 bigram Jaccard
    相似度（进程内索引，零模型调用）——**确定性、P95 可控**；embedding 语义
    召回作为二阶段挂跟进卡（引入 API 往返会威胁 500ms 口径）；
  - 只匹配 published 且未过有效期（valid_until 按日粒度）的条目——
    fail-closed，与知识生命周期同语义；
  - 每次查询落 ai.cs_faq_query_log（C5 承接占比基线），命中累计 hit_count。
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

import psycopg2
import psycopg2.pool
import psycopg2.extras
from prometheus_client import Counter

# FAQ 层故障计数（G 告警数据源，2026-10-04）：匹配失败仅被
# knowledge/service.py 捕获后降级 RAG 链（fail-open），此前只有日志——
# 无指标则监控栈无法表达「FAQ 层错误率激增」。定义在本模块（生产者与
# 消费方都在 FAQ/客服知识面），app 进程 /metrics 与 worker multiproc
# 聚合端点均可采集。
cs_faq_layer_failures_total = Counter(
    "cs_faq_layer_failures_total",
    "FAQ 精准匹配层故障降级 RAG 链次数",
)

# 匹配阈值：score = max(bigram Jaccard, 包含度|A∩B|/|FAQ|)。
# 中文改写（加"的/呢"、语序调整）会把 Jaccard 压到 0.3 左右，包含度对
# 「长问法命中考短标准问」更稳；0.5 = 召回与误召回的保守折中
# （宁可落回 RAG 链，不冒错答风险）。
MATCH_SCORE_THRESHOLD = 0.42
# 包含度腿的最小交集守卫：只共享 1-2 个 bigram 的部分相关问题
# （如「退货流程」vs「退货运费」）不得因包含度过线而误召回
MATCH_MIN_INTERSECTION = 3
_INDEX_TTL_S = 60.0

_PUNCT_RE = re.compile(r"[\s，。！？!?\?、；;：:·…\-—_/\\\\()（）\[\]【】\"'“”‘’]+")


# 疑问功能词：相似度打分前剔除——否则「怎么办/什么/多久」这类功能性
# bigram 主导得分，造成「密码忘了怎么办」误命中「工牌丢了怎么办」
# （2026-10-04 实测错答案例）；精确匹配仍用全量规范化指纹不受影响
_STOPWORDS_RE = re.compile(
    r"怎么办|怎么样|什么样|是什么|多久|多少|哪些|哪里|有没有|能不能|"
    r"是不是|如何|可以|请问|帮我|谢谢|还是|的话|了吗|吗"
)
_PARTICLE_RE = re.compile(r"[的呢吧啊呀哦]")


def normalize_question(text: str) -> str:
    """问题规范化指纹：去空白/标点、转小写（幂等去重与精确匹配共用）。"""
    return _PUNCT_RE.sub("", (text or "").lower())


def _bigrams(text: str) -> set[str]:
    """内容 bigram：规范化 → 剔疑问功能词 → 剔语气助词 → 邻接字对。"""
    norm = _PARTICLE_RE.sub("", _STOPWORDS_RE.sub("", normalize_question(text)))
    return {norm[i:i + 2] for i in range(len(norm) - 1)} if len(norm) >= 2 else {norm}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def match_score(query_bigrams: set[str], faq_bigrams: set[str]) -> float:
    """改写鲁棒分：max(Jaccard, FAQ 侧包含度)。

    包含度 = 交集/FAQ bigram 数——用户长问法盖住考短标准问时给高分；
    语序调整/插入助词主要稀释 Jaccard，对包含度影响小。
    交集 < MATCH_MIN_INTERSECTION 时包含度腿不计（防部分相关误召回）。
    """
    if not query_bigrams or not faq_bigrams:
        return 0.0
    inter = len(query_bigrams & faq_bigrams)
    jac = inter / (len(query_bigrams) + len(faq_bigrams) - inter)
    if inter < MATCH_MIN_INTERSECTION:
        return jac
    contain = inter / len(faq_bigrams)
    return max(jac, contain)


# 进程内连接池：vpnkit 回环单次 connect ≈2s，FAQ 每问要 3 次取用，
# 不池化时延全烧在握手上年（P95 500ms 口径不可能达标）
_pool: psycopg2.pool.SimpleConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from backend.config.database import DOC_REGISTRY_PG_CONFIG
                _pool = psycopg2.pool.SimpleConnectionPool(
                    1, 4, **DOC_REGISTRY_PG_CONFIG, connect_timeout=5)
    return _pool


@contextmanager
def faq_conn():
    """池化生产连接（agent_memory，ai schema）；用完归还，坏连接丢弃重建。

    connect_timeout=5：宿主 vpnkit 回环偶发 connect 挂死（AGENTS.md
    已知环境病），不设超时会无限阻塞问答主链路。
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.cursor().execute("SELECT 1")  # 探活：池内连接可能已被服务端断开
        conn.rollback()
    except Exception:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


_FAQ_DDL = """
CREATE TABLE IF NOT EXISTS ai.cs_faq (
    id          BIGSERIAL PRIMARY KEY,
    faq_key     TEXT NOT NULL UNIQUE,
    question    TEXT NOT NULL,
    variants    TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    kb_refs     TEXT NOT NULL DEFAULT '',
    valid_until TEXT,
    hit_count   INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

_QUERY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS ai.cs_faq_query_log (
    id           BIGSERIAL PRIMARY KEY,
    question     TEXT NOT NULL,
    matched      BOOLEAN NOT NULL,
    faq_id       BIGINT,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)"""


@dataclass
class FAQEntry:
    faq_id: int
    question: str
    variants: list[str]
    answer: str
    kb_refs: str
    valid_until: str


@dataclass
class FAQMatch:
    faq_id: int
    question: str
    answer: str
    score: float
    matched_by: str  # exact | variant | jaccard


class FAQStore:
    """FAQ 存储 + 进程内匹配索引（conn_factory 可注入，测试 mock PG 边界）。"""

    def __init__(self, conn_factory=None):
        self._conn_factory = conn_factory or faq_conn
        # RLock：_get_index→_build_index→_ensure_tables 同线程重入
        # （Lock 非重入会造成首次 match 自死锁，2026-10-04 实测修复）
        self._lock = threading.RLock()
        self._ensured = False
        self._index_built_at = 0.0
        self._index: dict[str, FAQEntry] = {}      # 规范化问题/变体 → 条目
        self._entries: list[tuple[FAQEntry, set[str]]] = []  # (条目, bigram集)

    # ── 存储 ────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        if self._ensured:
            return
        with self._lock:
            if self._ensured:
                return
            with self._conn_factory() as conn:
                with conn.cursor() as cur:
                    cur.execute(_FAQ_DDL)
                    cur.execute(_QUERY_LOG_DDL)
            self._ensured = True

    def upsert_faq(
        self, question: str, answer: str, *,
        variants: list[str] | None = None, status: str = "published",
        kb_refs: str = "", valid_until: str | None = None,
    ) -> int:
        """幂等写入（faq_key=规范化问题）；返回 faq id。"""
        self._ensure_tables()
        key = normalize_question(question)
        if not key:
            raise ValueError("question 规范化后为空")
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai.cs_faq (faq_key, question, variants, answer, status, kb_refs, valid_until) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (faq_key) DO UPDATE SET question=EXCLUDED.question, "
                    "variants=EXCLUDED.variants, answer=EXCLUDED.answer, status=EXCLUDED.status, "
                    "kb_refs=EXCLUDED.kb_refs, valid_until=EXCLUDED.valid_until, updated_at=now() "
                    "RETURNING id",
                    (key, question, "\n".join(variants or []), answer, status,
                     kb_refs, valid_until),
                )
                row = cur.fetchone()
        self.invalidate_index()
        return row[0]

    def load_published(self) -> list[FAQEntry]:
        self._ensure_tables()
        today = date.today().isoformat()
        with self._conn_factory() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, question, variants, answer, kb_refs, valid_until FROM ai.cs_faq "
                    "WHERE status = 'published' AND (valid_until IS NULL OR valid_until = '' OR valid_until >= %s)",
                    (today,),
                )
                rows = cur.fetchall()
        out = []
        for r in rows:
            out.append(FAQEntry(
                faq_id=r["id"], question=r["question"],
                variants=[v for v in (r["variants"] or "").split("\n") if v.strip()],
                answer=r["answer"], kb_refs=r["kb_refs"] or "",
                valid_until=r["valid_until"] or "",
            ))
        return out

    def record_hit(self, faq_id: int) -> None:
        self._ensure_tables()
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE ai.cs_faq SET hit_count = hit_count + 1 WHERE id = %s", (faq_id,))

    def log_query(self, question: str, matched: bool, faq_id: int | None, latency_ms: int) -> None:
        """C5 承接占比基线：全量查询结果落台账（命中与否都记）。"""
        self._ensure_tables()
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ai.cs_faq_query_log (question, matched, faq_id, latency_ms) "
                    "VALUES (%s, %s, %s, %s)",
                    (question[:300], matched, faq_id, int(latency_ms)),
                )

    def stats(self) -> dict:
        """C5 基线口径：published 数量 + 近期承接占比。"""
        self._ensure_tables()
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM ai.cs_faq WHERE status='published'")
                published = cur.fetchone()[0]
                cur.execute(
                    "SELECT count(*), count(*) FILTER (WHERE matched) FROM ai.cs_faq_query_log "
                    "WHERE created_at >= now() - interval '7 days'"
                )
                total, hits = cur.fetchone()
        return {
            "published": published,
            "queries_7d": total,
            "hits_7d": hits,
            "hit_ratio_7d": round(hits / total, 4) if total else None,
        }

    # ── 匹配索引 ────────────────────────────────────────────

    def invalidate_index(self) -> None:
        self._index_built_at = 0.0

    def _build_index(self) -> None:
        entries = self.load_published()
        index: dict[str, FAQEntry] = {}
        with_bigrams: list[tuple[FAQEntry, set[str]]] = []
        for e in entries:
            for text in [e.question, *e.variants]:
                key = normalize_question(text)
                if key and key not in index:
                    index[key] = e
            with_bigrams.append((e, _bigrams(e.question)))
        self._index = index
        self._entries = with_bigrams
        self._index_built_at = time.monotonic()

    def _get_index(self):
        if time.monotonic() - self._index_built_at > _INDEX_TTL_S:
            with self._lock:
                if time.monotonic() - self._index_built_at > _INDEX_TTL_S:
                    self._build_index()
        return self._index, self._entries

    def match(self, question: str) -> FAQMatch | None:
        """FAQ 精准匹配：规范化精确（question/variants）→ bigram Jaccard。

        未命中返回 None（调用方落回 RAG 链）。
        """
        if not question or not question.strip():
            return None
        t0 = time.monotonic()
        index, entries = self._get_index()
        norm = normalize_question(question)

        matched: FAQMatch | None = None
        exact = index.get(norm)
        if exact is not None:
            matched = FAQMatch(exact.faq_id, exact.question, exact.answer, 1.0, "exact")
        else:
            q_bigrams = _bigrams(question)
            best_entry, best_score = None, 0.0
            for entry, e_bigrams in entries:
                score = match_score(q_bigrams, e_bigrams)
                if score > best_score:
                    best_entry, best_score = entry, score
            if best_entry is not None and best_score >= MATCH_SCORE_THRESHOLD:
                matched = FAQMatch(best_entry.faq_id, best_entry.question,
                                   best_entry.answer, round(best_score, 4), "jaccard")

        latency_ms = int((time.monotonic() - t0) * 1000)
        if matched:
            self.record_hit(matched.faq_id)
        try:
            self.log_query(question, matched is not None,
                           matched.faq_id if matched else None, latency_ms)
        except Exception:
            pass  # 台账旁路失败不阻断问答
        return matched

    def match_candidates(self, question: str, k: int = 2,
                         min_score: float = 0.30) -> list[FAQMatch]:
        """拒答自救候选（任务卡 V4）：返回 top-k 相似条目，供「您是不是想问」。

        与 match() 同索引同打分、只放低阈值（主匹配 0.42 / 推荐 0.30）；
        只读——不记台账不记命中（该问题的 miss 已由 match() 落账，推荐
        本身不算承接）。精确命中时返回空（主匹配就该接住，不走推荐）。
        """
        if not question or not question.strip():
            return []
        index, entries = self._get_index()
        if normalize_question(question) in index:
            return []
        q_bigrams = _bigrams(question)
        scored: list[tuple[FAQEntry, float]] = []
        for entry, e_bigrams in entries:
            s = match_score(q_bigrams, e_bigrams)
            if s >= min_score:
                scored.append((entry, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            FAQMatch(e.faq_id, e.question, e.answer, round(s, 4), "jaccard")
            for e, s in scored[:k]
        ]


_store: FAQStore | None = None


def get_faq_store() -> FAQStore:
    global _store
    if _store is None:
        _store = FAQStore()
    return _store


def reset_faq_store_for_tests() -> None:
    global _store
    _store = None
