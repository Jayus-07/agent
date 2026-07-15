"""
data_collection/models.py — 采集结果 SQLAlchemy ORM 模型

表命名: stg_* (staging 层，即数据仓库的原始层)
与 memory/models/ 风格保持一致。
"""

from sqlalchemy import (
    Column, BigInteger, String, Text, Float, Integer,
    DateTime, UniqueConstraint, func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class StgProduct(Base):
    """采集商品上架表 — 每个数据源的原始商品记录"""
    __tablename__ = "stg_products"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    source = Column(String(256), nullable=False, comment="数据来源URL/路径")
    sku = Column(String(50), nullable=False, comment="SKU编码")
    名称 = Column(Text, comment="商品名称")
    品类 = Column(String(64), comment="品类")
    品牌 = Column(String(64), comment="品牌")
    售价 = Column(Float, comment="售价(元)")
    成本 = Column(Float, comment="成本(元)")
    平台 = Column(String(32), comment="销售平台")
    状态 = Column(String(16), comment="在售/停售")
    上架日期 = Column(String(32), comment="上架日期")
    collected_at = Column(DateTime, server_default=func.now(), comment="采集时间")

    __table_args__ = (
        UniqueConstraint("source", "sku", name="uq_stg_product_source_sku"),
        {"comment": "采集商品上架数据(staging层)"},
    )


class StgCollectionLog(Base):
    """采集任务日志表 — 记录每次 Pipeline 执行结果"""
    __tablename__ = "stg_collection_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    task_id = Column(String(64), index=True, nullable=False, comment="任务ID")
    source = Column(String(256), comment="数据源")
    target_table = Column(String(128), comment="目标表")
    status = Column(String(20), comment="success/partial/failed")
    rows_fetched = Column(Integer, comment="获取行数")
    rows_inserted = Column(Integer, comment="写入行数")
    rows_dedup = Column(Integer, comment="去重行数")
    elapsed_ms = Column(Float, comment="总耗时(毫秒)")
    error = Column(Text, comment="错误信息")
    started_at = Column(DateTime, server_default=func.now(), comment="开始时间")

    __table_args__ = (
        {"comment": "采集任务日志表"},
    )
