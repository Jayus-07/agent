"""config/database.py — 数据路径 + PostgreSQL 多库连接

设计：两库分离
  - MEMORY_DB_CONFIG  → agent_memory  (Agent 自身 metadata + 用户聊天/记忆)
  - BUSINESS_DB_CONFIG → agent_business (跨境电商业务数据仓库，含多业务 schema)

迁移背景：
  - 原 DB_CONFIG 单库承载 memory + 业务 schema，已重构成两库 (2026-08)
  - DB_CONFIG 仍保留作为向后兼容别名（指向 memory 库）
"""
import os

from dotenv import load_dotenv

load_dotenv()

# 基础路径（基于本文件位置，消除 CWD 依赖）
_CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_CONFIG_DIR)  # agent/backend/

# RAG 索引路径
BM25_INDEX_DIR = os.getenv("BM25_INDEX_DIR", os.path.join(_BACKEND_DIR, "data", "bm25"))
CHROMA_PATH = os.getenv("CHROMA_PATH", os.path.join(_BACKEND_DIR, "data", "chroma"))
DOC_DB_PATH = os.getenv("DOC_DB_PATH", os.path.join(_BACKEND_DIR, "data", "doc_db"))

DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", os.path.join(_BACKEND_DIR, "data", "docs"))
DOC_REGISTRY_PATH = os.getenv("DOC_REGISTRY_PATH", os.path.join(_BACKEND_DIR, "data", "doc_registry.db"))
DOC_OPERATION_LOG_PATH = os.getenv("DOC_OPERATION_LOG_PATH", os.path.join(_BACKEND_DIR, "data", "doc_operation_log.db"))
ENABLE_INCREMENTAL_INDEX = os.getenv("ENABLE_INCREMENTAL_INDEX", "true").lower() == "true"


def _pg_cfg(db_env_var: str, default_db: str) -> dict:
    """构造一个 PostgreSQL 连接配置（dict）。

    支持共用 PGPORT / PGHOST / PGUSER / PGPASSWORD，但 dbname 独立：
      - BUSINESS_PGDATABASE / PGDATABASE 环境变量
    """
    return {
        "host":     os.getenv("PGHOST", "localhost"),
        "port":     int(os.getenv("PGPORT", "5432")),
        "dbname":   os.getenv(db_env_var, default_db),
        "user":     os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD", ""),
    }


# === Memory 库（Agent 自身 + 聊天记忆 + 长期事实）===
MEMORY_DB_CONFIG = _pg_cfg("PGDATABASE", "agent_memory")

# === Business 库（业务数据仓库：跨境电商 7 业务 schema）===
BUSINESS_DB_CONFIG = _pg_cfg("BUSINESS_PGDATABASE", "agent_business")

# === Business 库只读连接（生产 P0：agent_readonly 角色）===
BUSINESS_DB_READONLY_CONFIG = {
    **_pg_cfg("BUSINESS_PGDATABASE", "agent_business"),
    "user": os.getenv("PG_READONLY_USER", "agent_readonly"),
    "password": os.getenv("PG_READONLY_PASSWORD", "agent_readonly_dev"),
}

# === 连接池参数 ===
DB_POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN_CONN", "2"))
DB_POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", "10"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
DB_KEEPALIVES_IDLE = int(os.getenv("DB_KEEPALIVES_IDLE", "30"))

# === 向后兼容：旧代码仍 import DB_CONFIG（指向 memory 库）===
# 注意：新代码应直接用 MEMORY_DB_CONFIG / BUSINESS_DB_CONFIG
DB_CONFIG = MEMORY_DB_CONFIG
