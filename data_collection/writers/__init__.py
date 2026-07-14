"""数据写入层 — 将清洗后数据写入 PostgreSQL/MySQL"""
from data_collection.writers.base import AbstractWriter, WriteResult
from data_collection.writers.sqlalchemy_writer import SQLAlchemyWriter

__all__ = ["AbstractWriter", "WriteResult", "SQLAlchemyWriter"]
