"""EntityStore — PostgreSQL-backed entity index for non-vector entity lookups.

Phase 1: entity_index table only (name → doc_id + chunk_id mapping).
EntityRelation table deferred to Phase 2.

Supports: person, project, tech, system entity types.
"""

from dataclasses import dataclass

from utils.logger import logger


@dataclass
class EntityRecord:
    entity_name: str
    entity_type: str   # "person" | "project" | "tech" | "system"
    doc_id: str
    chunk_id: str
    frequency: int = 1


class EntityStore:
    """Synchronous EntityStore using the shared async engine.

    Callers inside the persistent event loop (MemoryManager) or asyncio.run()
    contexts are safe. For pure sync callers, wrap in asyncio.run().
    """

    def __init__(self):
        self._ready = False

    def _ensure_table(self) -> None:
        """Ensure entity_index table exists (idempotent)."""
        import asyncio
        try:
            asyncio.get_running_loop()
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(asyncio.run, self._async_ensure_table()).result(timeout=10)
                else:
                    asyncio.run(self._async_ensure_table())
            except RuntimeError:
                asyncio.run(self._async_ensure_table())
        except RuntimeError:
            asyncio.run(self._async_ensure_table())

    async def _async_ensure_table(self) -> None:
        from memory.database import async_engine
        async with async_engine.begin() as conn:
            await conn.execute(
                conn.exec_driver_sql(
                    """CREATE TABLE IF NOT EXISTS entity_index (
                        id          SERIAL PRIMARY KEY,
                        entity_name VARCHAR(256) NOT NULL,
                        entity_type VARCHAR(32)  NOT NULL,
                        doc_id      VARCHAR(256) NOT NULL,
                        chunk_id    VARCHAR(256) NOT NULL,
                        frequency   INT DEFAULT 1,
                        UNIQUE(entity_name, entity_type, chunk_id)
                    )"""
                )
            )
            await conn.execute(
                conn.exec_driver_sql(
                    """CREATE INDEX IF NOT EXISTS idx_entity_name
                       ON entity_index(entity_name, entity_type)"""
                )
            )
        self._ready = True
        logger.info("[EntityStore] entity_index table ready")

    def search(self, entity_name: str, entity_type: str | None = None) -> list[EntityRecord]:
        """Synchronous search. Call from any thread — uses asyncio.run() internally."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(
                        asyncio.run, self._async_search(entity_name, entity_type)
                    ).result(timeout=10)
            return asyncio.run(self._async_search(entity_name, entity_type))
        except RuntimeError:
            return asyncio.run(self._async_search(entity_name, entity_type))

    async def _async_search(self, entity_name: str, entity_type: str | None = None) -> list[EntityRecord]:
        from memory.database import async_engine
        from sqlalchemy import text

        async with async_engine.connect() as conn:
            if entity_type:
                result = await conn.execute(
                    text(
                        "SELECT entity_name, entity_type, doc_id, chunk_id, frequency "
                        "FROM entity_index "
                        "WHERE entity_name = :name AND entity_type = :type "
                        "ORDER BY frequency DESC LIMIT 100"
                    ),
                    {"name": entity_name, "type": entity_type},
                )
            else:
                result = await conn.execute(
                    text(
                        "SELECT entity_name, entity_type, doc_id, chunk_id, frequency "
                        "FROM entity_index "
                        "WHERE entity_name = :name "
                        "ORDER BY frequency DESC LIMIT 100"
                    ),
                    {"name": entity_name},
                )
            rows = result.fetchall()
            return [
                EntityRecord(
                    entity_name=r[0], entity_type=r[1],
                    doc_id=r[2], chunk_id=r[3], frequency=r[4],
                )
                for r in rows
            ]

    def get_chunk_ids_for_entity(self, entity_name: str) -> list[str]:
        """Return chunk IDs associated with an entity. Useful for MetadataFilter."""
        records = self.search(entity_name)
        return list({r.chunk_id for r in records})

    def build_from_metadata(self, doc_map: dict, doc_level_meta: list) -> int:
        """Build entity_index from existing pipeline metadata. Returns inserted count."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(
                        asyncio.run, self._async_build(doc_map, doc_level_meta)
                    ).result(timeout=60)
            return asyncio.run(self._async_build(doc_map, doc_level_meta))
        except RuntimeError:
            return asyncio.run(self._async_build(doc_map, doc_level_meta))

    async def _async_build(self, doc_map: dict, doc_level_meta: list) -> int:
        from memory.database import async_engine
        from sqlalchemy import text

        self._ensure_table()

        rows: list[dict] = []
        tech_keywords = {"redis", "mysql", "postgresql", "mongodb", "elasticsearch",
                         "kafka", "rabbitmq", "rocketmq", "docker", "kubernetes",
                         "springboot", "springcloud", "fastapi", "django", "flask",
                         "react", "vue", "netty", "dubbo", "grpc", "nginx", "jenkins"}

        for meta in doc_level_meta:
            doc_id = meta.get("doc_id", "")
            chunk_id = meta.get("chunk_id", doc_id)

            # Persons
            persons = meta.get("person_names", [])
            if isinstance(persons, str):
                persons = [persons]
            for p in persons:
                if p and p.strip():
                    rows.append({"entity_name": p.strip(), "entity_type": "person",
                                 "doc_id": doc_id, "chunk_id": chunk_id})

            # Keywords as tech stacks
            keywords = meta.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [keywords]
            for kw in keywords:
                if kw and kw.lower() in tech_keywords:
                    rows.append({"entity_name": kw, "entity_type": "tech",
                                 "doc_id": doc_id, "chunk_id": chunk_id})

        if not rows:
            return 0

        async with async_engine.begin() as conn:
            inserted = 0
            for r in rows:
                try:
                    await conn.execute(
                        text(
                            "INSERT INTO entity_index (entity_name, entity_type, doc_id, chunk_id, frequency) "
                            "VALUES (:name, :type, :doc_id, :chunk_id, 1) "
                            "ON CONFLICT (entity_name, entity_type, chunk_id) DO UPDATE SET frequency = entity_index.frequency + 1"
                        ),
                        r,
                    )
                    inserted += 1
                except Exception:
                    pass
            logger.info(f"[EntityStore] 构建完成: {inserted} 条实体索引")
            return inserted
