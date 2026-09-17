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

# RAG 数据根目录（所有 RAG 索引/文档路径统一由此派生，避免各模块硬编码相对路径）
RAG_DATA_DIR = os.getenv("RAG_DATA_DIR", os.path.join(_BACKEND_DIR, "data"))

# RAG 索引路径
BM25_INDEX_DIR = os.getenv("BM25_INDEX_DIR", os.path.join(RAG_DATA_DIR, "bm25"))
CHROMA_PATH = os.getenv("CHROMA_PATH", os.path.join(RAG_DATA_DIR, "chroma"))
DOC_DB_PATH = os.getenv("DOC_DB_PATH", os.path.join(RAG_DATA_DIR, "doc_db"))

DOCS_DIRECTORY = os.getenv("DOCS_DIRECTORY", os.path.join(RAG_DATA_DIR, "docs"))
DOC_REGISTRY_PATH = os.getenv("DOC_REGISTRY_PATH", os.path.join(RAG_DATA_DIR, "doc_registry.db"))
DOC_OPERATION_LOG_PATH = os.getenv("DOC_OPERATION_LOG_PATH", os.path.join(RAG_DATA_DIR, "doc_operation_log.db"))
CHUNK_STORE_PATH = os.getenv("CHUNK_STORE_PATH", os.path.join(RAG_DATA_DIR, "chunk_store.db"))
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

# === Doc registry 存储引擎开关（R1/C19：SQLite → PostgreSQL）===
# "sqlite"（默认，回滚开关）| "postgres"
# 切换 postgres 前，先跑 backend/scripts/migrate_doc_registry_to_pg.py 迁移历史数据。
DOC_REGISTRY_BACKEND = os.getenv("DOC_REGISTRY_BACKEND", "sqlite").strip().lower()
# PG 模式专用库名：默认 agent_memory（Agent 自身元数据库）。
# ⚠️ 不跟随 PGDATABASE（本地 .env 常把它指到 demo 等业务库）。
DOC_REGISTRY_PG_CONFIG = _pg_cfg("DOC_REGISTRY_PGDATABASE", "agent_memory")
# 表名可覆盖（测试隔离用）；生产保持默认 doc_registry。
DOC_REGISTRY_PG_TABLE = os.getenv("DOC_REGISTRY_PG_TABLE", "doc_registry")

# === 可观测层存储引擎开关（SQLite → PostgreSQL，2026-09-17 迁移计划 Batch A）===
# "sqlite"（默认，回滚开关）| "postgres"
# 作用于 trace_store / analytics(trace_summary) / llm_usage 三个存储（get_*_store 工厂分发）。
# 时间戳语义与 SQLite 版一致：trace_store/trace_summary 用 Python localtime 文本、
# llm_usage 用 UTC ISO 文本，均由应用侧生成后作参数写入（不依赖 PG 服务器时区）。
OBS_DB_BACKEND = os.getenv("OBS_DB_BACKEND", "sqlite").strip().lower()
OBS_DB_PG_CONFIG = _pg_cfg("OBS_DB_PGDATABASE", "agent_memory")
# 表名前缀（测试隔离用；生产保持空串 → trace_store / trace_summary / llm_usage）
OBS_DB_PG_TABLE_PREFIX = os.getenv("OBS_DB_PG_TABLE_PREFIX", "")

# === RAG 索引族存储引擎开关（迁移计划 2026-09-17 Batch B）===
# "sqlite"（默认，回滚开关）| "postgres"
# 作用于 chunk_store / keyword_rules / doc_operation_log（各自工厂分发）；
# doc_registry 已有独立开关 DOC_REGISTRY_BACKEND（R1/C19 先例）。
RAG_STORES_PG_CONFIG = _pg_cfg("RAG_STORES_PGDATABASE", "agent_memory")
# 表名前缀（测试隔离用；生产保持空串 → chunk_store / keyword_rules / doc_operation_log）
RAG_STORES_PG_TABLE_PREFIX = os.getenv("RAG_STORES_PG_TABLE_PREFIX", "")
CHUNK_STORE_BACKEND = os.getenv("CHUNK_STORE_BACKEND", "sqlite").strip().lower()
KEYWORD_STORE_BACKEND = os.getenv("KEYWORD_STORE_BACKEND", "sqlite").strip().lower()
OPLOG_BACKEND = os.getenv("OPLOG_BACKEND", "sqlite").strip().lower()

# === 编排族存储引擎开关（迁移计划 2026-09-17 Batch C）===
# "sqlite"（默认，回滚开关）| "postgres"
# 库归属：workflow_runs → agent_memory；inventory_alerts 4 表 → agent_business
WORKFLOW_DB_BACKEND = os.getenv("WORKFLOW_DB_BACKEND", "sqlite").strip().lower()
WORKFLOW_DB_PG_CONFIG = _pg_cfg("WORKFLOW_DB_PGDATABASE", "agent_memory")
INVENTORY_DB_BACKEND = os.getenv("INVENTORY_DB_BACKEND", "sqlite").strip().lower()
INVENTORY_DB_PG_CONFIG = _pg_cfg("INVENTORY_DB_PGDATABASE", "agent_business")

# === 业务族存储引擎开关（迁移计划 2026-09-17 Batch D，目标库 agent_business）===
# "sqlite"（默认，回滚开关）| "postgres"；各自 <STORE>_BACKEND 工厂分发
SELECTION_BACKEND = os.getenv("SELECTION_BACKEND", "sqlite").strip().lower()
SELECTION_PG_CONFIG = _pg_cfg("SELECTION_PGDATABASE", "agent_business")
SELECTION_DECISION_BACKEND = os.getenv("SELECTION_DECISION_BACKEND", "sqlite").strip().lower()
SELECTION_DECISION_PG_CONFIG = _pg_cfg("SELECTION_DECISION_PGDATABASE", "agent_business")
MARKET_RESEARCH_BACKEND = os.getenv("MARKET_RESEARCH_BACKEND", "sqlite").strip().lower()
MARKET_RESEARCH_PG_CONFIG = _pg_cfg("MARKET_RESEARCH_PGDATABASE", "agent_business")
COMPETITOR_BACKEND = os.getenv("COMPETITOR_BACKEND", "sqlite").strip().lower()
COMPETITOR_PG_CONFIG = _pg_cfg("COMPETITOR_PGDATABASE", "agent_business")
FEEDBACK_BACKEND = os.getenv("FEEDBACK_BACKEND", "sqlite").strip().lower()
FEEDBACK_PG_CONFIG = _pg_cfg("FEEDBACK_PGDATABASE", "agent_business")

# === 连接池参数 ===
DB_POOL_MIN_CONN = int(os.getenv("DB_POOL_MIN_CONN", "2"))
DB_POOL_MAX_CONN = int(os.getenv("DB_POOL_MAX_CONN", "10"))
DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "5"))
DB_KEEPALIVES_IDLE = int(os.getenv("DB_KEEPALIVES_IDLE", "30"))

# === 向后兼容：旧代码仍 import DB_CONFIG（指向 memory 库）===
# 注意：新代码应直接用 MEMORY_DB_CONFIG / BUSINESS_DB_CONFIG
DB_CONFIG = MEMORY_DB_CONFIG
