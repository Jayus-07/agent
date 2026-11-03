"""PG 表名标识符白名单校验（audit-remediation-r1 P3/A2）。

DDL 中的表名/索引名是不可参数化的标识符。多个 PG store 用
``*_TABLE_PREFIX`` 环境变量拼表名后 f-string 插入 DDL——前缀属运维
输入而非攻击者直接可控，但按纵深防御与扫描分诊结论，统一在此
fail-closed 校验，口径对齐既有先例：

- ``infra/llm/price_page_monitor.py``：前缀 fullmatch + ValueError
- ``rag/indexing/operation_log_pg.py``：registry_table fullmatch 白名单

注意：仅支持纯标识符（不含 schema 限定点号）。带点前缀下既有索引
命名 ``idx_{table}_*`` 本就产生非法标识符，本约束不改变可用行为。
"""

from __future__ import annotations

import os
import re

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def pg_table_name(env_key: str, suffix: str) -> str:
    """组合环境变量前缀与固定后缀为 PG 表名，非法即抛 ValueError。

    校验对象是最终进入 DDL 的完整标识符：前缀含引号/分号/空白/点号
    等非标识符字符时 fail-closed，而不是到 execute 阶段才暴露。
    """
    name = os.getenv(env_key, "") + suffix
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"{env_key} 拼出的表名不是合法标识符: {name!r}")
    return name
