"""MemoryRepository — async CRUD + pgvector hybrid search for memory_records

scope 契约（STOP C）：所有读写路径必须携带 (tenant_id, user_id) 双维度过滤，
不存在仅 user_id 的查询。tenant_id 在本仓储层入口统一
normalize_tenant_id（漏传归一 default 桶——隔离仍精确，绝不 fail-open 查全表）。
"""
import hashlib
from uuid import UUID, uuid4
from datetime import datetime, timezone
from sqlalchemy import and_, delete, func, or_, select, update, text, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession
from pgvector.sqlalchemy import Vector

from backend.memory.keying import normalize_memory_domain, normalize_tenant_id
from backend.memory.models.memory import EMBEDDING_DIM, MemoryRecord


def _query_vector(embedding: list[float]):
    """把 Python list 显式 coerce 成 pgvector Vector 类型。

    背景（2026-09-21 P1 修复）：裸 list 走 asyncpg 时被推断为 unknown，
    与 vector 列做 ``<=>`` 会报「vector <=> unknown」/「bytea <=> unknown」。
    type_coerce 走 Vector 类型的 bind processor，参数以 vector(1024) 参与
    运算（pgvector-python 官方 asyncpg 用法）。
    """
    return type_coerce(embedding, Vector(EMBEDDING_DIM))


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self._s = session

    async def insert(self, record: MemoryRecord) -> MemoryRecord:
        if record.id is None:
            record.id = uuid4()
        self._s.add(record)
        await self._s.flush()
        return record

    async def search_hybrid(
        self, embedding: list[float], user_id: str, top_k: int = 20,
        memory_type: str | None = None, tenant_id: str = "",
        domain: str | None = None,
    ) -> list[MemoryRecord]:
        """候选召回（SQL 层 hard eligibility，STOP D）。

        排序键：cosine similarity = 1 - cosine_distance（范围约 [0,1]，
        越高越相关——relevance gate 的 normalized 口径与此一致）。
        过滤条件（全部 SQL 层，过期行不占候选槽位，§21）：
          is_active AND tenant_id AND user_id
          AND (expire_at IS NULL OR expire_at > NOW())
        注意：本方法仅按相似度排序，不做 relevance gate 与 importance/
        recency 联合打分（职责在 Retriever，§72）。
        """
        tenant_id = normalize_tenant_id(tenant_id)
        query_vec = _query_vector(embedding)
        eligible_scope = or_(
            MemoryRecord.scope == "user_global",
            and_(
                MemoryRecord.scope == "user_domain",
                MemoryRecord.domain == (normalize_memory_domain(domain) or "general"),
            ),
        )
        query = select(
            MemoryRecord,
            (1.0 - (MemoryRecord.embedding.cosine_distance(query_vec))).label("similarity"),
        ).where(
            MemoryRecord.is_active == True,
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.user_id == user_id,
            MemoryRecord.verification_status.in_(("verified", "legacy")),
            eligible_scope,
            (MemoryRecord.expire_at.is_(None)) | (MemoryRecord.expire_at > text("NOW()")),
        )
        if memory_type:
            query = query.where(MemoryRecord.memory_type == memory_type)
        query = query.order_by(text("similarity DESC")).limit(top_k)

        result = await self._s.execute(query)
        # 返回 (record, similarity) 元组：relevance gate 需要逐条 semantic
        # score（§73：到 service 层不得丢失分数）
        return [(row[0], float(row[1])) for row in result.all()]

    async def find_similar_candidates(
        self, embedding: list[float], user_id: str, tenant_id: str,
        threshold: float = 0.85, top_n: int = 5,
    ) -> list[tuple[MemoryRecord, float]]:
        """unkeyed 语义去重候选：返回相似度 ≥ threshold 的 top-N (record, sim)。

        替代旧 find_similar(top-1)：调用方用最高相似度做 duplicate 判定
        （sim >= supersede 阈值 → DUPLICATE），**不做事实版本更新**——
        无 key 时无法区分「同属性新值」与「相似的不同事实」，
        semantic similarity 不再拥有 supersede 资格（§44-46）。
        """
        tenant_id = normalize_tenant_id(tenant_id)
        query_vec = _query_vector(embedding)
        query = (
            select(
                MemoryRecord,
                (1.0 - MemoryRecord.embedding.cosine_distance(query_vec)).label("sim"),
            )
            .where(
                MemoryRecord.is_active == True,
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
            )
            .order_by(text("sim DESC"))
            .limit(top_n)
        )
        result = await self._s.execute(query)
        return [(row[0], float(row[1])) for row in result.all() if float(row[1]) >= threshold]

    async def list_active_profile(
        self, user_id: str, tenant_id: str = "", limit: int | None = 50,
    ) -> list[MemoryRecord]:
        """画像展示：列出用户全部 eligible active 记忆（不走向量召回）。

        与 search_hybrid 的分工：画像页要「全量画像」而非「与问题相关的
        记忆」，故不做语义召回；eligibility 口径与 STOP D 一致（is_active
        + (tenant, user) 双维度 + 未过期），按重要度、最近访问排序。
        """
        tenant_id = normalize_tenant_id(tenant_id)
        query = (
            select(MemoryRecord)
            .where(
                MemoryRecord.is_active == True,
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
                MemoryRecord.verification_status.in_(("verified", "legacy")),
                (MemoryRecord.expire_at.is_(None)) | (MemoryRecord.expire_at > text("NOW()")),
            )
            .order_by(
                MemoryRecord.importance_score.desc(),
                MemoryRecord.last_access_at.desc(),
            )
        )
        if limit is not None:
            query = query.limit(max(1, min(int(limit), 200)))
        result = await self._s.execute(query)
        return list(result.scalars().all())

    async def list_pending_profile(
        self, user_id: str, tenant_id: str = "", limit: int = 200,
    ) -> list[MemoryRecord]:
        """列出本人待确认的自动推断候选，不供 Agent 上下文直接注入。"""
        query = (select(MemoryRecord).where(
            MemoryRecord.is_active.is_(True),
            MemoryRecord.tenant_id == normalize_tenant_id(tenant_id),
            MemoryRecord.user_id == user_id,
            MemoryRecord.verification_status == "pending",
            (MemoryRecord.expire_at.is_(None)) | (MemoryRecord.expire_at > text("NOW()")),
        ).order_by(MemoryRecord.created_at.desc()).limit(max(1, min(int(limit), 200))))
        result = await self._s.execute(query)
        return list(result.scalars().all())

    async def verify_pending_memory(
        self, user_id: str, tenant_id: str, record_id: str,
    ) -> bool:
        from backend.config import MEMORY_EXPLICIT_DEFAULT_CONFIDENCE

        try:
            parsed_id = UUID(str(record_id))
        except (TypeError, ValueError, AttributeError):
            return False
        result = await self._s.execute(update(MemoryRecord).where(
            MemoryRecord.id == parsed_id,
            MemoryRecord.tenant_id == normalize_tenant_id(tenant_id),
            MemoryRecord.user_id == user_id,
            MemoryRecord.is_active.is_(True),
            MemoryRecord.verification_status == "pending",
        ).values(
            verification_status="verified",
            origin="explicit",
            confidence_score=func.greatest(
                MemoryRecord.confidence_score, MEMORY_EXPLICIT_DEFAULT_CONFIDENCE,
            ),
        ))
        return bool(result.rowcount)

    async def lock_memory_identity(
        self, tenant_id: str, user_id: str, *, memory_key: str | None = None,
        content_hash: str | None = None,
    ) -> None:
        """串行化同一记忆的写入与删除，防止删除屏障竞态。"""
        identity = (f"key:{memory_key}" if memory_key else
                    f"content:{content_hash or ''}")
        lock_key = f"memory:{normalize_tenant_id(tenant_id)}:{user_id}:{identity}"
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )

    async def next_memory_version(
        self, tenant_id: str, user_id: str, *, scope: str,
        domain: str, memory_key: str,
    ) -> int:
        """在已持有同一 key advisory lock 时读取下一单调版本。"""
        result = await self._s.execute(select(
            func.coalesce(func.max(MemoryRecord.version), 0)
        ).where(
            MemoryRecord.tenant_id == normalize_tenant_id(tenant_id),
            MemoryRecord.user_id == user_id,
            MemoryRecord.scope == scope,
            MemoryRecord.domain == domain,
            MemoryRecord.memory_key == memory_key,
        ))
        return int(result.scalar_one()) + 1

    @staticmethod
    def stale_source_event(incoming_id: int | None,
                           existing_id: int | None) -> bool:
        """chat_messages.id 是服务端分配的全局事件序列；来源不明按旧事件挡住。"""
        return incoming_id is None or existing_id is None or incoming_id <= existing_id

    async def delete_tombstone_blocks(
        self, tenant_id: str, user_id: str, *,
        memory_key: str | None = None, content_hash: str | None = None,
        source_message_id: int | None = None, explicit: bool = False,
    ) -> bool:
        """判断候选是否来自删除前的事件；删除行中不保留原文或向量。"""
        tenant_id = normalize_tenant_id(tenant_id)
        query = select(MemoryRecord).where(
            MemoryRecord.memory_type == "tombstone",
            MemoryRecord.is_active.is_(False),
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.user_id == user_id,
        )
        if memory_key:
            # unkeyed tombstone 使用保留的 tombstone.content 标记，且其
            # structured_value 保存内容摘要；不可误判为同名 keyed 记忆。
            query = query.where(
                MemoryRecord.memory_key == memory_key,
                MemoryRecord.structured_value.is_(None),
            )
        else:
            query = query.where(
                MemoryRecord.memory_key == "tombstone.content",
                MemoryRecord.structured_value == content_hash,
            )
        tombstone = (await self._s.execute(
            query.order_by(MemoryRecord.created_at.desc()).limit(1)
            .with_for_update()
        )).scalar_one_or_none()
        if tombstone is None or explicit:
            return False
        if source_message_id is None:
            return True

        from backend.memory.models.session import ChatMessage
        source_created_at = (await self._s.execute(
            select(ChatMessage.created_at).where(ChatMessage.id == source_message_id)
        )).scalar_one_or_none()
        # 找不到来源时按旧事件处理，避免来源缺失绕过遗忘屏障。
        return source_created_at is None or source_created_at <= tombstone.created_at

    async def delete_profile_memory(
        self, user_id: str, tenant_id: str, *,
        record_id: str | None = None, memory_key: str | None = None,
    ) -> dict | None:
        """按本人/租户范围物理清除记忆正文与 Embedding，并写最小删除屏障。"""
        from backend.memory.keying import normalize_memory_key

        tenant_id = normalize_tenant_id(tenant_id)
        if bool(record_id) == bool(memory_key):
            raise ValueError("必须且只能提供 record_id 或 memory_key")
        query = select(MemoryRecord).where(
            MemoryRecord.is_active.is_(True),
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.user_id == user_id,
            (MemoryRecord.expire_at.is_(None))
            | (MemoryRecord.expire_at > text("NOW()")),
        )
        if memory_key:
            normalized_key = normalize_memory_key(memory_key)
            if normalized_key is None:
                return None
            query = query.where(MemoryRecord.memory_key == normalized_key)
        else:
            try:
                query = query.where(MemoryRecord.id == UUID(str(record_id)))
            except (TypeError, ValueError, AttributeError):
                return None
        record = (await self._s.execute(query.limit(1))).scalar_one_or_none()
        if record is None:
            return None

        content_hash = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
        await self.lock_memory_identity(
            tenant_id, user_id, memory_key=record.memory_key,
            content_hash=content_hash,
        )
        # 先拿 advisory lock 再锁行，避免与写入方反序等待造成死锁。
        record = (await self._s.execute(query.limit(1).with_for_update())) \
            .scalar_one_or_none()
        if record is None:
            return None
        record_id = str(record.id)
        record_key = record.memory_key
        record_content = record.content
        record_scope = record.scope
        record_domain = record.domain
        deleted_version = int(record.version or 1) + 1
        content_hash = hashlib.sha256(record_content.encode("utf-8")).hexdigest()
        tombstone_key = record_key or "tombstone.content"
        tombstone_value = None if record_key else content_hash
        if record_key:
            # 版本链使用自引用外键；先清空本 scope/key 的链指针，才能
            # 在同一事务内物理删除旧版本，避免 FK 阻止删除。
            await self._s.execute(update(MemoryRecord).where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
                MemoryRecord.memory_key == record_key,
                MemoryRecord.memory_type != "tombstone",
            ).values(superseded_by=None))
            deleted = await self._s.execute(delete(MemoryRecord).where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
                MemoryRecord.memory_key == record_key,
                MemoryRecord.memory_type != "tombstone",
            ))
        else:
            deleted = await self._s.execute(delete(MemoryRecord).where(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
                MemoryRecord.memory_key.is_(None),
                MemoryRecord.content == record_content,
                MemoryRecord.memory_type != "tombstone",
            ))

        tombstone = MemoryRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id="",
            memory_type="tombstone",
            content="",
            embedding=None,
            importance_score=0.0,
            confidence_score=0.0,
            origin="legacy",
            memory_key=tombstone_key,
            structured_value=tombstone_value,
            scope=record_scope,
            domain=record_domain,
            version=deleted_version,
            verification_status="deleted",
            is_active=False,
            expire_at=None,
        )
        await self.insert(tombstone)
        return {
            "memory_id": record_id,
            "memory_key": record_key,
            "deleted_records": int(deleted.rowcount or 0),
        }

    async def find_active_by_key(
        self, tenant_id: str, user_id: str, memory_key: str, *,
        scope: str | None = None, domain: str | None = None,
        for_update: bool = False,
    ) -> MemoryRecord | None:
        """按 (tenant, user, memory_key) 精确定位唯一 active 记录。

        keyed 事实版本管理的唯一入口——不再依赖 embedding 相似度找旧版本
        （top-1 blind spot 对 keyed 路径彻底消失）。for_update=True 时加
        行锁（SELECT ... FOR UPDATE），供原子 supersede 防并发双写。
        """
        tenant_id = normalize_tenant_id(tenant_id)
        query = (
            select(MemoryRecord)
            .where(
                MemoryRecord.is_active == True,
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.user_id == user_id,
                MemoryRecord.memory_key == memory_key,
            )
            .limit(1)
        )
        if scope is not None:
            query = query.where(MemoryRecord.scope == scope)
        if domain is not None:
            query = query.where(MemoryRecord.domain == domain)
        if for_update:
            query = query.with_for_update()
        result = await self._s.execute(query)
        return result.scalar_one_or_none()

    async def supersede(self, old_id: str, new_id: str) -> bool:
        """回填版本链指针（atomic supersede 第 4 步）。

        只按 id 回填：调用方已先 deactivate（is_active=False），此处若再带
        is_active=True 条件会永远 0 行——旧实现靠「insert 前不 deactivate」
        规避，原子化后顺序变了，条件必须跟着改。
        """
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == old_id)
            .values(is_active=False, superseded_by=new_id)
        )
        return result.rowcount > 0

    async def deactivate(self, old_id: str) -> bool:
        """原子 supersede 第 2 步：先置 inactive（superseded_by 等 new 落库后回填）。"""
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == old_id, MemoryRecord.is_active == True)
            .values(is_active=False)
        )
        return result.rowcount > 0

    async def mark_accessed(self, ids: list[str], tenant_id: str = "",
                            user_id: str = "") -> None:
        """批量更新访问状态（单 SQL，§27）——只有真正注入模型 / 工具返回的
        记忆才调用（§25/§26：候选与被拒者不计访问）。

        scope 契约（§28）：UPDATE 再带 tenant_id+user_id 过滤，不单靠 id。
        """
        if not ids:
            return
        await self._s.execute(
            update(MemoryRecord)
            .where(
                MemoryRecord.id.in_(ids),
                MemoryRecord.tenant_id == normalize_tenant_id(tenant_id),
                MemoryRecord.user_id == user_id,
                MemoryRecord.is_active == True,
            )
            .values(access_count=MemoryRecord.access_count + 1,
                    last_access_at=datetime.now(timezone.utc))
        )

    async def update_fields(self, record_id: str, **fields) -> bool:
        result = await self._s.execute(
            update(MemoryRecord).where(MemoryRecord.id == record_id).values(**fields)
        )
        return result.rowcount > 0

    async def apply_decay(self, days: int, factor: float,
                          upper_days: int | None = None) -> int:
        """衰减 [days, upper_days) 天未访问的 active 非 explicit 记录。

        upper_days 构成互斥区间（90~180 → 0.95，>180 → 0.9）——旧实现两档
        级联（>180 天被 ×0.9×0.95 双重衰减），接线时修正为互斥语义。
        """
        clause = "last_access_at < NOW() - (:days || ' days')::INTERVAL"
        if upper_days is not None:
            clause += " AND last_access_at >= NOW() - (:upper || ' days')::INTERVAL"
        result = await self._s.execute(
            text(f"""
                UPDATE memory_records
                SET importance_score = importance_score * :factor
                WHERE is_active = TRUE
                  AND origin <> 'explicit'
                  AND {clause}
            """),
            {"factor": factor, "days": str(days),
             **({"upper": str(upper_days)} if upper_days is not None else {})},
        )
        return result.rowcount

    async def archive_stale(self, min_importance: float = 0.2) -> int:
        result = await self._s.execute(
            update(MemoryRecord)
            .where(MemoryRecord.is_active == True,
                   MemoryRecord.importance_score < min_importance,
                   MemoryRecord.origin != "explicit")
            .values(is_active=False)
        )
        return result.rowcount
