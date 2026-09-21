"""config/competitor.py — 竞品爬取（backend/competitor）域配置

纪律：业务模块禁止直接 ``os.getenv``（AGENTS.md Code Rules）；
env 名与解析统一收口本模块。

- **getter（调用时求值）**：测试 ``monkeypatch.setenv`` 后立即生效，
  且 ``tests/fixtures/pg_env.py`` 通过「删 sys.modules 再重导 store 模块」
  换表前缀的机制不受 config 模块缓存影响。
- 若未来出现纯静态项（导入期即定格、无测试依赖），再以模块常量收口。
"""
import os

from dotenv import load_dotenv

load_dotenv()


def robots_override() -> str:
    """robots.txt 合规覆盖模式：``''``（正常拦截）| ``warn_only``（仅告警不拦截）。

    用户运维开关：根 .env 可能长期带 ``ROBOTS_OVERRIDE=warn_only``，
    测试拦截行为前必须先 ``monkeypatch.delenv`` 隔离。
    """
    return os.getenv("ROBOTS_OVERRIDE", "").strip()


def crawler_cookies_raw() -> str:
    """全局兜底 Cookie：store 无平台级/全局配置时的最后回退（可能为空）。"""
    return os.getenv("CRAWLER_COOKIES", "").strip()


def pg_table_prefix() -> str:
    """PG 存储表名前缀（多套部署共用一个库时隔离；store 模块导入期调用）。

    注意：不做 strip —— 与历史 ``os.getenv(...)`` 语义逐字一致。
    """
    return os.getenv("COMPETITOR_PG_TABLE_PREFIX", "")
