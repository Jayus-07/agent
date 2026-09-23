"""MemoryRepository — async CRUD + pgvector hybrid search for memory_records

scope 契约（STOP C）：所有读写路径必须携带 (tenant_id, user_id) 双维度过滤，
不存在仅 user_id 的查询。tenant_id 在本仓储层入口统一
normalize_tenant_id（漏传归一 default 桶——隔离仍精确，绝不 fail-open 查全表）。
"""
from uuid import uuid4
from datetime import datetime, timezone
from sqlalchemy import select, update, text, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession
from pgvector.sqlalchemy import Vector

from backend.memory.keying import normalize_tenant_id
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
        query = select(
            MemoryRecord,
            (1.0 - (MemoryRecord.embedding.cosine_distance(query_vec))).label("similarity"),
        ).where(
            MemoryRecord.is_active == True,
            MemoryRecord.tenant_id == tenant_id,
            MemoryRecord.user_id == user_id,
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

    async def find_active_by_key(
        self, tenant_id: str, user_id: str, memory_key: str, *, for_update: bool = False,
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
