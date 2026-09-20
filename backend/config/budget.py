"""预算与价格表配置；金额策略本身只存 PostgreSQL，不从环境变量读取。"""
import os


BUDGET_CURRENCY = "USD"
BUDGET_TIMEZONE = os.getenv("BUDGET_TIMEZONE", "Asia/Shanghai")
BUDGET_PG_TABLE_PREFIX = os.getenv("BUDGET_PG_TABLE_PREFIX", "")
BUDGET_PLATFORM_DEFAULT_TENANT_ID = os.getenv(
    "BUDGET_PLATFORM_DEFAULT_TENANT_ID", "platform-default"
)
BUDGET_INTERNAL_TENANT_ID = os.getenv("BUDGET_INTERNAL_TENANT_ID", "internal")
BUDGET_TEST_TENANT_PREFIX = os.getenv("BUDGET_TEST_TENANT_PREFIX", "test")
