from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
from sqlalchemy.exc import SQLAlchemyError

from backend.infra.db import get_memory_engine


class TravelV2PersistenceError(RuntimeError):
    """V2 PostgreSQL 不可用或事务提交失败。"""


@contextmanager
def memory_connection() -> Iterator[object]:
    """严格事务边界：失败回滚，提交失败不返回保存成功。"""
    try:
        connection = get_memory_engine().raw_connection()
    except (psycopg2.Error, SQLAlchemyError) as exc:
        raise TravelV2PersistenceError("旅游 V2 数据库暂不可用") from exc
    try:
        yield connection
        connection.commit()
    except (psycopg2.Error, SQLAlchemyError) as exc:
        connection.rollback()
        raise TravelV2PersistenceError("旅游 V2 数据库事务失败") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
