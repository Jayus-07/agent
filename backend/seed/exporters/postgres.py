"""PostgresExporter — 将生成数据写入 PostgreSQL（兼容 sql_agent schema_loader）。"""

from __future__ import annotations

import asyncio
from typing import Any

from backend.seed.core.context import GenerationContext


class PostgresExporter:
    """将 Context 中所有实体导出到 PostgreSQL。

    设计思路:
    1. 使用 memory/database.py 的 async engine 连接
    2. 为每个实体类型自动建表（CREATE TABLE IF NOT EXISTS）
    3. 批量 INSERT
    4. 调用 schema_loader.register_table() 注册元数据（SQL Agent 可直接查询）

    用法:
        exporter = PostgresExporter()
        await exporter.export(ctx)
    """

    def __init__(self):
        self._tables_created: set[str] = set()

    async def export(self, ctx: GenerationContext) -> None:
        """异步导出所有实体到 PostgreSQL。"""
        try:
            from backend.memory.database import get_engine
            engine = get_engine()
        except ImportError:
            print("[WARN] memory.database 不可用，跳过 PostgreSQL 导出")
            return
        except Exception as e:
            print(f"[WARN] 数据库连接失败: {e}，跳过 PostgreSQL 导出")
            return

        import asyncpg
        try:
            conn: asyncpg.Connection = await engine.acquire()
        except Exception as e:
            print(f"[WARN] 无法获取数据库连接: {e}")
            return

        try:
            for entity_name in ctx.entity_names:
                entities = ctx.get_entities(entity_name)
                if not entities:
                    continue
                await self._create_and_insert(conn, entity_name, entities)

            await self._register_schemas(ctx)
            print(f"  OK 已写入 PostgreSQL ({len(ctx.entity_names)} 个表)")

        finally:
            await engine.release(conn)

    async def _create_and_insert(self, conn, table_name: str,
                                 entities: list[dict]) -> None:
        """自动建表 + 批量插入。"""
        if not entities:
            return

        # 从第一条实体推断列类型
        columns = self._infer_columns(entities[0])

        # 建表
        col_defs = []
        for col_name, col_type in columns.items():
            col_name_safe = col_name.replace("-", "_")
            col_defs.append(f"{col_name_safe} {col_type}")

        sql_create = (
            f"CREATE TABLE IF NOT EXISTS seed_{table_name} (\n"
            f"  {', '.join(col_defs)}\n"
            f")"
        )
        await conn.execute(sql_create)

        # 批量插入（简单的 INSERT）
        col_names = [c.replace("-", "_") for c in columns.keys()]
        placeholders = ", ".join([f"${i + 1}" for i in range(len(col_names))])

        sql_insert = (
            f"INSERT INTO seed_{table_name} ({', '.join(col_names)}) "
            f"VALUES ({placeholders})"
        )

        for entity in entities:
            values = []
            for key in columns.keys():
                val = entity.get(key)
                # 将 list/dict 序列化为 JSON 字符串
                if isinstance(val, (list, dict)):
                    import json
                    val = json.dumps(val, ensure_ascii=False)
                values.append(val)
            try:
                await conn.execute(sql_insert, *values)
            except Exception:
                # 单条失败不中断
                pass

    def _infer_columns(self, sample: dict) -> dict[str, str]:
        """从样本实体推断列类型映射。"""
        columns = {}
        for key, value in sample.items():
            key_safe = key  # 将在 SQL 中使用时做替换
            if isinstance(value, bool):
                columns[key_safe] = "BOOLEAN"
            elif isinstance(value, int):
                columns[key_safe] = "INTEGER"
            elif isinstance(value, float):
                columns[key_safe] = "DOUBLE PRECISION"
            elif isinstance(value, (list, dict)):
                columns[key_safe] = "JSONB"
            else:
                columns[key_safe] = "TEXT"
        return columns

    async def _register_schemas(self, ctx: GenerationContext) -> None:
        """向 schema_loader 注册所有表。"""
        try:
            from backend.sql.schema_loader import schema_loader

            for entity_name in ctx.entity_names:
                entities = ctx.get_entities(entity_name)
                if not entities:
                    continue
                columns = self._infer_columns(entities[0])
                col_descs = {k: f"{entity_name} {k}" for k in columns.keys()}
                schema_loader.register_table(
                    f"seed_{entity_name}",
                    col_descs,
                    f"Seed data: {entity_name}"
                )
        except ImportError:
            pass  # sql_agent 不可用时跳过
        except Exception:
            pass

    # 同步包装
    def export_sync(self, ctx: GenerationContext) -> None:
        """同步导出（内部调用 asyncio.run）。"""
        try:
            asyncio.run(self.export(ctx))
        except RuntimeError:
            # 已有 event loop 在运行
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.export(ctx))
