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


_REQUIRED_MEMORY_COLUMNS = frozenset(
    {"origin", "tenant_id", "memory_key", "structured_value"}
)

# 本次 pytest 会话内 schema 前置检查的结果缓存。
# 背景（2026-10-09 实测）：require_memory_pg() 原本每个用例都新建一条
# psycopg2 连接做 information_schema 查询，单次约 2.0s；配合用例级
# cleanup 的连接开销，memory 模块 93 例累计 315s，而用例本身只有毫秒级。
# 该检查验证的是"库可达 + migration 已应用"这类**会话期不变**的前置条件，
# 与具体用例无关，因此按会话缓存结果即可；失败原因照旧原样抛出，
# 不会把 skip 变成静默通过。
_schema_checked: bool = False
# 同上，按 "列名|类型" 缓存；供各测试文件的 _require_pg 复用。
_column_checked: set[str] = set()


def require_memory_pg() -> None:
    """权威库不可达或 048 列缺失时显式 skip（不静默假装通过）。

    结果按 pytest 会话缓存：schema 前置条件在一次运行内不会变化，
    重复探测只是把 ~2s/用例的连接开销白送给整个模块。
    """
    global _schema_checked
    if _schema_checked:
        return
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
    missing = _REQUIRED_MEMORY_COLUMNS - cols
    if missing:
        pytest.skip(f"memory_records 缺列 {sorted(missing)}（047/048 未应用），跳过")
    _schema_checked = True


def require_memory_column(column: str, *, udt: str | None = None, label: str = "") -> None:
    """按会话缓存地校验 memory_records 的某一列存在（可选校验其类型）。

    与 require_memory_pg() 同理：这是"迁移是否已应用"的会话期不变前置条件。
    各测试文件原先各自新建 psycopg2 连接重复探测（每次 ~2s），
    在用例数多的模块里是纯粹的固定开销。
    """
    probe = f"{column}|{udt or ''}"
    if probe in _column_checked:
        return
    try:
        with psycopg2.connect(**MEMORY_DB_CONFIG, connect_timeout=2) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name='memory_records' AND column_name=%s",
                    (column,),
                )
                row = cursor.fetchone()
    except Exception as exc:
        pytest.skip(f"agent_memory PostgreSQL 不可达，跳过真实验收: {exc}")
    if row is None:
        pytest.skip(f"memory_records.{column} 不存在（迁移未应用），跳过{label}")
    if udt is not None and row[0] != udt:
        pytest.skip(
            f"memory_records.{column} 尚为 {row[0]}（期望 {udt}，迁移未应用），跳过{label}"
        )
    _column_checked.add(probe)


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
