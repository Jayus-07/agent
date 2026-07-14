"""
writers/sqlalchemy_writer.py — SQLAlchemy 统一写入器

通过 SQLAlchemy Engine 写入数据库，支持 PostgreSQL / MySQL。
只传入 DATABASE_URL 即可自动适配后端。

模式:
  - "append":  INSERT（主键冲突则跳过）
  - "replace": TRUNCATE + INSERT
  - "upsert":  INSERT ON CONFLICT DO NOTHING

用法:
    writer = SQLAlchemyWriter("postgresql://user:pass@localhost:5432/demo")
    result = writer.write(records, table="stg_products", mode="append")
"""

import time
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from data_collection.writers.base import AbstractWriter, WriteResult
from utils.logger import logger


class SQLAlchemyWriter(AbstractWriter):
    """统一数据库写入器

    DATABASE_URL 示例:
      - PostgreSQL: postgresql://user:pass@localhost:5432/demo
      - MySQL (Phase 2): mysql+pymysql://user:pass@localhost:3306/demo

    写入逻辑:
      1. 用 pandas.to_sql 做批量 insert
      2. upsert 模式用 PostgreSQL ON CONFLICT DO NOTHING
      3. replace 模式先 truncate 再 insert
    """

    def __init__(
        self,
        database_url: str,
        batch_size: int = 500,
        pool_size: int = 5,
    ):
        """
        Args:
            database_url: SQLAlchemy 连接字符串
            batch_size: 批量写入行数
            pool_size: 连接池大小
        """
        self._batch_size = batch_size
        self._engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            echo=False,
        )
        self._dialect = self._engine.dialect.name

    @property
    def dialect(self) -> str:
        """数据库后端名称: 'postgresql' | 'mysql'"""
        return self._dialect

    def write(
        self,
        records: list[dict[str, Any]],
        table: str,
        mode: str = "append",
        if_exists: str = "append",
    ) -> WriteResult:
        """将记录写入数据库

        Args:
            records: 待写入记录
            table: 目标表名
            mode: "append" | "replace" | "upsert"
            if_exists: pandas.to_sql 的 if_exists 参数

        Returns:
            WriteResult: 写入计数
        """
        if not records:
            logger.warning(f"[SQLAlchemyWriter] 无数据可写入 {table}")
            return WriteResult(table=table)

        started = time.perf_counter()
        errors: list[str] = []

        df = pd.DataFrame(records)
        inserted = 0

        try:
            if mode == "replace":
                self._do_replace(df, table)
                inserted = len(df)
            elif mode == "upsert" and self._dialect == "postgresql":
                inserted = self._do_upsert_pg(df, table)
            elif mode == "upsert":
                logger.info(f"[SQLAlchemyWriter] {self._dialect} 不支持原生 upsert，退化为 append")
                df.to_sql(table, self._engine, if_exists="append", index=False)
                inserted = len(df)
            else:
                df.to_sql(table, self._engine, if_exists=if_exists, index=False)
                inserted = len(df)

        except Exception as e:
            error_msg = f"写入 {table} 失败: {e}"
            logger.error(f"[SQLAlchemyWriter] {error_msg}")
            errors.append(error_msg)
            inserted = 0  # 写入失败，实际插入数为 0

        elapsed = (time.perf_counter() - started) * 1000

        logger.info(
            f"[SQLAlchemyWriter] {table}: "
            f"{len(records)} 条, mode={mode}, inserted={inserted}, {elapsed:.0f}ms"
        )

        return WriteResult(
            table=table,
            inserted=inserted,
            errors=errors,
            elapsed_ms=round(elapsed, 1),
        )

    def _do_replace(self, df: pd.DataFrame, table: str) -> None:
        """清空表后写入"""
        with self._engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {table}"))
        df.to_sql(table, self._engine, if_exists="append", index=False)
        logger.info(f"[SQLAlchemyWriter] replace: 清空 {table} → 写入 {len(df)} 条")

    def _do_upsert_pg(self, df: pd.DataFrame, table: str) -> int:
        """PostgreSQL 专用 upsert: INSERT ... ON CONFLICT DO NOTHING

        需要目标表有主键或唯一约束。
        无法自动推断约束时退化为 append。
        返回实际写入行数。
        """
        inspector = inspect(self._engine)
        pk_cols = inspector.get_pk_constraint(table).get("constrained_columns", [])

        if not pk_cols:
            logger.warning(
                f"[SQLAlchemyWriter] {table} 无主键，upsert 退化为 append"
            )
            df.to_sql(table, self._engine, if_exists="append", index=False)
            return len(df)

        # 分批写入
        total = len(df)
        for start in range(0, total, self._batch_size):
            batch = df.iloc[start : start + self._batch_size]
            records = batch.to_dict(orient="records")

            stmt = pg_insert(table).values(records).on_conflict_do_nothing()
            with self._engine.begin() as conn:
                conn.execute(stmt)

        logger.info(f"[SQLAlchemyWriter] upsert: {total} 条 → {table} (PK: {pk_cols})")
        return total

    def close(self) -> None:
        """释放连接池"""
        self._engine.dispose()
