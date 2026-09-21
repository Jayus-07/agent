"""config/data_collection.py — Data Collection Center 模块配置

env 名与解析的唯一收口点（2026-09-21 审查遗留项 3.3：业务包内不得直接
``os.getenv``）。``backend/data_collection/config.py`` 保留为向后兼容壳，
新代码一律从这里导入 ``DC_*`` 常量。

所有配置可通过环境变量覆盖（导入期求值，与历史语义一致）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

# ── 数据源 ──
DC_DEFAULT_FETCHER = os.getenv("DC_DEFAULT_FETCHER", "static")
DC_DATA_DIR = os.getenv("DC_DATA_DIR", "")  # Phase 2: 自定义数据目录（当前 StaticDataFetcher 自动计算）

# ── HTTP 采集 ──
DC_HTTP_TIMEOUT = int(os.getenv("DC_HTTP_TIMEOUT", "30"))
DC_HTTP_USER_AGENT = os.getenv("DC_HTTP_USER_AGENT", "DataCollectionCenter/1.0")
DC_HTTP_MAX_RETRIES = int(os.getenv("DC_HTTP_MAX_RETRIES", "2"))

# ── Mock API ──
DC_MOCK_API_HOST = os.getenv("DC_MOCK_API_HOST", "localhost")
DC_MOCK_API_PORT = int(os.getenv("DC_MOCK_API_PORT", "8001"))

# ── 数据库 ──
DC_DATABASE_URL = os.getenv(
    "DC_DATABASE_URL",
    "postgresql://postgres@localhost:5432/demo",  # 密码从环境变量 PGPASSWORD 注入
)
DC_BATCH_SIZE = int(os.getenv("DC_BATCH_SIZE", "500"))

# ── 清洗 ──
DC_DEDUP_ENABLED = os.getenv("DC_DEDUP_ENABLED", "true").lower() == "true"  # Phase 2: 全局去重开关

# ── 分析 ──
DC_ANALYSIS_ENABLED = os.getenv("DC_ANALYSIS_ENABLED", "true").lower() == "true"
