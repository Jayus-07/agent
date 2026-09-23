"""tests/memory/conftest.py — 记忆子系统共享测试工具。

只提供 helper（无 autouse 行为，不影响既有测试文件）：
- require_memory_pg()：权威库可达性 + 048 schema 前置检查（不满足则 skip）
- ScriptedEmbedding：可控向量的假 embedding（精确构造余弦相似度）
- cleanup_memory_prefix()：按 user/session 前缀清理测试数据

注意：本机双 PG——5432 是宿主机原生同名旧库，agent 权威库在 docker
5433；跑记忆真库测试须 PGPORT=5433（见 MEMORY.md 实测坑）。
"""
from __future__ import annotations

import hashlib

import numpy as np
import psycopg2
import pytest
from sqlalchemy import text

from backend.config.database import MEMORY_DB_CONFIG
from backend.memory.database import AsyncSessionLocal
from backend.memory.models.memory import EMBEDDING_DIM


def require_memory_pg() -> None:
    """权威库不可达或 048 列缺失时显式 skip（不静默假装通过）。"""
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='memory_records' "
                    "AND column_name IN ('origin','tenant_id','memory_key','structured_value')"
                )
                cols = {row[0] for row in cursor.fetchall()}
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过真实验收: {exc}")
    missing = {"origin", "tenant_id", "memory_key", "structured_value"} - cols
    if missing:
        pytest.skip(f"memory_records 缺列 {sorted(missing)}（047/048 未应用），跳过")


async def cleanup_memory_prefix(prefix: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM memory_records WHERE user_id LIKE :p"), {"p": prefix + "%"})
        await db.execute(
            text("DELETE FROM chat_sessions WHERE session_id LIKE :p"), {"p": prefix + "%"})
        await db.commit()


class ScriptedEmbedding:
    """脚本化假 embedding：预置向量精确可控，未注册文本回退伪随机正交向量。"""

    def __init__(self):
        self.vectors: dict[str, list[float]] = {}

    def unit(self, index: int) -> list[float]:
        vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        vec[index % EMBEDDING_DIM] = 1.0
        return vec.tolist()

    def blend(self, base: list[float], index: int, similarity: float) -> list[float]:
        """构造与 base 余弦相似度恰为 similarity 的单位向量。"""
        b = np.asarray(base, dtype=np.float32)
        ortho = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        ortho[index % EMBEDDING_DIM] = 1.0
        ortho -= ortho.dot(b) * b
        ortho /= np.linalg.norm(ortho)
        v = similarity * b + (1.0 - similarity**2) ** 0.5 * ortho
        return (v / np.linalg.norm(v)).astype(np.float32).tolist()

    def embed_query(self, text_value: str) -> list[float]:
        v = self.vectors.get(text_value)
        if v is not None:
            return v
        seed = int(hashlib.md5(text_value.encode("utf-8")).hexdigest(), 16)
        rng = np.random.default_rng(seed % (2**32))
        vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        return (vec / np.linalg.norm(vec)).tolist()
