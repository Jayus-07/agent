"""MemoryDreamingService — dreaming 式后台整合（ADR-0012 第 2 期 D4）。

职责（D-4 裁决的最小可交付形态，逐用户批处理）：
  1. 碎片合并：unkeyed 高相似（≥ MEMORY_DREAMING_MERGE_SIM_THRESHOLD）同
     memory_type 碎片簇 → LLM 三分类（合并/矛盾/独立）→ 合并动作走
     supersede 版本链（碎片 is_active=False + invalid_at 双写 + superseded_by
     指向聚合行），聚合行只重组碎片已有信息，**不臆造新事实**。
  2. 矛盾失效：LLM 判定为矛盾陈述的簇 → 较弱/较旧行 deactivate（失效
     双写，无 supersede 回填——没有取代者）。
  3. 置信度强化：keyed 事实由 conflict REAFFIRMED 承担（同 key 同值只升
     不降）；unkeyed 印证需要新计数基建，本期不做（已在任务文档记录）。

豁免（D-2 安全红线）：
  - 内容命中 MEMORY_SAFETY_CONTENT_PATTERNS 的行永不参与合并/失效
    （漏判方向危害高，清单宁宽勿严）；
  - 簇内含 explicit 行整体跳过（用户显式资产不做机器改写，也不被合并
    降级为机器产物）；
  - keyed 行不进 dreaming（同 key 由唯一约束 + conflict supersede 治理）。

预算护栏：开关默认关 + 单次最多用户数 + 每用户簇数/行数上限；LLM 调用
按用户归因（memory-dreaming:<tenant>:<user>，scoped_request_budget）。

trace（D9）：任务级 root trace（workflow_name=memory_dreaming）+ root span
type=memory 承载动作计数（merged/contradictions/llm_calls/skipped——只有
计数与状态枚举，无记忆原文/PII/租户标识）。
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.config.memory import (
    MEMORY_DREAMING_ENABLED,
    MEMORY_DREAMING_MAX_CLUSTERS_PER_USER,
    MEMORY_DREAMING_MAX_FRAGMENTS_PER_USER,
    MEMORY_DREAMING_MAX_USERS_PER_RUN,
    MEMORY_DREAMING_MERGE_SIM_THRESHOLD,
    MEMORY_SAFETY_CONTENT_PATTERNS,
)
from backend.memory.models.memory import MemoryRecord
from backend.memory.repository.memory_repo import MemoryRepository
from backend.shared.logger import logger


def _cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 余弦（候选 ≤60 行两两计算，无需进 SQL/pgvector）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _is_safe_content(content: str) -> bool:
    """D-2 安全豁免：内容命中安全关键词即豁免（保守，宁宽勿严）。"""
    text = (content or "").lower()
    return any(p in text for p in MEMORY_SAFETY_CONTENT_PATTERNS)


class MemoryDreamingService:
    """逐用户批处理 unkeyed 碎片：合并/矛盾失效，动作全部走版本链。"""

    def __init__(self, repo_factory=None):
        # repo_factory 便于测试注入独立事务；缺省每用户一个 AsyncSessionLocal
        self._repo_factory = repo_factory

    # ── 主入口 ────────────────────────────────────────────────

    async def run(self, user_scope: tuple[str, str, str] | None = None) -> dict:
        """跑一轮整合。

        user_scope=None（beat 全量）：扫最旧 MEMORY_DREAMING_MAX_USERS_PER_RUN
        个用户；传入 (tenant_id, product, user_id) 限定单用户（手动重放/测试
        用——测试共享库上必须限定范围，不得对真实用户数据执行动作）。
        """
        from backend.observability.tracer import trace_collector

        if not MEMORY_DREAMING_ENABLED:
            return {"status": "skipped", "reason": "disabled"}

        trace = trace_collector.start(
            question="memory dreaming", session_id="",
            workflow_name="memory_dreaming",
        )
        span = trace_collector.start_span(
            "memory.dreaming", name="记忆整合", type="memory")
        started = datetime.now(timezone.utc)
        stats = {
            "users_scanned": 0, "clusters_examined": 0,
            "merged": 0, "contradictions": 0, "llm_calls": 0,
            "skipped_clusters": 0, "errors": 0,
        }
        try:
            if user_scope is not None:
                user_keys = [user_scope]
            else:
                user_keys = await self._iter_dreaming_users()
            for tenant_id, product, user_id in user_keys:
                stats["users_scanned"] += 1
                try:
                    await self._dream_user(
                        tenant_id, product, user_id, stats)
                except Exception as exc:  # 单用户失败不拖垮整批
                    stats["errors"] += 1
                    logger.warning(
                        "[Dreaming] 用户整合失败（跳过）user=%s: %s",
                        user_id, type(exc).__name__)
            trace_collector.end_span(span, metrics=dict(stats))
        except BaseException as exc:
            trace_collector.end_span(
                span, status="cancelled" if isinstance(exc, asyncio.CancelledError)
                else "error",
                metrics={"error_type": type(exc).__name__})
            raise
        finally:
            total_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            try:
                trace_collector.finish(
                    trace, answer="", total_ms=total_ms, model="", provider="")
            except Exception:  # 观测面异常不反噬任务
                pass
        logger.info("[Dreaming] 整合完成：%s", stats)
        return {"status": "succeeded", **stats}

    async def _iter_dreaming_users(self) -> list[tuple[str, str, str]]:
        """有 unkeyed active 碎片的用户集（最旧优先，护栏截断）。"""
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(
                    MemoryRecord.tenant_id, MemoryRecord.product,
                    MemoryRecord.user_id,
                    func.min(MemoryRecord.last_access_at).label("oldest"),
                )
                .where(
                    MemoryRecord.is_active.is_(True),
                    MemoryRecord.invalid_at.is_(None),
                    MemoryRecord.memory_key.is_(None),
                    MemoryRecord.verification_status.in_(("verified", "legacy")),
                )
                .group_by(
                    MemoryRecord.tenant_id, MemoryRecord.product,
                    MemoryRecord.user_id,
                )
                .order_by(func.min(MemoryRecord.last_access_at).asc())
                .limit(MEMORY_DREAMING_MAX_USERS_PER_RUN)
            )).all()
        return [(r[0], r[1], r[2]) for r in rows]

    # ── 单用户批处理 ──────────────────────────────────────────

    async def _dream_user(self, tenant_id: str, product: str,
                          user_id: str, stats: dict) -> None:
        from backend.memory.database import AsyncSessionLocal
        from backend.infra.llm.budget import scoped_request_budget
        from backend.core.request_context import scoped_tool_identity

        factory = self._repo_factory or AsyncSessionLocal
        async with factory() as db:
            repo = MemoryRepository(db)
            with (
                scoped_request_budget(
                    f"memory-dreaming:{tenant_id}:{user_id}",
                    user_id=user_id, tenant_id=tenant_id,
                    on_error=lambda exc: logger.warning(
                        "[Dreaming] 预算绑定失败（放行）user=%s error=%s",
                        user_id, type(exc).__name__),
                ),
                scoped_tool_identity(user_id=user_id, tenant_id=tenant_id),
            ):
                fragments = (await db.execute(
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.tenant_id == tenant_id,
                        MemoryRecord.product == product,
                        MemoryRecord.user_id == user_id,
                        MemoryRecord.is_active.is_(True),
                        MemoryRecord.invalid_at.is_(None),
                        MemoryRecord.memory_key.is_(None),
                        MemoryRecord.verification_status.in_(("verified", "legacy")),
                    )
                    .order_by(MemoryRecord.last_access_at.asc())
                    .limit(MEMORY_DREAMING_MAX_FRAGMENTS_PER_USER)
                )).scalars().all()

                # D-2 安全豁免：内容命中关键词的行整体排除
                eligible = [r for r in fragments if not _is_safe_content(r.content)]
                clusters = self._cluster(eligible)[:MEMORY_DREAMING_MAX_CLUSTERS_PER_USER]

                for cluster in clusters:
                    stats["clusters_examined"] += 1
                    # 簇内含 explicit → 整体跳过（用户显式资产不改写）
                    if any(r.origin == "explicit" for r in cluster):
                        stats["skipped_clusters"] += 1
                        continue
                    verdict = await self._classify_cluster(cluster, stats)
                    if verdict is None:
                        stats["skipped_clusters"] += 1
                        continue
                    action = verdict.get("action")
                    if action == "merge" and verdict.get("merged"):
                        await self._apply_merge(
                            repo, db, cluster, str(verdict["merged"]), stats)
                    elif action == "contradiction":
                        idx = verdict.get("superseded")
                        rows = [r for i, r in enumerate(cluster) if i in idx] \
                            if isinstance(idx, list) else []
                        for row in rows:
                            await repo.deactivate(str(row.id))
                        if rows:
                            await db.commit()
                            stats["contradictions"] += len(rows)
                            logger.info(
                                "[Dreaming] 矛盾失效 %d 行 (user=%s)",
                                len(rows), user_id)
                    else:
                        stats["skipped_clusters"] += 1

    # ── 簇构建 ────────────────────────────────────────────────

    @staticmethod
    def _cluster(records: list) -> list[list]:
        """同 memory_type + 两两余弦 ≥ 阈值的连通分量（简单图聚类）。"""
        by_type: dict[str, list] = {}
        for r in records:
            if r.embedding:
                by_type.setdefault(r.memory_type, []).append(r)
        clusters: list[list] = []
        for rows in by_type.values():
            n = len(rows)
            parent = list(range(n))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i in range(n):
                for j in range(i + 1, n):
                    if (_cosine(rows[i].embedding, rows[j].embedding)
                            >= MEMORY_DREAMING_MERGE_SIM_THRESHOLD):
                        pi, pj = find(i), find(j)
                        if pi != pj:
                            parent[max(pi, pj)] = min(pi, pj)
            groups: dict[int, list] = {}
            for i, r in enumerate(rows):
                groups.setdefault(find(i), []).append(r)
            clusters.extend(g for g in groups.values() if len(g) >= 2)
        return clusters

    # ── LLM 三分类 ────────────────────────────────────────────

    def _build_prompt(self, cluster: list) -> str:
        lines = "\n".join(
            f"{i}. {r.content}" for i, r in enumerate(cluster))
        return (
            "以下是与同一用户相关的多条已记录事实。判断它们的关系：\n"
            "- merge：多条表达的是同一件事（同义重复/碎片化表述），给出一条"
            "合并后的完整表述（只能使用已有信息，不得添加新事实）。\n"
            "- contradiction：其中某些条目互相矛盾（同一属性的不同取值），"
            "指出应失效的条目编号（通常保留更明确/更可信的一条）。\n"
            "- independent：相似但表达不同事实，保持独立。\n"
            "只输出 JSON：{\"action\": \"merge|contradiction|independent\", "
            "\"merged\": \"合并表述（仅 merge 时）\", \"superseded\": [失效条目"
            "编号（仅 contradiction 时）]}\n\n事实列表：\n" + lines
        )

    async def _call_llm(self, prompt: str) -> str:
        from backend.infra.llm import llm

        resp = await asyncio.to_thread(llm.invoke, prompt)
        return resp.content if hasattr(resp, "content") else str(resp)

    async def _classify_cluster(self, cluster: list, stats: dict) -> dict | None:
        """单簇三分类；LLM/解析失败返回 None（fail-safe 不动库）。"""
        stats["llm_calls"] += 1
        try:
            text = await self._call_llm(self._build_prompt(cluster))
            payload = json.loads(self._extract_json(text))
            if payload.get("action") not in ("merge", "contradiction", "independent"):
                return None
            if payload["action"] == "merge" and not payload.get("merged"):
                return None
            if payload["action"] == "contradiction":
                idx = payload.get("superseded")
                valid = isinstance(idx, list) and idx and all(
                    isinstance(i, int) and 0 <= i < len(cluster) for i in idx)
                if not valid:
                    return None
            return payload
        except Exception as exc:
            logger.debug("[Dreaming] 簇分类失败（跳过）: %s", type(exc).__name__)
            return None

    @staticmethod
    def _extract_json(text: str) -> str:
        """宽松提取首个 {...} 块（模型可能带围栏/前后缀）。"""
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no json object")
        return text[start:end + 1]

    # ── 动作落库 ──────────────────────────────────────────────

    async def _apply_merge(self, repo: MemoryRepository, db, cluster: list,
                           merged_text: str, stats: dict) -> None:
        """碎片 supersede 到聚合行（版本链可审计回滚）：只重组已有信息。"""
        from backend.memory.pii_filter import scan_and_sanitize
        from backend.memory.keying import memory_scope_domain

        sanitized = scan_and_sanitize(merged_text).sanitized.strip()
        if not sanitized:
            return
        base = cluster[0]
        scope, domain = memory_scope_domain(base.memory_key)
        merged = MemoryRecord(
            tenant_id=base.tenant_id,
            product=base.product,
            user_id=base.user_id,
            session_id="",
            memory_type=base.memory_type,
            content=sanitized,
            embedding=base.embedding,
            importance_score=max(r.importance_score for r in cluster),
            confidence_score=max(r.confidence_score for r in cluster),
            # 聚合是机器产物：origin 固定 inferred（写入通道决定，
            # 不继承碎片 explicit——含 explicit 的簇已在入口被跳过）
            origin="inferred",
            source_message_id=next(
                (r.source_message_id for r in reversed(cluster)
                 if r.source_message_id is not None), None),
            memory_key=None,
            structured_value=None,
            scope=scope,
            domain=domain,
            version=max(r.version for r in cluster) + 1,
            verification_status="pending",
            valid_from=min(r.valid_from for r in cluster
                           if r.valid_from is not None),
        )
        await repo.insert(merged)
        merged_id = str(merged.id)
        for fragment in cluster:
            await repo.deactivate(str(fragment.id))
            await repo.supersede(str(fragment.id), merged_id)
        await db.commit()
        stats["merged"] += 1
        logger.info(
            "[Dreaming] 合并 %d 碎片 → 1 聚合行 (user=%s)",
            len(cluster), base.user_id)
