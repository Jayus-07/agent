"""data_collection/config.py — 向后兼容壳（2026-09-21 审查遗留项 3.3）

``DC_*`` 常量事实源已迁至 ``backend/config/data_collection.py``（env 读取
收口 config 层）。本壳逐名 re-export，既有 ``from backend.data_collection.config
import DC_*`` 不受影响；新代码请直接从 config 层导入。
"""
from backend.config.data_collection import (  # noqa: F401
    DC_ANALYSIS_ENABLED,
    DC_BATCH_SIZE,
    DC_DATABASE_URL,
    DC_DATA_DIR,
    DC_DEDUP_ENABLED,
    DC_DEFAULT_FETCHER,
    DC_HTTP_MAX_RETRIES,
    DC_HTTP_TIMEOUT,
    DC_HTTP_USER_AGENT,
    DC_MOCK_API_HOST,
    DC_MOCK_API_PORT,
)
